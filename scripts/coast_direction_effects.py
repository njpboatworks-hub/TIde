"""How each East Coast station's wind differs from the regional wind, by regional wind direction.

For every hour (racing season May-Oct, 9:00-18:00 local, 2019-2024):
  regional wind at a station = Gaussian-weighted (sigma 60 km, within 150 km) vector mean of the
  OTHER stations' winds at that hour.
Then for each station and each of 16 regional-direction sectors:
  off[k]   circular mean of (station direction - regional direction), degrees (+ = station is veered / more right)
  ratio[k] median of station speed / regional speed
  cons[k]  consistency 0..1 of the offset (1 = the same bend every time)
  n[k]     number of hours
Only hours with regional wind >= 6 kt and station wind >= 3 kt are used (direction is noise in light air).

Writes data/coast_direction_effects.json. Usage: python -m scripts.coast_direction_effects
"""
from __future__ import annotations

import gzip
import json
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from backend.config import DATA_DIR
from backend.data import NDBC_HIST_URL, parse_stdmet
from scripts.coast_climatology import YEARS, fetch

CACHE = DATA_DIR / "coast_hourly.npz"
OUT = DATA_DIR / "coast_direction_effects.json"
SIGMA_KM, CUTOFF_KM, MIN_REG_KT, MIN_STN_KT, NSECT = 60.0, 150.0, 6.0, 3.0, 16


def hourly(sid: str) -> pd.DataFrame | None:
    if sid.startswith("asos_"):  # coastal airports: cached by scripts.coast_asos
        f = DATA_DIR / "asos" / f"{sid}.npz"
        if not f.exists():
            return None
        z = np.load(f)
        return pd.DataFrame({"u": z["u"], "v": z["v"], "s": z["s"]}, index=pd.to_datetime(z["t"], utc=True))
    parts = []
    for y in YEARS:
        blob = fetch(NDBC_HIST_URL.format(sid=sid, year=y))
        if not blob:
            continue
        try:
            df = parse_stdmet(gzip.decompress(blob).decode("ascii", "ignore"))[["WDIR", "WSPD_KT"]].dropna()
        except Exception:
            continue
        if len(df):
            parts.append(df)
    if not parts:
        return None
    df = pd.concat(parts)
    r = np.deg2rad(df["WDIR"])
    return pd.DataFrame({"u": np.sin(r) * df["WSPD_KT"], "v": np.cos(r) * df["WSPD_KT"], "s": df["WSPD_KT"]}).resample("1h").mean()


def load_matrix(stations):
    """Hourly matrix for all stations; rows already in the cache are reused, only new stations are fetched."""
    ids = [s["id"] for s in stations]
    idx = pd.date_range(f"{YEARS[0]}-01-01", f"{YEARS[-1]}-12-31 23:00", freq="1h", tz="UTC")
    loc = idx.tz_convert("America/New_York")
    idx = idx[(loc.month >= 5) & (loc.month <= 10) & (loc.hour >= 9) & (loc.hour <= 18)]
    U = np.full((len(ids), len(idx)), np.nan, dtype=np.float32)
    V, S = U.copy(), U.copy()
    old = {}
    if CACHE.exists():
        z = np.load(CACHE)
        old = {sid: k for k, sid in enumerate(z["ids"])}
        if z["U"].shape[1] == len(idx):
            for i, sid in enumerate(ids):
                if sid in old:
                    U[i], V[i], S[i] = z["U"][old[sid]], z["V"][old[sid]], z["S"][old[sid]]
    todo = [i for i, sid in enumerate(ids) if sid not in old]
    print(f"  {len(ids) - len(todo)} stations from cache, fetching {len(todo)}", flush=True)
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, h in zip(todo, ex.map(lambda i: hourly(ids[i]), todo)):
            if h is not None:
                h = h.reindex(idx)
                U[i], V[i], S[i] = h["u"].values, h["v"].values, h["s"].values
    np.savez_compressed(CACHE, ids=np.array(ids), U=U, V=V, S=S)
    return U, V, S


def main():
    stations = json.loads((DATA_DIR / "coast_stations.json").read_text())["stations"]
    U, V, S = load_matrix(stations)
    lat = np.array([s["lat"] for s in stations]); lon = np.array([s["lon"] for s in stations])
    dy = (lat[:, None] - lat[None, :]) * 111.32
    dx = (lon[:, None] - lon[None, :]) * 111.32 * np.cos(np.deg2rad((lat[:, None] + lat[None, :]) / 2))
    dist = np.hypot(dx, dy)
    W = np.exp(-(dist ** 2) / (2 * SIGMA_KM ** 2)) * (dist <= CUTOFF_KM)
    np.fill_diagonal(W, 0.0)

    valid = ~np.isnan(U)
    U0, V0 = np.nan_to_num(U), np.nan_to_num(V)
    wsum = W @ valid.astype(np.float32)
    RU = (W @ U0) / np.where(wsum > 0, wsum, np.nan)
    RV = (W @ V0) / np.where(wsum > 0, wsum, np.nan)
    nref = (W > 0.05).astype(np.float32) @ valid.astype(np.float32)  # stations meaningfully contributing
    rdir = (np.degrees(np.arctan2(RU, RV)) + 360) % 360
    rspd = np.hypot(RU, RV)
    sdir = (np.degrees(np.arctan2(U, V)) + 360) % 360

    out = []
    for i, st in enumerate(stations):
        ok = valid[i] & (nref[i] >= 2) & (rspd[i] >= MIN_REG_KT) & (S[i] >= MIN_STN_KT)
        if ok.sum() < 200:
            continue
        rd, sd = rdir[i][ok], sdir[i][ok]
        off = ((sd - rd + 180) % 360) - 180
        ratio = S[i][ok] / rspd[i][ok]
        sect = np.floor(((rd + 360 / NSECT / 2) % 360) / (360 / NSECT)).astype(int)
        O, R, C, N = [], [], [], []
        for k in range(NSECT):
            m = sect == k
            n = int(m.sum())
            if n < 30:
                O.append(None); R.append(None); C.append(None); N.append(n); continue
            a = np.deg2rad(off[m])
            mu = np.degrees(np.arctan2(np.sin(a).mean(), np.cos(a).mean()))
            O.append(round(float(mu), 1))
            R.append(round(float(np.median(ratio[m])), 2))
            C.append(round(float(np.hypot(np.sin(a).mean(), np.cos(a).mean())), 2))
            N.append(n)
        out.append({"id": st["id"], "off": O, "ratio": R, "cons": C, "n": N})
    OUT.write_text(json.dumps({"sectors": NSECT, "sigma_km": SIGMA_KM, "min_regional_kt": MIN_REG_KT,
                               "season": "May-Oct, 9:00-18:00 local", "stations": out}, separators=(",", ":")))
    print(f"wrote {OUT}: {len(out)} stations with direction effects")


if __name__ == "__main__":
    main()
