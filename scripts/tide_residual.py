"""How far the water is running above or below the predicted tide right now, at NOAA's real-time water-level gauges.

Tide predictions only know the moon and sun. Wind blowing water ashore (or off), river flow and air pressure push the
real level up or down, often by a foot or more in the Gulf of Mexico and shallow bays. This compares the last 6 hours of
measured water level with NOAA's prediction at each real-time US gauge (NOAA CO-OPS API) and writes site/surge.json:
{"time": iso, "st": [[id, name, lat, lon, residual_ft], ...]}. Great Lakes gauges are left out (no tide there).
Usage: python -m scripts.tide_residual
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import time
import urllib.request
from datetime import datetime, timezone

from backend.config import ROOT

API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?station={id}&product={p}&range=6&datum=MLLW&units=english&time_zone=gmt&format=json&application=wind_prototype"
UA = {"User-Agent": "wind-analytics-prototype"}


def get(url, tries=3):
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return json.load(r)
        except Exception:
            time.sleep(2 * (a + 1))
    return None


def one(s):
    obs = get(API.format(id=s["id"], p="water_level")); pred = get(API.format(id=s["id"], p="predictions"))
    if not obs or not pred or "data" not in obs or "predictions" not in pred:
        return None
    P = {x["t"]: float(x["v"]) for x in pred["predictions"] if x.get("v")}
    diffs = [float(x["v"]) - P[x["t"]] for x in obs["data"] if x.get("v") and x["t"] in P]
    if len(diffs) < 20:
        return None
    diffs.sort()
    return [s["id"], s["name"], round(s["lat"], 4), round(s["lng"], 4), round(diffs[len(diffs) // 2], 2)]


def main():
    t0 = time.time()
    meta = get("https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=waterlevels")
    sts = [s for s in meta["stations"] if s.get("tidal") and not s.get("greatlakes")]
    with cf.ThreadPoolExecutor(8) as ex:
        out = [r for r in ex.map(one, sts) if r]
    (ROOT / "site" / "surge.json").write_text(json.dumps({"time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "st": out}, separators=(",", ":")))
    big = sorted(out, key=lambda r: -abs(r[4]))[:5]
    print(f"{len(out)} of {len(sts)} gauges ({time.time() - t0:.0f}s); largest: " + ", ".join(f"{r[1]} {r[4]:+.1f} ft" for r in big))


if __name__ == "__main__":
    main()
