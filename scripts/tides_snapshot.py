"""NOAA CO-OPS tide and tidal-current predictions for East Coast stations over a date window.

Predictions are astronomical (calculated from harmonic constants), so they do not go stale.
For every station we store the predicted events:
  currents: max flood / slack / max ebb times with signed speed (knots, + flood, - ebb)
            plus mean flood and ebb directions (degrees true, direction of flow)
  tides:    high and low water times with heights (feet above MLLW)
The page draws the usual smooth (cosine) curve between events.

Usage: python -m scripts.tides_snapshot [days_ahead=16]
Writes site/tides.json.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from backend.config import ROOT

API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
MD = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type="
OUT = ROOT / "site" / "tides.json"


def get(url: str):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wind-analytics-prototype"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None


def east(lat, lon):
    from scripts.region import in_region
    return in_region(lat, lon)


def main(days_ahead: int = 16, in_region=None, out=None, tz="America/New_York"):
    inside = in_region or east
    out = out or OUT
    start = date.today() - timedelta(days=1)
    end = date.today() + timedelta(days=days_ahead)
    t0 = datetime(start.year, start.month, start.day)
    q = f"begin_date={start:%Y%m%d}&end_date={end:%Y%m%d}&units=english&time_zone=lst_ldt&format=json&application=wind_prototype"
    mins = lambda s: int((datetime.strptime(s, "%Y-%m-%d %H:%M") - t0).total_seconds() // 60)

    # ---- currents: one entry per station, the shallowest prediction bin
    by_id = {}
    for s in get(MD + "currentpredictions")["stations"]:
        if s["type"] in ("H", "S") and inside(s["lat"], s["lng"]):
            by_id.setdefault(s["id"], []).append(s)
    cur_st = [min(v, key=lambda s: (s["depth"] if s["depth"] is not None else 0, s["currbin"] or 0)) for v in by_id.values()]

    def current(s):
        d = get(f"{API}?product=currents_predictions&station={s['id']}&bin={s['currbin']}&interval=MAX_SLACK&{q}")
        cp = (d or {}).get("current_predictions", {}).get("cp")
        if not cp:
            return None
        ev = [[mins(e["Time"]), int(round(float(e["Velocity_Major"]) * 100))] for e in cp]
        out = {"id": s["id"], "name": s["name"], "lat": round(s["lat"], 5), "lon": round(s["lng"], 5),
               "flood": cp[0].get("meanFloodDir"), "ebb": cp[0].get("meanEbbDir"), "ev": ev}
        if s["type"] == "H":
            # Harmonic stations: NOAA's full prediction every 30 minutes, so the page uses the real curve shape
            # (slack-to-max is often not a smooth wave) instead of a cosine between slack and max times.
            d2 = get(f"{API}?product=currents_predictions&station={s['id']}&bin={s['currbin']}&interval=30&{q}")
            ts = (d2 or {}).get("current_predictions", {}).get("cp")
            if ts and len(ts) > 48:
                out["ts"] = [int(round(float(e["Velocity_Major"]) * 100)) for e in ts]
                out["ts0"] = mins(ts[0]["Time"])
        return out

    # ---- tides: high/low events
    tide_st = [s for s in get(MD + "tidepredictions")["stations"] if inside(s["lat"], s["lng"])]

    def tide(s):
        d = get(f"{API}?product=predictions&station={s['id']}&datum=MLLW&interval=hilo&{q}")
        p = (d or {}).get("predictions")
        if not p:
            return None
        return {"id": s["id"], "name": s["name"], "lat": round(s["lat"], 5), "lon": round(s["lng"], 5),
                "ev": [[mins(e["t"]), int(round(float(e["v"]) * 100)), e["type"]] for e in p]}

    print(f"window {start} to {end}: {len(cur_st)} current stations, {len(tide_st)} tide stations", flush=True)
    with ThreadPoolExecutor(max_workers=8) as ex:
        currents = [c for c in ex.map(current, cur_st) if c]
        print(f"  currents done: {len(currents)}", flush=True)
        tides = [t for t in ex.map(tide, tide_st) if t]
        print(f"  tides done: {len(tides)}", flush=True)
    # channel speed factor at each current station (from scripts.bathymetry), used to carry its speed to nearby water
    ff = ROOT / "data" / "depth_station_factors.json"
    if ff.exists():
        fac = json.loads(ff.read_text())
        for c in currents:
            if c["id"] in fac:
                c["df"] = fac[c["id"]]
    out.write_text(json.dumps({"start": f"{start}T00:00", "end": f"{end}T23:59", "tz": tz,
                               "source": "NOAA CO-OPS predictions", "currents": currents, "tides": tides},
                              separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 16)
