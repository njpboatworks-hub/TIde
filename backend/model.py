"""Machine-learning shift forecaster.

Predicts how many degrees the wind at the primary (race-course reference)
station will shift over the next N hours, given current readings from all
venue stations. Gradient-boosted trees with quantile models for an 80% range.

Trained on 2019-2023, evaluated on 2024 (never seen in training), and compared
against two baselines: "no change" (persistence) and climatology.
"""
from __future__ import annotations

import json
import pickle

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from .analysis import venue_frames, wrap180
from .config import CACHE_DIR, VENUES

HORIZON_H = 2
MIN_SPEED_KT = 5.0  # direction is unreliable in very light air


def _vec_mean_dir(df, window):
    r = np.deg2rad(df["WDIR"])
    u = (np.sin(r) * df["WSPD_KT"]).rolling(window, min_periods=max(1, window // 2)).mean()
    v = (np.cos(r) * df["WSPD_KT"]).rolling(window, min_periods=max(1, window // 2)).mean()
    return (np.rad2deg(np.arctan2(u, v)) + 360) % 360


def build_features(frames: dict, cfg: dict, local_index: pd.DatetimeIndex) -> pd.DataFrame:
    """Features at every 10-minute timestamp. Uses only data at or before each time."""
    prim_id = cfg["primary"]
    X = pd.DataFrame(index=frames[prim_id].index)
    for sid, df in frames.items():
        d30 = _vec_mean_dir(df, 3)
        X[f"{sid}_sin"] = np.sin(np.deg2rad(d30))
        X[f"{sid}_cos"] = np.cos(np.deg2rad(d30))
        X[f"{sid}_spd"] = df["WSPD_KT"].rolling(3, min_periods=1).mean()
        X[f"{sid}_gst"] = df["GST_KT"].rolling(3, min_periods=1).mean()
        if sid == prim_id:
            X["prim_dir"] = d30
            d_1h = _vec_mean_dir(df, 3).shift(6)
            X["prim_chg_1h"] = wrap180(d30 - d_1h)
            X["prim_spd_chg_1h"] = X[f"{sid}_spd"] - X[f"{sid}_spd"].shift(6)
        else:
            X[f"{sid}_minus_prim"] = np.nan  # filled below once prim_dir exists
    for sid in frames:
        if sid != prim_id:
            X[f"{sid}_minus_prim"] = wrap180(_vec_mean_dir(frames[sid], 3) - X["prim_dir"])
    X["land_sea_dT"] = frames[cfg["land_temp"]]["ATMP"].rolling(6, min_periods=1).mean() - \
        frames[cfg["water_temp"]]["WTMP"].rolling(6, min_periods=1).mean()
    pres = frames[cfg["gradient"]]["PRES"].combine_first(frames[prim_id]["PRES"])
    X["pres_tend_3h"] = pres - pres.shift(18)
    X["hour"] = local_index.hour + local_index.minute / 60
    X["doy_sin"] = np.sin(2 * np.pi * local_index.dayofyear / 365.25)
    X["doy_cos"] = np.cos(2 * np.pi * local_index.dayofyear / 365.25)
    return X


FEATURES_EXCLUDE = {"prim_dir"}


def _target(frames, cfg, horizon_h):
    prim = frames[cfg["primary"]]
    d_now = _vec_mean_dir(prim, 3)
    d_fut = d_now.shift(-int(horizon_h * 6))
    spd_fut = prim["WSPD_KT"].rolling(3, min_periods=1).mean().shift(-int(horizon_h * 6))
    return pd.Series(wrap180(d_fut - d_now), index=prim.index), spd_fut


def train(venue: str = "newport", horizon_h: float = HORIZON_H, verbose=True) -> dict:
    cfg = VENUES[venue]
    frames, grid, local = venue_frames(venue)
    X = build_features(frames, cfg, local)
    y, spd_fut = _target(frames, cfg, horizon_h)

    m = (
        np.isin(local.month, cfg["season_months"])
        & (local.hour >= cfg["day_start_hour"]) & (local.hour <= cfg["day_end_hour"] - horizon_h)
        & (local.minute % 30 == 0)
        & (X[f"{cfg['primary']}_spd"].values >= MIN_SPEED_KT)
        & (spd_fut.values >= MIN_SPEED_KT)
        & y.notna().values & X["prim_dir"].notna().values
    )
    feats = [c for c in X.columns if c not in FEATURES_EXCLUDE]
    D = X.loc[m, feats]
    Y = y[m]
    yrs = local[m].year
    test_year = int(yrs.max())
    tr, te = yrs < test_year, yrs == test_year

    common = dict(max_iter=400, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=40,
                  l2_regularization=1.0, random_state=0)
    med = HistGradientBoostingRegressor(loss="absolute_error", **common).fit(D[tr], Y[tr])
    lo = HistGradientBoostingRegressor(loss="quantile", quantile=0.1, **common).fit(D[tr], Y[tr])
    hi = HistGradientBoostingRegressor(loss="quantile", quantile=0.9, **common).fit(D[tr], Y[tr])

    yt = Y[te].values
    pred = med.predict(D[te])
    plo, phi = lo.predict(D[te]), hi.predict(D[te])

    # Climatology baseline: median shift by (month, hour) from training years.
    lt = local[m]
    clim = pd.DataFrame({"mo": lt.month, "hr": lt.hour, "y": Y.values, "tr": tr})
    table = clim[clim.tr].groupby(["mo", "hr"])["y"].median()
    clim_pred = np.array([table.get((a, b), 0.0) for a, b in zip(clim.mo[~clim.tr], clim.hr[~clim.tr])])

    big = np.abs(yt) >= 10
    metrics = {
        "venue": venue,
        "horizon_h": horizon_h,
        "train_years": sorted(set(int(v) for v in yrs[tr])),
        "test_year": test_year,
        "n_train": int(tr.sum()),
        "n_test": int(te.sum()),
        "mae_model": round(float(np.mean(np.abs(pred - yt))), 2),
        "mae_persistence": round(float(np.mean(np.abs(yt))), 2),
        "mae_climatology": round(float(np.mean(np.abs(clim_pred - yt))), 2),
        "n_big_shifts_test": int(big.sum()),
        "big_shift_direction_accuracy": round(float(np.mean(np.sign(pred[big]) == np.sign(yt[big]))), 3) if big.any() else None,
        "big_shift_direction_accuracy_climatology": round(float(np.mean(np.sign(clim_pred[big]) == np.sign(yt[big]))), 3) if big.any() else None,
        "interval_80_coverage": round(float(np.mean((yt >= plo) & (yt <= phi))), 3),
        "notes": [
            f"Target: change in 30-minute-average wind direction at {cfg['stations'][cfg['primary']]['name']} "
            f"over the next {horizon_h} h, daytime, racing season, wind >= {MIN_SPEED_KT} kt.",
            "Persistence = always predicting no shift. Climatology = typical shift for that month and hour.",
            "Big shift = actual shift of 10 degrees or more; accuracy = model got the side (left or right) correct.",
        ],
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_DIR / f"model_{venue}.pkl", "wb") as f:
        pickle.dump({"med": med, "lo": lo, "hi": hi, "features": feats, "horizon_h": horizon_h}, f)
    (CACHE_DIR / f"model_{venue}_metrics.json").write_text(json.dumps(metrics, indent=2))
    if verbose:
        print(json.dumps(metrics, indent=2))
    return metrics


def load_model(venue: str = "newport"):
    p = CACHE_DIR / f"model_{venue}.pkl"
    if not p.exists():
        train(venue, verbose=False)
    with open(p, "rb") as f:
        return pickle.load(f)


def load_metrics(venue: str = "newport") -> dict:
    p = CACHE_DIR / f"model_{venue}_metrics.json"
    if not p.exists():
        return train(venue, verbose=False)
    return json.loads(p.read_text())


def predict_from_frames(venue: str, frames: dict) -> dict | None:
    """Predict from the latest row of a set of (recent) station frames."""
    cfg = VENUES[venue]
    mdl = load_model(venue)
    idx = frames[cfg["primary"]].index
    local = idx.tz_convert(cfg["tz"])
    X = build_features(frames, cfg, local)
    valid = X["prim_dir"].notna()
    if not valid.any():
        return None
    row = X[valid].iloc[[-1]]
    D = row[mdl["features"]]
    return {
        "as_of_local": row.index[0].tz_convert(cfg["tz"]).strftime("%Y-%m-%d %H:%M"),
        "current_dir": round(float(row["prim_dir"].iloc[0])),
        "current_speed": round(float(row[f"{cfg['primary']}_spd"].iloc[0]), 1),
        "horizon_h": mdl["horizon_h"],
        "shift_median": round(float(mdl["med"].predict(D)[0]), 1),
        "shift_p10": round(float(mdl["lo"].predict(D)[0]), 1),
        "shift_p90": round(float(mdl["hi"].predict(D)[0]), 1),
    }


if __name__ == "__main__":
    train()
