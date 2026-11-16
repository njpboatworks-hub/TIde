"""Latest observations and the National Weather Service hourly wind forecast for the East Coast.

  obs: latest reading at every East Coast NOAA NDBC station (latest_obs.txt) and coastal
       airport (IEM current observations), with its age
  fc:  NWS gridded forecast (api.weather.gov, ~2.5 km grid) of wind direction and speed, hourly
       for ~7 days, at every station plus open-water points every 0.25 degrees near the coast

The snapshot is only as current as its last run; the page shows the time it was made.
Usage: python -m scripts.live_snapshot
Writes site/live.json. Caches the NWS point-to-grid lookup in data/nws_points.json.
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np

from backend.config import DATA_DIR, ROOT
from scripts.coast_asos import STATES, load_raster, near_water, on_east_coast

OUT = ROOT / "site" / "live.json"
PTS_CACHE = DATA_DIR / "nws_points.json"
UA = {"User-Agent": "wind-analytics-prototype", "Accept": "application/geo+json"}
KMH_TO_KT = 0.539957
HOURS = 168


def get(url, as_json=True, tries=4):
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                b = r.read()
            return json.loads(b) if as_json else b.decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            time.sleep(2 * (attempt + 1))
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None


def latest_obs():
    now = datetime.now(timezone.utc)
    out = []
    txt = get("https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt", as_json=False) or ""
    for line in txt.splitlines():
        if line.startswith("#"):
            continue
        f = line.split()
        if len(f) < 11:
            continue
        try:
            lat, lon = float(f[1]), float(f[2])
        except ValueError:
            continue
        if not on_east_coast(lat, lon) or f[8] == "MM" or f[9] == "MM":
            continue
        t = datetime(int(f[3]), int(f[4]), int(f[5]), int(f[6]), int(f[7]), tzinfo=timezone.utc)
        if now - t > timedelta(hours=3):
            continue
        out.append({"id": f[0].lower(), "lat": lat, "lon": lon, "dir": int(f[8]), "spd": round(float(f[9]) * 1.943844, 1),
                    "gust": None if f[10] == "MM" else round(float(f[10]) * 1.943844, 1), "time": t.isoformat(), "kind": "buoy"})
    g = load_raster()  # keep only airports within ~3 km of open water, as in the history data
    for st in STATES:
        d = get(f"https://mesonet.agron.iastate.edu/api/1/currents.json?network={st}_ASOS")
        for r in (d or {}).get("data", []):
            lat, lon = r.get("lat"), r.get("lon")
            if lat is None or not on_east_coast(lat, lon) or not near_water(g, lat, lon) or r.get("drct") is None or r.get("sknt") is None:
                continue
            try:
                t = datetime.fromisoformat(r["utc_valid"].replace("Z", "+00:00"))
            except Exception:
                continue
            if now - t > timedelta(hours=3):
                continue
            out.append({"id": "asos_" + r["station"], "name": r.get("name"), "lat": lat, "lon": lon, "dir": int(r["drct"]),
                        "spd": round(float(r["sknt"]), 1), "gust": r.get("gust"), "time": t.isoformat(), "kind": "airport"})
    return out


def forecast_points():
    stations = json.loads((DATA_DIR / "coast_stations.json").read_text())["stations"]
    pts = [(round(s["lat"], 3), round(s["lon"], 3)) for s in stations]
    g = json.loads((DATA_DIR / "coast_raster.json").read_text())
    cells = np.unpackbits(np.frombuffer(base64.b64decode(g["bits"]), dtype=np.uint8))[: g["nx"] * g["ny"]].reshape(g["ny"], g["nx"]).astype(bool)
    near = int(40 / (g["res"] * 111.32))
    from scipy.ndimage import binary_dilation
    coastband = binary_dilation(cells, iterations=near) & ~cells  # water within ~40 km of land
    for lat in np.arange(24.25, 45.6, 0.25):
        for lon in np.arange(-97.5, -65.6, 0.25):
            i, j = int((lon - g["lon0"]) / g["res"]), int((lat - g["lat0"]) / g["res"])
            if 0 <= i < g["nx"] and 0 <= j < g["ny"] and coastband[j, i] and on_east_coast(lat, lon):
                pts.append((round(float(lat), 3), round(float(lon), 3)))
    return sorted(set(pts))


def grid_url(pt, cache):
    key = f"{pt[0]},{pt[1]}"
    if key not in cache:
        d = get(f"https://api.weather.gov/points/{pt[0]},{pt[1]}")
        p = (d or {}).get("properties") or {}
        cache[key] = p.get("forecastGridData")
    return cache[key]


def expand(series, start, hours, conv=1.0):
    out = [None] * hours
    for v in series.get("values", []):
        if v["value"] is None:
            continue
        t_str, dur = v["validTime"].split("/")
        t = datetime.fromisoformat(t_str)
        m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?", dur)
        n = int(m.group(1) or 0) * 24 + int(m.group(2) or 0) or 1
        for k in range(n):
            idx = int((t + timedelta(hours=k) - start).total_seconds() // 3600)
            if 0 <= idx < hours:
                out[idx] = v["value"] * conv
    return out


def main():
    t0 = time.time()
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    obs = latest_obs()
    print(f"{len(obs)} live observations ({time.time() - t0:.0f}s)", flush=True)
    pts = forecast_points()
    cache = json.loads(PTS_CACHE.read_text()) if PTS_CACHE.exists() else {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        urls = list(ex.map(lambda p: grid_url(p, cache), pts))
    PTS_CACHE.write_text(json.dumps(cache))
    by_url = {}
    for p, u in zip(pts, urls):
        if u:
            by_url.setdefault(u, p)
    print(f"{len(pts)} forecast points -> {len(by_url)} distinct NWS grid cells ({time.time() - t0:.0f}s)", flush=True)

    def fetch(item):
        u, p = item
        d = get(u)
        props = (d or {}).get("properties")
        if not props:
            return None
        dirs = expand(props.get("windDirection", {}), start, HOURS)
        spds = expand(props.get("windSpeed", {}), start, HOURS, KMH_TO_KT)
        if sum(x is not None for x in dirs) < 24:
            return None
        return {"lat": p[0], "lon": p[1], "d": [None if x is None else int(round(x)) for x in dirs],
                "s": [None if x is None else round(x, 1) for x in spds]}
    with ThreadPoolExecutor(max_workers=6) as ex:
        fc = [f for f in ex.map(fetch, by_url.items()) if f]
    print(f"{len(fc)} forecast series ({time.time() - t0:.0f}s)", flush=True)
    OUT.write_text(json.dumps({"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                               "obs": obs, "fc": {"start": start.isoformat(), "hours": HOURS, "points": fc},
                               "sources": "NOAA NDBC, Iowa Environmental Mesonet (ASOS), National Weather Service"},
                              separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
