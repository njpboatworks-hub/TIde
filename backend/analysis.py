"""Historical analog-day analysis for pre-race planning.

Idea: find past days at the venue whose conditions at race start resembled the
expected conditions (same season, similar direction and speed), then measure
what the wind actually did over the race window on those days.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from .config import VENUES
from .data import load_venue

STEP = pd.Timedelta("10min")


def wrap180(a):
    return (np.asarray(a, dtype=float) + 180.0) % 360.0 - 180.0


def circ_mean(deg, axis=None):
    r = np.deg2rad(deg)
    return (np.rad2deg(np.arctan2(np.nanmean(np.sin(r), axis=axis), np.nanmean(np.cos(r), axis=axis))) + 360) % 360


@lru_cache(maxsize=4)
def venue_frames(venue: str):
    """Return per-station frames on one shared regular 10-minute UTC grid."""
    raw = load_venue(venue)
    start = min(df.index.min() for df in raw.values())
    end = max(df.index.max() for df in raw.values())
    grid = pd.date_range(start, end, freq=STEP)
    frames = {sid: df.reindex(grid) for sid, df in raw.items()}
    tz = VENUES[venue]["tz"]
    local = grid.tz_convert(tz)
    return frames, grid, local


def _uv(df):
    r = np.deg2rad(df["WDIR"].values)
    s = df["WSPD_KT"].values
    return np.sin(r) * s, np.cos(r) * s


def _windows(arr: np.ndarray, starts: np.ndarray, n: int) -> np.ndarray:
    idx = starts[:, None] + np.arange(n)[None, :]
    idx = np.clip(idx, 0, len(arr) - 1)
    return arr[idx]


def plan(venue: str, months: list[int], start_hour: float, duration_h: float,
         direction: float, speed: float, dir_tol: float = 25, spd_tol: float = 4) -> dict:
    cfg = VENUES[venue]
    frames, grid, local = venue_frames(venue)
    prim = frames[cfg["primary"]]

    # Conditions at start: 30-minute vector average ending at start time.
    u, v = _uv(prim)
    us = pd.Series(u).rolling(3, min_periods=2).mean().values
    vs = pd.Series(v).rolling(3, min_periods=2).mean().values
    start_dir = (np.rad2deg(np.arctan2(us, vs)) + 360) % 360
    start_spd = prim["WSPD_KT"].rolling(3, min_periods=2).mean().values

    sh, sm = int(start_hour), int(round((start_hour % 1) * 60 / 10) * 10)
    is_start = (local.hour == sh) & (local.minute == sm) & np.isin(local.month, months)
    cand = np.where(is_start)[0]
    ok = (
        (np.abs(wrap180(start_dir[cand] - direction)) <= dir_tol)
        & (np.abs(start_spd[cand] - speed) <= spd_tol)
    )
    starts = cand[ok]
    n = int(round(duration_h * 6)) + 1
    times_min = np.arange(n) * 10

    result = {
        "query": dict(months=months, start_hour=start_hour, duration_h=duration_h, direction=direction,
                      speed=speed, dir_tol=dir_tol, spd_tol=spd_tol),
        "candidates": int(len(cand)),
        "n_analogs": int(len(starts)),
        "times_min": times_min.tolist(),
    }
    if len(starts) == 0:
        return result

    dirs = _windows(prim["WDIR"].values, starts, n)
    spds = _windows(prim["WSPD_KT"].values, starts, n)
    # Drop analog days with too much missing data in the window.
    good = (np.isnan(dirs).mean(axis=1) < 0.2)
    starts, dirs, spds = starts[good], dirs[good], spds[good]
    result["n_analogs"] = int(len(starts))
    if len(starts) == 0:
        return result

    ref = start_dir[starts]
    delta = wrap180(dirs - ref[:, None])

    # Per-day metrics.
    t_h = times_min / 60.0
    end_shift = np.nanmean(delta[:, -3:], axis=1)
    slopes, osc_amp, osc_period, max_r, max_l = [], [], [], [], []
    for row in delta:
        m = ~np.isnan(row)
        if m.sum() < 4:
            slopes.append(np.nan); osc_amp.append(np.nan); osc_period.append(np.nan)
            max_r.append(np.nan); max_l.append(np.nan)
            continue
        p = np.polyfit(t_h[m], row[m], 1)
        slopes.append(p[0])
        resid = row[m] - np.polyval(p, t_h[m])
        osc_amp.append((np.percentile(resid, 90) - np.percentile(resid, 10)) / 2)
        sgn = np.sign(resid)
        crossings = np.sum(sgn[1:] * sgn[:-1] < 0)
        osc_period.append(2 * (t_h[m][-1] - t_h[m][0]) * 60 / crossings if crossings >= 2 else np.nan)
        max_r.append(np.nanmax(row)); max_l.append(np.nanmin(row))
    slopes, osc_amp, osc_period = map(np.array, (slopes, osc_amp, osc_period))

    persistent_thresh = 10.0
    veer = end_shift > persistent_thresh
    back = end_shift < -persistent_thresh
    osc = ~veer & ~back

    def pct(x):
        return round(100.0 * float(np.mean(x)), 1)

    days = []
    for i, s in enumerate(starts):
        days.append({
            "date": local[s].strftime("%Y-%m-%d"),
            "start_dir": round(float(ref[i])), "start_speed": round(float(spds[i, 0]), 1),
            "end_shift": round(float(end_shift[i]), 1),
            "end_speed": round(float(np.nanmean(spds[i, -3:])), 1),
            "osc_amp": None if np.isnan(osc_amp[i]) else round(float(osc_amp[i]), 1),
            "trace": [None if np.isnan(x) else round(float(x), 1) for x in delta[i]],
        })

    result.update({
        "summary": {
            "pct_persistent_right": pct(veer),
            "pct_persistent_left": pct(back),
            "pct_oscillating": pct(osc),
            "median_end_shift": round(float(np.nanmedian(end_shift)), 1),
            "median_trend_deg_per_h": round(float(np.nanmedian(slopes)), 1),
            "median_osc_amplitude": round(float(np.nanmedian(osc_amp)), 1),
            "median_osc_period_min": None if np.all(np.isnan(osc_period)) else round(float(np.nanmedian(osc_period))),
            "median_speed_change": round(float(np.nanmedian(np.nanmean(spds[:, -3:], 1) - spds[:, 0])), 1),
        },
        "envelope": {
            "p10": _nanpct(delta, 10), "p25": _nanpct(delta, 25), "p50": _nanpct(delta, 50),
            "p75": _nanpct(delta, 75), "p90": _nanpct(delta, 90),
            "speed_p50": _nanpct(spds, 50), "speed_p25": _nanpct(spds, 25), "speed_p75": _nanpct(spds, 75),
        },
        "days": sorted(days, key=lambda d: d["date"], reverse=True),
        "stations": _station_fields(frames, cfg, starts, n),
    })
    result["advice"] = advice(result["summary"], result["n_analogs"])
    return result


def _nanpct(a, q):
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = np.nanpercentile(a, q, axis=0)
    return [None if np.isnan(x) else round(float(x), 1) for x in out]


def _station_fields(frames, cfg, starts, n):
    """Vector-mean wind at each station over the race window, across analog days."""
    out = {}
    for sid, meta in cfg["stations"].items():
        df = frames[sid]
        u, v = _uv(df)
        U = _windows(u, starts, n)
        V = _windows(v, starts, n)
        with np.errstate(all="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                mu, mv = np.nanmean(U, 0), np.nanmean(V, 0)
                spd = np.nanmedian(_windows(df["WSPD_KT"].values, starts, n), 0)
                cover = 1 - np.isnan(U).mean(0)
        d = (np.rad2deg(np.arctan2(mu, mv)) + 360) % 360
        out[sid] = {
            **meta,
            "dir": [None if (np.isnan(x) or c < 0.3) else round(float(x)) for x, c in zip(d, cover)],
            "speed": [None if (np.isnan(x) or c < 0.3) else round(float(x), 1) for x, c in zip(spd, cover)],
        }
    return out


def advice(s: dict, n: int) -> list[str]:
    tips = []
    if n < 8:
        tips.append(f"Only {n} matching days were found, so treat these percentages as weak evidence. "
                    "Widen the direction or speed tolerance or add months.")
    r, l, o = s["pct_persistent_right"], s["pct_persistent_left"], s["pct_oscillating"]
    if r >= 50 and r > l * 2:
        tips.append(f"On {r}% of similar days the wind veered (clockwise) by more than 10° over the race. "
                    "History favors the RIGHT side of the upwind leg (looking upwind), toward the new breeze.")
    elif l >= 50 and l > r * 2:
        tips.append(f"On {l}% of similar days the wind backed (counter-clockwise) by more than 10°. "
                    "History favors the LEFT side of the upwind leg (looking upwind).")
    else:
        tips.append(f"No strong persistent trend: {r}% veered, {l}% backed and {o}% stayed within ±10°. "
                    "Plan to play the middle and tack on the headers rather than banking on one side.")
    if s["median_trend_deg_per_h"] and abs(s["median_trend_deg_per_h"]) >= 4:
        side = "right (veer)" if s["median_trend_deg_per_h"] > 0 else "left (back)"
        tips.append(f"The typical trend is {abs(s['median_trend_deg_per_h'])}° per hour to the {side}. "
                    "Later legs are likely to be more one-sided than the first beat.")
    if s["median_osc_amplitude"] is not None:
        per = s["median_osc_period_min"]
        per_txt = f" with a typical cycle of about {per} minutes" if per else ""
        tips.append(f"Oscillations around the trend are typically ±{s['median_osc_amplitude']}°{per_txt}. "
                    "This is measured from 10-minute averages at the harbor station, so shorter puffs and shifts "
                    "on the course will be larger and faster than this.")
    if s["median_speed_change"] >= 3:
        tips.append(f"Breeze typically BUILDS by about {s['median_speed_change']} kt over the window. "
                    "This is consistent with a sea breeze filling, so check sail choices for the later races.")
    elif s["median_speed_change"] <= -3:
        tips.append(f"Breeze typically FADES by about {abs(s['median_speed_change'])} kt over the window.")
    return tips


def climatology(venue: str, month: int) -> dict:
    """Median direction and speed at the primary station by local hour, for one month."""
    cfg = VENUES[venue]
    frames, grid, local = venue_frames(venue)
    prim = frames[cfg["primary"]]
    m = (local.month == month) & (prim["WSPD_KT"].values >= 3)
    df = pd.DataFrame({"hour": local.hour[m], "dir": prim["WDIR"].values[m], "spd": prim["WSPD_KT"].values[m]})
    df = df.dropna()
    hours = list(range(cfg["day_start_hour"], cfg["day_end_hour"] + 1))
    out = {"hours": hours, "dir": [], "spd": [], "sectors": []}
    sector_names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    for h in hours:
        d = df[df.hour == h]
        out["dir"].append(round(float(circ_mean(d["dir"].values))) if len(d) else None)
        out["spd"].append(round(float(d["spd"].median()), 1) if len(d) else None)
        sec = ((d["dir"].values + 22.5) // 45 % 8).astype(int)
        counts = np.bincount(sec, minlength=8) / max(len(sec), 1) * 100
        out["sectors"].append({n: round(float(c), 1) for n, c in zip(sector_names, counts)})
    return out
