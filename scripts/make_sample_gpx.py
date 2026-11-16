"""Generate SYNTHETIC GPX race tracks for demo and testing.

The base wind comes from the real Newport harbor station on the chosen date.
Added on top: simulated oscillations and a simulated geographic shift
(+6 degrees veer per km to the east). None of these are real boats.

Usage: python -m scripts.make_sample_gpx
"""
from __future__ import annotations

import math
import random
from pathlib import Path

import numpy as np
import pandas as pd

from backend.analysis import venue_frames, wrap180

OUT = Path(__file__).resolve().parent.parent / "samples"
COURSE_CENTER = (41.425, -71.365)  # Rhode Island Sound, south of Brenton Point
KT_TO_MS = 0.514444


def find_days(n=2):
    frames, grid, local = venue_frames("newport")
    p = frames["nwpr1"]
    days = []
    for d in sorted(set(local[(local.year == 2024) & (local.month.isin([7, 8]))].date)):
        m = (local.date == d) & (local.hour >= 13) & (local.hour < 15)
        seg = p[m]
        if seg["WDIR"].notna().all() and seg["WDIR"].between(195, 230).all() and seg["WSPD_KT"].between(9, 16).all():
            days.append(d)
    return days[:n] if days else []


def wind(t_s, x_e, base_fn, rng_phase):
    """TWD at time t (s since start) and east offset x_e (m)."""
    osc = 7 * math.sin(2 * math.pi * t_s / 540 + rng_phase) + 3 * math.sin(2 * math.pi * t_s / 200 + 1.3 * rng_phase)
    geo = 6.0 * x_e / 1000.0
    return (base_fn(t_s) + osc + geo) % 360


def simulate(base_fn, t0, skill, seed, name):
    rng = random.Random(seed)
    phase = 1.7
    dt = 2.0
    leg_m = 1.2 * 1852
    axis = base_fn(0)
    ax = math.radians(axis)
    # Positions in local metres: x east, y north. Start at leeward end.
    x = -math.sin(ax) * leg_m / 2 + rng.uniform(-60, 60)
    y = -math.cos(ax) * leg_m / 2
    marks = {"W": (math.sin(ax) * leg_m / 2, math.cos(ax) * leg_m / 2),
             "L": (-math.sin(ax) * leg_m / 2, -math.cos(ax) * leg_m / 2)}
    legs = ["W", "L", "W", "L"]
    tack = rng.choice(["stbd", "port"])
    t, pts = 0.0, []
    hist = []
    tack_cool = 0.0
    for target in legs:
        mx, my = marks[target]
        upwind = target == "W"
        while t < 4 * 3600:
            dist = math.hypot(mx - x, my - y)
            along = x * math.sin(ax) + y * math.cos(ax)  # distance up the course axis
            if dist < 60 or (upwind and along >= leg_m / 2 - 20) or (not upwind and along <= -leg_m / 2 + 20):
                break
            twd = wind(t, x, base_fn, phase)
            hist.append(twd)
            hist = hist[-180:]  # ~6 min
            mean = (math.degrees(math.atan2(np.mean(np.sin(np.radians(hist))), np.mean(np.cos(np.radians(hist))))) + 360) % 360
            brg = (math.degrees(math.atan2(mx - x, my - y)) + 360) % 360
            if upwind and dist < 300 and abs(wrap180(brg - twd)) > 80:
                break  # overstood and passed the mark: count it as rounded
            if upwind:
                half = 42
                hdg = twd - half if tack == "stbd" else twd + half
                spd = 5.6
                # Tactical decision: good sailors tack on persistent headers; everyone tacks at laylines.
                headed = (twd - mean) if tack == "port" else (mean - twd)
                off_axis = wrap180(brg - twd)
                overstood = (tack == "stbd" and off_axis > half + 3) or (tack == "port" and off_axis < -half - 3)
                if tack_cool <= 0 and (overstood or (headed > 5 and rng.random() < skill * 0.05)
                                       or rng.random() < (1 - skill) * 0.003):
                    tack = "port" if tack == "stbd" else "stbd"
                    tack_cool = 90
                    spd = 2.5
                    hdg = twd - half if tack == "stbd" else twd + half
            else:
                hdg = brg + rng.uniform(-6, 6)
                spd = 6.4
            tack_cool -= dt
            hdg += rng.gauss(0, 2.5)
            # Small tidal current setting east-northeast, as a GPS course would include.
            cx, cy = 0.15, 0.05
            v = spd * KT_TO_MS
            x += (v * math.sin(math.radians(hdg)) + cx) * dt
            y += (v * math.cos(math.radians(hdg)) + cy) * dt
            t += dt
            lat = COURSE_CENTER[0] + y / 111320.0
            lon = COURSE_CENTER[1] + x / (111320.0 * math.cos(math.radians(COURSE_CENTER[0])))
            lat += rng.gauss(0, 1.5) / 111320.0
            lon += rng.gauss(0, 1.5) / 83000.0
            pts.append((t0 + pd.Timedelta(seconds=t), lat, lon))
        if upwind:
            tack = rng.choice(["stbd", "port"])

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="sailiq-wind synthetic generator" xmlns="http://www.topografix.com/GPX/1/1">',
             f"<trk><name>{name}</name><trkseg>"]
    for ts, lat, lon in pts:
        lines.append(f'<trkpt lat="{lat:.7f}" lon="{lon:.7f}"><time>{ts.strftime("%Y-%m-%dT%H:%M:%SZ")}</time></trkpt>')
    lines.append("</trkseg></trk></gpx>")
    return "\n".join(lines)


def main():
    OUT.mkdir(exist_ok=True)
    frames, grid, local = venue_frames("newport")
    p = frames["nwpr1"]
    days = find_days(3)
    if not days:
        raise SystemExit("No suitable day found")
    for di, d in enumerate(days):
        t0 = pd.Timestamp(f"{d} 13:00", tz="America/New_York").tz_convert("UTC")
        seg = p.loc[t0: t0 + pd.Timedelta("3h"), "WDIR"].interpolate()
        secs = (seg.index - t0).total_seconds().values
        unwrapped = np.rad2deg(np.unwrap(np.deg2rad(seg.values)))
        base_fn = lambda s, secs=secs, u=unwrapped: float(np.interp(s, secs, u)) % 360
        for bi, (skill, label) in enumerate([(0.9, "BoatA"), (0.6, "BoatB"), (0.3, "BoatC"), (0.75, "BoatD")]):
            name = f"SYNTHETIC {label} {d}"
            gpx = simulate(base_fn, t0, skill, seed=100 * di + bi, name=name)
            path = OUT / f"synthetic_{label}_{d}.gpx"
            path.write_text(gpx)
            print("wrote", path)


if __name__ == "__main__":
    main()
