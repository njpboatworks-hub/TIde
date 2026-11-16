"""Coastal airport weather stations (ASOS/AWOS) from the Iowa Environmental Mesonet (IEM).

Adds shoreline wind points the NOAA buoy network does not cover. Keeps airports within ~3 km of
open water (using data/coast_raster.json), downloads 2019-2024 hourly wind, and:
  * appends their typical-wind climatology to data/coast_stations.json (kind = "airport")
  * caches hourly series in data/asos/<id>.npz for scripts.coast_direction_effects

Usage: python -m scripts.coast_asos
"""
from __future__ import annotations

import base64
import io
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from backend.config import DATA_DIR

STATES = ["ME", "NH", "MA", "RI", "CT", "NY", "NJ", "DE", "MD", "VA", "NC", "SC", "GA", "FL", "AL", "MS", "LA", "TX"]
IEM = "https://mesonet.agron.iastate.edu"
CACHE = DATA_DIR / "asos"


def get(url: str, as_json=True):
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wind-analytics-prototype"})
            with urllib.request.urlopen(req, timeout=300) as r:
                b = r.read()
            return json.loads(b) if as_json else b.decode("utf-8", "ignore")
        except Exception:
            time.sleep(3 * (attempt + 1))
    return None


def load_raster():
    g = json.loads((DATA_DIR / "coast_raster.json").read_text())
    bits = np.unpackbits(np.frombuffer(base64.b64decode(g["bits"]), dtype=np.uint8))[: g["nx"] * g["ny"]]
    g["cells"] = bits.reshape(g["ny"], g["nx"]).astype(bool)
    return g


def near_water(g, lat, lon, km=3.0):
    r = int(np.ceil(km / (g["res"] * 111.32)))
    i, j = int((lon - g["lon0"]) / g["res"]), int((lat - g["lat0"]) / g["res"])
    if not (r <= i < g["nx"] - r and r <= j < g["ny"] - r):
        return False
    win = g["cells"][j - r: j + r + 1, i - r: i + r + 1]
    return (~win).sum() >= 3  # a few water cells nearby, not a single pond pixel


def on_east_coast(lat, lon):
    from scripts.region import in_region
    return in_region(lat, lon)


def candidates():
    g = load_raster()
    out = []
    for st in STATES:
        d = get(f"{IEM}/geojson/network/{st}_ASOS.geojson")
        for f in (d or {}).get("features", []):
            lon, lat = f["geometry"]["coordinates"]
            p = f["properties"]
            if on_east_coast(lat, lon) and near_water(g, lat, lon):
                out.append({"id": "asos_" + p["sid"], "sid": p["sid"], "name": p.get("sname", p["sid"]).title() + " (airport)",
                            "lat": round(lat, 4), "lon": round(lon, 4), "kind": "airport"})
    return out


def hourly(st) -> pd.DataFrame | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{st['id']}.npz"
    if f.exists():
        z = np.load(f)
        return pd.DataFrame({"u": z["u"], "v": z["v"], "s": z["s"]}, index=pd.to_datetime(z["t"], utc=True))
    url = (f"{IEM}/cgi-bin/request/asos.py?station={st['sid']}&data=drct&data=sknt&year1=2019&month1=1&day1=1"
           f"&year2=2025&month2=1&day2=1&tz=Etc/UTC&format=onlycomma&latlon=no&missing=null&trace=null&report_type=3")
    txt = get(url, as_json=False)
    if not txt or "valid" not in txt[:200]:
        return None
    df = pd.read_csv(io.StringIO(txt), na_values=["null", "M"])
    df = df.dropna(subset=["drct", "sknt"])
    if df.empty:
        return None
    t = pd.to_datetime(df["valid"], utc=True)
    r = np.deg2rad(df["drct"].astype(float).values)
    s = df["sknt"].astype(float).values
    h = pd.DataFrame({"u": np.sin(r) * s, "v": np.cos(r) * s, "s": s}, index=t).resample("1h").mean().dropna()
    np.savez_compressed(f, t=h.index.values.astype("datetime64[ns]").astype(np.int64), u=h["u"].values, v=h["v"].values, s=h["s"].values)
    return h


def climatology(st):
    h = hourly(st)
    if h is None or len(h) < 24 * 200:
        return None
    loc = h.index.tz_convert("America/New_York")
    h = h.assign(mo=loc.month, hr=loc.hour)
    g = h.groupby(["mo", "hr"])
    mu, mv, ms, med, n = g["u"].mean(), g["v"].mean(), g["s"].mean(), g["s"].median(), g["s"].size()
    D = [[None] * 24 for _ in range(12)]; S = [[None] * 24 for _ in range(12)]; T = [[None] * 24 for _ in range(12)]
    for (mo, hr), cnt in n.items():
        if cnt < 30:
            continue
        u, v = mu[(mo, hr)], mv[(mo, hr)]
        D[mo - 1][hr] = int(round((np.degrees(np.arctan2(u, v)) + 360) % 360))
        S[mo - 1][hr] = round(float(med[(mo, hr)]), 1)
        T[mo - 1][hr] = round(float(np.hypot(u, v) / ms[(mo, hr)]), 2) if ms[(mo, hr)] > 0 else 0
    if sum(x is not None for row in D for x in row) < 144:
        return None
    return {k: st[k] for k in ("id", "name", "lat", "lon", "kind")} | {"dir": D, "spd": S, "steady": T}


def main():
    cands = candidates()
    print(f"{len(cands)} coastal airports within ~3 km of open water", flush=True)
    path = DATA_DIR / "coast_stations.json"
    data = json.loads(path.read_text())
    data["stations"] = [s for s in data["stations"] if s.get("kind") != "airport"]
    added = 0
    with ThreadPoolExecutor(max_workers=3) as ex:  # be gentle with IEM
        for i, res in enumerate(ex.map(climatology, cands)):
            if res:
                data["stations"].append(res); added += 1
            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{len(cands)} processed, {added} usable", flush=True)
    path.write_text(json.dumps(data, separators=(",", ":")))
    print(f"added {added} airports; {len(data['stations'])} stations total")


if __name__ == "__main__":
    main()
