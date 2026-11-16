"""Download, parse and cache NOAA NDBC standard meteorological data."""
from __future__ import annotations

import gzip
import io
import urllib.error
import urllib.request

import numpy as np
import pandas as pd

from .config import CACHE_DIR, NDBC_HIST_URL, NDBC_REALTIME_URL, RAW_DIR, VENUES, YEARS

MISSING = {"WDIR": 999, "WSPD": 99.0, "GST": 99.0, "PRES": 9999.0, "ATMP": 999.0, "WTMP": 999.0}
KEEP = ["WDIR", "WSPD", "GST", "PRES", "ATMP", "WTMP"]
MS_TO_KT = 1.943844


def _fetch(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": "wind-analytics-client"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def parse_stdmet(text: str) -> pd.DataFrame:
    """Parse an NDBC stdmet text file (historical or realtime2 format)."""
    lines = [l for l in text.splitlines() if l.strip()]
    header = lines[0].lstrip("#").split()
    rows = [l.split() for l in lines if not l.startswith("#")]
    df = pd.DataFrame(rows, columns=header[: len(rows[0])])
    # Realtime files call the column "MM" for month and "mm" for minute, same as historical.
    df = df.rename(columns={"YY": "YYYY"})
    ts = pd.to_datetime(
        dict(year=df["YYYY"].astype(int), month=df["MM"].astype(int), day=df["DD"].astype(int),
             hour=df["hh"].astype(int), minute=df["mm"].astype(int)),
        utc=True,
    )
    out = pd.DataFrame(index=ts)
    for col in KEEP:
        if col in df.columns:
            v = pd.to_numeric(df[col], errors="coerce").values
            v = np.where((v >= MISSING[col]) | np.isnan(v), np.nan, v)
            out[col] = v
        else:
            out[col] = np.nan
    out.index.name = "time"
    out = out[~out.index.duplicated()].sort_index()
    out["WSPD_KT"] = out["WSPD"] * MS_TO_KT
    out["GST_KT"] = out["GST"] * MS_TO_KT
    return out


def download_station_year(sid: str, year: int) -> pd.DataFrame | None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{sid}h{year}.txt.gz"
    if not path.exists():
        blob = _fetch(NDBC_HIST_URL.format(sid=sid, year=year))
        if blob is None:
            return None
        path.write_bytes(blob)
    text = gzip.decompress(path.read_bytes()).decode("ascii", errors="ignore")
    return parse_stdmet(text)


def resample_10min(df: pd.DataFrame) -> pd.DataFrame:
    """Resample to 10-minute bins, averaging wind direction as a vector."""
    rad = np.deg2rad(df["WDIR"])
    tmp = pd.DataFrame(
        {
            "u": np.sin(rad) * df["WSPD_KT"],
            "v": np.cos(rad) * df["WSPD_KT"],
            "WSPD_KT": df["WSPD_KT"],
            "GST_KT": df["GST_KT"],
            "PRES": df["PRES"],
            "ATMP": df["ATMP"],
            "WTMP": df["WTMP"],
        },
        index=df.index,
    )
    r = tmp.resample("10min").mean()
    r["WDIR"] = (np.rad2deg(np.arctan2(r["u"], r["v"])) + 360) % 360
    # Hourly stations: forward-fill up to 50 minutes so all stations share a 10-minute grid.
    r = r.ffill(limit=5)
    return r.drop(columns=["u", "v"])


def load_station(sid: str, refresh: bool = False) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{sid}.pkl"
    if cache.exists() and not refresh:
        return pd.read_pickle(cache)
    parts = []
    for y in YEARS:
        d = download_station_year(sid, y)
        if d is not None and len(d):
            parts.append(resample_10min(d))
    if not parts:
        raise RuntimeError(f"No data for station {sid}")
    df = pd.concat(parts).sort_index()
    df = df[~df.index.duplicated()]
    df.to_pickle(cache)
    return df


def load_venue(venue: str = "newport", refresh: bool = False) -> dict[str, pd.DataFrame]:
    cfg = VENUES[venue]
    return {sid: load_station(sid, refresh) for sid in cfg["stations"]}


def fetch_realtime(sid: str) -> pd.DataFrame | None:
    """Last ~45 days of observations from NDBC realtime2 feed."""
    blob = _fetch(NDBC_REALTIME_URL.format(SID=sid.upper()))
    if blob is None:
        return None
    df = parse_stdmet(blob.decode("ascii", errors="ignore"))  # "MM" (missing) coerces to NaN
    return df


if __name__ == "__main__":
    import sys

    v = sys.argv[1] if len(sys.argv) > 1 else "newport"
    for sid, df in load_venue(v, refresh="--refresh" in sys.argv).items():
        print(f"{sid}: {len(df):,} rows {df.index.min()} -> {df.index.max()}, "
              f"wind coverage {df['WDIR'].notna().mean():.0%}")
