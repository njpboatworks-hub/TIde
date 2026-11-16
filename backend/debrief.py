"""GPS track debrief: infer wind shifts from a boat's tacking headings.

Method (upwind sailing):
  On starboard tack a boat steers about (TWD - half_tack_angle);
  on port tack about (TWD + half_tack_angle).
  So every upwind GPS point gives a local true-wind-direction estimate.
  Comparing that estimate with the leg's median tells us whether the boat
  was lifted (pointing closer to the mark) or headed at that moment.

Limits, stated plainly in the UI:
  * GPS gives course over ground, not compass heading, so tidal current and
    leeway are folded into the estimate.
  * Steering changes (footing, pinching, waves) look like small shifts.
"""
from __future__ import annotations

import json
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .analysis import circ_mean, venue_frames, wrap180
from .config import DATA_DIR, VENUES

TRACK_DIR = DATA_DIR / "tracks"
EARTH_R = 6371000.0
KT = 1.943844


def parse_gpx(content: bytes) -> pd.DataFrame:
    root = ET.fromstring(content)
    pts = []
    for el in root.iter():
        if el.tag.split("}")[-1] != "trkpt":
            continue
        t = None
        for c in el:
            if c.tag.split("}")[-1] == "time":
                t = c.text
        if t is None:
            continue
        pts.append((pd.Timestamp(t), float(el.get("lat")), float(el.get("lon"))))
    if len(pts) < 20:
        raise ValueError("GPX file has fewer than 20 timestamped track points.")
    df = pd.DataFrame(pts, columns=["time", "lat", "lon"]).set_index("time").sort_index()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df[~df.index.duplicated()]
    return df


def _kinematics(df: pd.DataFrame, step_s: int = 5) -> pd.DataFrame:
    r = df.resample(f"{step_s}s").mean().interpolate(limit=6).dropna()
    lat, lon = np.deg2rad(r["lat"].values), np.deg2rad(r["lon"].values)
    # Centered differences over +-2 samples for a smoother course.
    k = 2
    dlat = np.roll(lat, -k) - np.roll(lat, k)
    dlon = (np.roll(lon, -k) - np.roll(lon, k)) * np.cos(lat)
    dist = np.hypot(dlat, dlon) * EARTH_R
    cog = (np.rad2deg(np.arctan2(dlon, dlat)) + 360) % 360
    sog = dist / (2 * k * step_s) * KT
    r["cog"], r["sog"] = cog, sog
    r = r.iloc[k:-k]
    return r


def _estimate_twd(cog: np.ndarray, sog: np.ndarray) -> tuple[float, float]:
    """Find wind direction and half tacking angle that best explain two upwind heading clusters."""
    moving = sog > 1.5
    c = cog[moving]
    best = (-1, 0.0, 45.0)
    for twd in range(0, 360, 2):
        rel = wrap180(c - twd)
        for half in (38, 42, 46, 50):
            stb = np.abs(rel + half) < 10
            prt = np.abs(rel - half) < 10
            score = min(stb.sum(), prt.sum())
            if score > best[0]:
                best = (score, twd, half)
    _, twd, half = best
    # Refine: average the per-point estimates within the clusters.
    rel = wrap180(c - twd)
    stb = np.abs(rel + half) < 15
    prt = np.abs(rel - half) < 15
    est = np.concatenate([c[stb] + half, c[prt] - half])
    twd_ref = float(circ_mean(est))
    half_ref = float((np.median(np.abs(wrap180(c[prt] - twd_ref))) + np.median(np.abs(wrap180(c[stb] - twd_ref)))) / 2)
    return twd_ref, half_ref


