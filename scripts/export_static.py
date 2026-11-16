"""Export everything the hosted (static HTML) version needs into one data.json.

Usage: python -m scripts.export_static [land.json]   (Newport data.json)
       python -m scripts.export_static --coast         (East Coast coast.json)
Writes site/data.json. The page itself is site/index.html.
"""
from __future__ import annotations

import base64
import json
import sys

import numpy as np

from backend import analysis, debrief, model
from backend.config import ROOT, VENUES

OUT = ROOT / "site"
DAY_START_MIN = 8 * 60      # 08:00 local
STEPS = 72                  # 08:00 .. 19:50 in 10-minute steps
MISSING = 65535


def pack(arr: np.ndarray) -> str:
    a = np.where(np.isnan(arr), MISSING, np.round(arr)).astype("<u2")
    return base64.b64encode(a.tobytes()).decode("ascii")


def main():
    venue = "newport"
    cfg = VENUES[venue]
    frames, grid, local = analysis.venue_frames(venue)
    in_season = np.isin(local.month, cfg["season_months"])
    mins = local.hour * 60 + local.minute
    sel = in_season & (mins >= DAY_START_MIN) & (mins < DAY_START_MIN + STEPS * 10)
    dates = sorted(set(local[sel].strftime("%Y-%m-%d")))
    date_idx = {d: i for i, d in enumerate(dates)}
    pos = np.where(sel)[0]
    di = np.array([date_idx[d] for d in local[pos].strftime("%Y-%m-%d")])
    ti = ((mins[pos] - DAY_START_MIN) // 10).astype(int)

    stations = {}
    for sid, meta in cfg["stations"].items():
        df = frames[sid]
        d = np.full(len(dates) * STEPS, np.nan)
        s = np.full(len(dates) * STEPS, np.nan)
        d[di * STEPS + ti] = df["WDIR"].values[pos]
        s[di * STEPS + ti] = df["WSPD_KT"].values[pos] * 10
        stations[sid] = {**meta, "dir": pack(d), "spd10": pack(s)}

    clim = {m: analysis.climatology(venue, m) for m in range(1, 13)}

    tracks = []
    for t in debrief.list_tracks(venue):
        r = debrief.get_track(t["id"])
        tracks.append({k: r[k] for k in ("id", "name", "summary", "points", "twd_series", "tacks", "station", "insights")})

    data = {
        "venue": {"id": venue, "name": cfg["name"], "tz": cfg["tz"], "center": cfg["center"],
                  "primary": cfg["primary"], "season_months": cfg["season_months"]},
        "grid": {"dates": dates, "day_start_min": DAY_START_MIN, "steps": STEPS, "missing": MISSING},
        "stations": stations,
        "climatology": clim,
        "metrics": model.load_metrics(venue),
        "demo_tracks": tracks,
        "liftmap": debrief.lift_map(venue, None),
    }
    if len(sys.argv) > 1 and sys.argv[1].endswith(".json"):
        data["land"] = json.loads(open(sys.argv[1]).read())
    OUT.mkdir(exist_ok=True)
    (OUT / "data.json").write_text(json.dumps(data, separators=(",", ":")))
    print("wrote", OUT / "data.json", (OUT / "data.json").stat().st_size // 1024, "KB,", len(dates), "days")


if __name__ == "__main__" and "--coast" not in sys.argv:
    main()


def export_coast():
    """site/coast.json for the East Coast tab (built by scripts.coast_climatology and scripts.coast_land)."""
    from backend.config import DATA_DIR
    clim = json.loads((DATA_DIR / "coast_stations.json").read_text())
    land = json.loads((DATA_DIR / "coast_land.json").read_text())
    raster = json.loads((DATA_DIR / "coast_raster.json").read_text())
    for s in clim["stations"]:
        s.pop("n", None)
    out = {"years": clim["years"], "stations": clim["stations"], "land": land, "raster": raster}
    eff = DATA_DIR / "coast_direction_effects.json"
    if eff.exists():
        e = json.loads(eff.read_text())
        for st in e["stations"]:
            st.pop("n", None)
        out["effects"] = e
    (OUT / "coast.json").write_text(json.dumps(out, separators=(",", ":")))
    print("wrote", OUT / "coast.json", (OUT / "coast.json").stat().st_size // 1024, "KB,", len(clim["stations"]), "stations")


if __name__ == "__main__" and "--coast" in sys.argv:
    export_coast()
