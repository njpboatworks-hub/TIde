"""Typical wind (by month and local hour) for every East Coast NOAA NDBC station with wind data.

Downloads each station-year in memory (nothing large is kept on disk) and writes
data/coast_stations.json with, for every station:
  dir[month][hour]   resultant (vector-mean) wind direction, degrees true (from)
  spd[month][hour]   median wind speed, knots
  steady[month][hour] steadiness 0..1 = |vector mean| / scalar mean (1 = always the same direction)
  n[month][hour]     number of hourly samples

Usage: python -m scripts.coast_climatology
"""
from __future__ import annotations

import gzip
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from backend.config import DATA_DIR
from backend.data import NDBC_HIST_URL, parse_stdmet

YEARS = list(range(2019, 2025))
TABLE_URL = "https://www.ndbc.noaa.gov/data/stations/station_table.txt"
INDEX_URL = "https://www.ndbc.noaa.gov/data/historical/stdmet/"
OUT = DATA_DIR / "coast_stations.json"


def fetch(url: str) -> bytes | None:
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wind-analytics-client"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 ** attempt)
        except Exception:
            time.sleep(2 ** attempt)
    return None


def on_east_coast(lat: float, lon: float) -> bool:
    from scripts.region import in_region
    return in_region(lat, lon)


def station_list() -> list[dict]:
    table = fetch(TABLE_URL).decode("utf-8", "ignore")
    index = fetch(INDEX_URL).decode("utf-8", "ignore")
    have = {}
    for sid, yr in re.findall(r'href="([a-z0-9]+)h(\d{4})\.txt\.gz"', index):
        if int(yr) in YEARS:
            have.setdefault(sid, set()).add(int(yr))
    out = []
    for line in table.splitlines():
        if line.startswith("#"):
            continue
        f = line.split("|")
        if len(f) < 7:
            continue
        m = re.match(r"\s*([\d.]+) ([NS]) ([\d.]+) ([EW])", f[6])
        if not m:
            continue
        lat = float(m.group(1)) * (1 if m.group(2) == "N" else -1)
        lon = float(m.group(3)) * (-1 if m.group(4) == "W" else 1)
        sid = f[0].strip().lower()
        if on_east_coast(lat, lon) and sid in have:
            name = re.sub(r"<[^>]+>", "", f[4]).strip() or sid.upper()
            out.append({"id": sid, "name": name, "lat": lat, "lon": lon, "years": sorted(have[sid])})
    return out


def climatology(st: dict) -> dict | None:
    parts = []
    for y in st["years"]:
        blob = fetch(NDBC_HIST_URL.format(sid=st["id"], year=y))
        if not blob:
            continue
        try:
            df = parse_stdmet(gzip.decompress(blob).decode("ascii", "ignore"))
        except Exception:
            continue
        df = df[["WDIR", "WSPD_KT"]].dropna()
        if len(df):
            parts.append(df)
    if not parts:
        return None
    df = pd.concat(parts)
    # Hourly means first so 6-minute and hourly stations count equally.
    r = np.deg2rad(df["WDIR"])
    h = pd.DataFrame({"u": np.sin(r) * df["WSPD_KT"], "v": np.cos(r) * df["WSPD_KT"], "s": df["WSPD_KT"]}).resample("1h").mean().dropna()
    if len(h) < 24 * 200:  # under ~200 days of wind data: too thin to call "typical"
        return None
    loc = h.index.tz_convert("America/New_York")
    h["mo"], h["hr"] = loc.month, loc.hour
    g = h.groupby(["mo", "hr"])
    mu, mv, ms = g["u"].mean(), g["v"].mean(), g["s"].mean()
    med, n = g["s"].median(), g["s"].size()
    D = [[None] * 24 for _ in range(12)]
    S = [[None] * 24 for _ in range(12)]
    T = [[None] * 24 for _ in range(12)]
    N = [[0] * 24 for _ in range(12)]
    for (mo, hr), cnt in n.items():
        if cnt < 30:
            continue
        u, v = mu[(mo, hr)], mv[(mo, hr)]
        D[mo - 1][hr] = int(round((np.degrees(np.arctan2(u, v)) + 360) % 360))
        S[mo - 1][hr] = round(float(med[(mo, hr)]), 1)
        T[mo - 1][hr] = round(float(np.hypot(u, v) / ms[(mo, hr)]), 2) if ms[(mo, hr)] > 0 else 0
        N[mo - 1][hr] = int(cnt)
    if sum(x is not None for row in D for x in row) < 12 * 24 * 0.5:
        return None
    return {**{k: st[k] for k in ("id", "name", "lat", "lon")}, "dir": D, "spd": S, "steady": T, "n": N}


def main():
    """Adds stations not already in data/coast_stations.json (existing ones are kept as they are)."""
    stations = station_list()
    existing = json.loads(OUT.read_text()) if OUT.exists() else {"years": [YEARS[0], YEARS[-1]], "stations": []}
    have = {s["id"] for s in existing["stations"]}
    todo = [s for s in stations if s["id"] not in have]
    print(f"{len(stations)} stations in region with history files; {len(todo)} new to process", flush=True)
    results, done = [], 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(climatology, todo):
            done += 1
            if res:
                results.append(res)
            if done % 20 == 0:
                print(f"  {done}/{len(todo)} processed, {len(results)} usable", flush=True)
    existing["stations"].extend(results)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(existing, separators=(",", ":")))
    print(f"wrote {OUT}: added {len(results)}; {len(existing['stations'])} stations total")


if __name__ == "__main__":
    sys.exit(main())