def analyze(df: pd.DataFrame, venue: str = "newport", twd_hint: float | None = None) -> dict:
    k = _kinematics(df)
    cog, sog = k["cog"].values, k["sog"].values
    twd0, half = _estimate_twd(cog, sog)
    if twd_hint is not None:
        twd0 = float(twd_hint)

    rel = wrap180(cog - twd0)
    upwind = (np.abs(rel) < half + 25) & (np.abs(rel) > max(half - 25, 15)) & (sog > 1.5)
    tack = np.where(rel < 0, "stbd", "port")  # starboard: wind over starboard bow, heading left of wind
    local_twd = np.where(tack == "stbd", cog + half, cog - half) % 360
    local_twd = np.where(upwind, local_twd, np.nan)

    # Smooth the TWD estimate over ~30 s within upwind stretches.
    s = pd.Series(np.where(upwind, wrap180(local_twd - twd0), np.nan), index=k.index)
    shift = s.rolling("30s", min_periods=3).median()

    # Rolling 10-minute median as the "mean wind" baseline to split trend from oscillation.
    baseline = shift.rolling("10min", min_periods=10, center=True).median()
    lift = np.where(tack == "stbd", shift.values, -shift.values)  # +ve = lifted

    # Tacks: upwind tack-side changes.
    tacks = []
    last_side, last_t = None, None
    for t, u, side in zip(k.index, upwind, tack):
        if not u:
            continue
        if last_side is not None and side != last_side and (t - last_t).total_seconds() < 120:
            tacks.append(t)
        last_side, last_t = side, t
    tack_info = []
    for t in tacks:
        before = shift[(shift.index < t) & (shift.index >= t - pd.Timedelta("60s"))]
        side_before = tack[k.index.get_indexer([t - pd.Timedelta("20s")], method="nearest")[0]]
        b = baseline.asof(t)
        if len(before.dropna()) and not np.isnan(b):
            v = before.dropna().median() - b
            on_header = (v < 0) if side_before == "stbd" else (v > 0)
            row = k.loc[t]
            tack_info.append({"time": t.isoformat(), "time_local": t.tz_convert(VENUES[venue]["tz"]).strftime("%H:%M:%S"),
                              "lat": round(float(row.lat), 6), "lon": round(float(row.lon), 6),
                              "on_header": bool(on_header), "shift_vs_mean": round(float(v), 1)})

    up_lift = lift[upwind & ~np.isnan(lift)]
    vmg = sog * np.cos(np.deg2rad(rel))
    cfg = VENUES[venue]
    tz = cfg["tz"]

    points = []
    for i, (t, row) in enumerate(k.iterrows()):
        if i % 2:  # every 10 s keeps payloads small
            continue
        points.append([
            round(row.lat, 6), round(row.lon, 6), t.tz_convert(tz).strftime("%H:%M:%S"),
            round(float(row.sog), 2), round(float(row.cog)),
            None if np.isnan(shift.iloc[i]) else round(float(shift.iloc[i] + twd0) % 360, 1),
            None if np.isnan(lift[i]) else round(float(lift[i]), 1),
            bool(upwind[i]),
        ])

    station = _station_overlay(venue, k.index[0], k.index[-1])
    n_on_header = sum(1 for t in tack_info if t["on_header"])
    summary = {
        "start_local": k.index[0].tz_convert(tz).strftime("%Y-%m-%d %H:%M"),
        "duration_min": round((k.index[-1] - k.index[0]).total_seconds() / 60),
        "inferred_twd": round(twd0), "half_tack_angle": round(half, 1),
        "pct_upwind": round(100 * float(upwind.mean()), 1),
        "pct_upwind_lifted": round(100 * float(np.mean(up_lift > 0)), 1) if len(up_lift) else None,
        "n_tacks": len(tacks), "n_tacks_on_header": n_on_header,
        "shift_range_p10_p90": [round(float(np.nanpercentile(shift, 10)), 1), round(float(np.nanpercentile(shift, 90)), 1)] if shift.notna().any() else None,
        "max_sog": round(float(np.nanpercentile(sog, 98)), 1),
        "avg_upwind_vmg": round(float(np.nanmean(vmg[upwind])), 2) if upwind.any() else None,
        "station_median_dir": station["median_dir"] if station else None,
    }
    return {
        "summary": summary,
        "points_fields": ["lat", "lon", "time", "sog", "cog", "twd_est", "lift", "upwind"],
        "points": points,
        "twd_series": [[t.tz_convert(tz).strftime("%H:%M"), None if np.isnan(v) else round(float(v + twd0) % 360, 1)]
                       for t, v in shift.resample("1min").median().items()],
        "tacks": tack_info,
        "station": station,
        "insights": _insights(summary, tack_info),
    }


