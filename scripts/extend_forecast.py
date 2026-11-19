"""Continue each region's hourly wind forecast past the end of the National Weather Service (7 days) or MET Norway
forecast with NOAA GFS, out to the end of the GFS run (16 days).

Reads site/global/w/*.png (written by scripts.global_wx gfs) and each region's live.json; the forecast points keep their
own NWS / MET Norway values, and the hours after their last value come from GFS. Over the first 12 hours after the
handover the difference between the two models at the handover is faded out, so there is no sudden jump.
Adds fc["gfs_from"] (first hour index that is GFS) so the page can say which days are the longer-range model.
Usage: python -m scripts.extend_forecast
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image

from backend.config import ROOT

SITE = ROOT / "site"
G = SITE / "global"


def load_gfs():
    meta = json.loads((G / "meta.json").read_text())["gfs"]
    run = datetime.strptime(meta["run"], "%Y-%m-%dT%H:00Z").replace(tzinfo=timezone.utc)
    U, V = [], []
    for k in range(len(meta["hours"])):
        a = np.asarray(Image.open(G / "w" / f"{k}.png"), np.float32)
        U.append(a[..., 0] / 255 * 80 - 40); V.append(a[..., 1] / 255 * 80 - 40)
    return run, np.array(meta["hours"], float), np.stack(U), np.stack(V)


def sample(A, lat, lon):  # A: (steps, 361, 720), rows 90N->90S, cols 180W.. by 0.5 degree
    x = ((lon + 180) % 360) / 0.5; y = (90 - lat) / 0.5
    i = int(math.floor(x)); j = min(359, int(math.floor(y))); fx = x - i; fy = y - j; i0 = i % 720; i1 = (i + 1) % 720
    return A[:, j, i0] * (1 - fx) * (1 - fy) + A[:, j, i1] * fx * (1 - fy) + A[:, j + 1, i0] * (1 - fx) * fy + A[:, j + 1, i1] * fx * fy


def main():
    run, hours, U, V = load_gfs()
    end = run + timedelta(hours=float(hours[-1]))
    for path in [SITE / "live.json"] + [SITE / r / "live.json" for r in ("west", "lakes", "hawaii", "iberia")]:
        if not path.exists():
            continue
        d = json.loads(path.read_text()); fc = d["fc"]
        start = datetime.fromisoformat(fc["start"])
        old = fc["hours"]; new = max(old, int((end - start).total_seconds() // 3600) + 1)
        th = np.array([(start + timedelta(hours=h) - run).total_seconds() / 3600 for h in range(new)])
        first_gfs = fc.get("gfs_from", new)  # keep the handover from an earlier run of this script
        for p in fc["points"]:
            u = np.interp(th, hours, sample(U, p["lat"], p["lon"])); v = np.interp(th, hours, sample(V, p["lat"], p["lon"]))
            gs = np.hypot(u, v) * 1.943844; gd = (np.degrees(np.arctan2(-u, -v)) + 360) % 360
            s = list(p["s"]) + [None] * (new - len(p["s"])); dd = list(p["d"]) + [None] * (new - len(p["d"]))
            last = max((i for i in range(len(s)) if s[i] is not None and dd[i] is not None), default=-1)
            if last < 0 or th[last + 1 if last + 1 < new else last] < hours[0]:
                continue
            ds = s[last] - gs[last]; dd0 = ((dd[last] - gd[last] + 180) % 360) - 180
            for i in range(last + 1, new):
                if th[i] > hours[-1]:
                    break
                f = max(0.0, 1 - (i - last) / 12)
                s[i] = round(max(0.0, float(gs[i] + ds * f)), 1); dd[i] = round(float((gd[i] + dd0 * f) % 360))
            p["s"], p["d"] = s, dd
            first_gfs = min(first_gfs, last + 1)
        fc["hours"] = new; fc["gfs_from"] = first_gfs; fc["gfs_run"] = run.strftime("%Y-%m-%dT%H:00Z")
        path.write_text(json.dumps(d, separators=(",", ":")))
        print(f"{path.relative_to(SITE)}: {old} -> {new} hours (GFS from hour {first_gfs})")


if __name__ == "__main__":
    main()