def _station_overlay(venue, t0, t1):
    cfg = VENUES[venue]
    try:
        frames, grid, local = venue_frames(venue)
        prim = frames[cfg["primary"]]
        seg = prim.loc[t0 - pd.Timedelta("10min"): t1 + pd.Timedelta("10min")]
        if seg["WDIR"].notna().sum() < 2:
            from .data import fetch_realtime, resample_10min
            rt = fetch_realtime(cfg["primary"])
            if rt is None:
                return None
            seg = resample_10min(rt).loc[t0 - pd.Timedelta("10min"): t1 + pd.Timedelta("10min")]
            if seg["WDIR"].notna().sum() < 2:
                return None
        tz = cfg["tz"]
        return {
            "name": cfg["stations"][cfg["primary"]]["name"],
            "series": [[t.tz_convert(tz).strftime("%H:%M"), None if np.isnan(d) else round(float(d)),
                        None if np.isnan(s) else round(float(s), 1)] for t, d, s in zip(seg.index, seg["WDIR"], seg["WSPD_KT"])],
            "median_dir": round(float(circ_mean(seg["WDIR"].dropna().values))),
        }
    except Exception:
        return None


def _insights(s, tacks):
    out = []
    if s["pct_upwind_lifted"] is not None:
        out.append(f"Upwind, the boat was on the lifted tack {s['pct_upwind_lifted']}% of the time. "
                   "Well-sailed beats in oscillating breeze usually aim well above 50%.")
    if tacks:
        pct = round(100 * s["n_tacks_on_header"] / len(tacks))
        out.append(f"{s['n_tacks_on_header']} of {len(tacks)} tacks ({pct}%) were made on a header, "
                   "meaning the boat tacked when the wind turned against it, which is usually the right call.")
    if s["shift_range_p10_p90"]:
        lo, hi = s["shift_range_p10_p90"]
        out.append(f"GPS-inferred wind swung between {lo:+}° and {hi:+}° around {s['inferred_twd']}° "
                   "(10th to 90th percentile).")
    if s["station_median_dir"] is not None:
        d = wrap180(s["inferred_twd"] - s["station_median_dir"])
        out.append(f"The track-inferred wind ({s['inferred_twd']}°) differs from the Newport harbor station "
                   f"({s['station_median_dir']}°) by {float(d):+.0f}°. Some of this is real geographic "
                   "difference and some is current and leeway in the GPS course.")
    return out


# ---------- persistence and the venue lift map ----------

def save(result: dict, name: str, venue: str) -> str:
    TRACK_DIR.mkdir(parents=True, exist_ok=True)
    tid = uuid.uuid4().hex[:10]
    rec = {"id": tid, "name": name, "venue": venue,
           "uploaded": datetime.now(timezone.utc).isoformat(timespec="seconds"), **result}
    (TRACK_DIR / f"{tid}.json").write_text(json.dumps(rec))
    return tid


def list_tracks(venue: str | None = None) -> list[dict]:
    out = []
    for p in sorted(TRACK_DIR.glob("*.json")) if TRACK_DIR.exists() else []:
        r = json.loads(p.read_text())
        if venue and r.get("venue") != venue:
            continue
        out.append({"id": r["id"], "name": r["name"], "venue": r["venue"], "uploaded": r["uploaded"],
                    "start_local": r["summary"]["start_local"], "inferred_twd": r["summary"]["inferred_twd"]})
    return sorted(out, key=lambda r: r["start_local"], reverse=True)


def get_track(tid: str) -> dict:
    p = TRACK_DIR / f"{tid}.json"
    if not p.exists() or not tid.isalnum():
        raise FileNotFoundError(tid)
    return json.loads(p.read_text())


def delete_track(tid: str):
    p = TRACK_DIR / f"{tid}.json"
    if tid.isalnum() and p.exists():
        p.unlink()


def lift_map(venue: str, direction: float | None, tol: float = 30, cell_m: float = 250) -> dict:
    """Grid of where the wind tends to be veered/backed, from saved debrief tracks.

    Each upwind point's GPS-inferred wind is compared with a reference that removes the shifts
    everyone felt at that moment:
      * Boats in the same race (same date, overlapping times): the fleet median wind for that minute.
      * A boat sailing alone: its own 10-minute rolling median (removes the trend, not the oscillation).
    Averaging the remainder per grid cell across sessions shows persistent geographic shifts:
    positive = wind is more to the right (veered) in that spot.
    """
    lat0 = VENUES[venue]["center"][0]
    dlat = cell_m / 111320.0
    dlon = cell_m / (111320.0 * np.cos(np.deg2rad(lat0)))

    rows = []
    for meta in list_tracks(venue):
        r = get_track(meta["id"])
        twd0 = r["summary"]["inferred_twd"]
        if direction is not None and abs(float(wrap180(twd0 - direction))) > tol:
            continue
        date = r["summary"]["start_local"][:10]
        for lat, lon, t, _, _, twd, _, up in r["points"]:
            if up and twd is not None:
                rows.append((meta["id"], date, t, lat, lon, float(twd)))
    if not rows:
        return {"sessions_used": 0, "races_with_fleet": 0, "cell_m": cell_m, "cells": []}
    df = pd.DataFrame(rows, columns=["tid", "date", "time", "lat", "lon", "twd"])
    df["minute"] = df["date"] + " " + df["time"].str[:5]
    # Unwrap directions around each session's circular mean before taking medians.
    ref_by_tid = df.groupby("tid")["twd"].apply(lambda x: float(circ_mean(x.values)))
    df["rel"] = wrap180(df["twd"] - df["tid"].map(ref_by_tid))
    df["abs_unwrapped"] = df["tid"].map(ref_by_tid) + df["rel"]
    df["boats"] = df.groupby("minute")["tid"].transform("nunique")
    # Comparison groups: boats sailing at the same minute, or (for a lone boat) its own 10-minute window.
    solo_key = df["tid"] + "|" + df["date"] + " " + df["time"].str[:4]  # HH:M -> 10-minute bin
    df["group"] = np.where(df["boats"] >= 2, df["minute"], solo_key)
    df["ci"] = np.floor(df["lat"] / dlat).astype(int)
    df["cj"] = np.floor(df["lon"] / dlon).astype(int)
    df["cell"] = list(zip(df["ci"], df["cj"]))
    df = df[df.groupby("group")["cell"].transform("nunique") >= 2]  # a group in one cell says nothing about space
    if df.empty:
        return {"sessions_used": 0, "races_with_fleet": 0, "cell_m": cell_m, "cells": []}

    # Solve for a wind offset per cell, f, such that within every group
    #   twd_i - mean_group(twd) ~= f[cell_i] - mean_group(f[cell])
    # This cancels shifts everyone felt at that moment and keeps only the geographic part.
    # Aggregate to (group, cell) rows first so the system stays small.
    agg = df.groupby(["group", "cell"]).agg(twd=("abs_unwrapped", "mean"), n=("twd", "size")).reset_index()
    cells_idx = {c: i for i, c in enumerate(sorted(agg["cell"].unique()))}
    agg["c"] = agg["cell"].map(cells_idx)
    agg["gw"] = agg.groupby("group")["n"].transform("sum")
    agg["twd_dm"] = agg["twd"] - (agg["twd"] * agg["n"]).groupby(agg["group"]).transform("sum") / agg["gw"]
    from scipy.sparse import coo_matrix
    from scipy.sparse.linalg import lsqr
    rows, cols, vals = [], [], []
    for gi, (_, g) in enumerate(agg.groupby("group")):
        w = g["n"].values / g["n"].values.sum()
        for r_i, (ri, ci) in enumerate(zip(g.index, g["c"].values)):
            # Row ri: e_ci - sum_j w_j e_cj
            rows.append(ri); cols.append(ci); vals.append(1.0)
            for cj, wj in zip(g["c"].values, w):
                rows.append(ri); cols.append(cj); vals.append(-wj)
    sw = np.sqrt(agg["n"].values.astype(float))  # weight rows by points behind them
    A = coo_matrix((np.array(vals) * sw[np.array(rows)], (rows, cols)), shape=(len(agg), len(cells_idx))).tocsr()
    b = agg["twd_dm"].values * sw
    f = lsqr(A, b, damp=0.1)[0]
    f -= np.average(f, weights=agg.groupby("c")["n"].sum().reindex(range(len(f))).fillna(0).values + 1e-9)

    stats = df.groupby("cell").agg(n_points=("twd", "size"), n_sessions=("tid", "nunique"))
    cells = []
    for c, i in cells_idx.items():
        npts = int(stats.loc[[c], "n_points"].iloc[0])
        if npts < 20:  # too few points to say anything about this spot
            continue
        ci, cj = c
        cells.append({"lat0": ci * dlat, "lon0": cj * dlon, "lat1": (ci + 1) * dlat, "lon1": (cj + 1) * dlon,
                      "shift": round(float(f[i]), 1), "n_points": npts,
                      "n_sessions": int(stats.loc[[c], "n_sessions"].iloc[0])})
    races_with_fleet = int(df[df["boats"] >= 2]["date"].nunique())
    return {"sessions_used": int(df["tid"].nunique()), "races_with_fleet": races_with_fleet, "cell_m": cell_m, "cells": cells}
