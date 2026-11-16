"""Venue and station configuration.

Station coordinates come from NOAA NDBC's station_table.txt.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"

NDBC_HIST_URL = "https://www.ndbc.noaa.gov/data/historical/stdmet/{sid}h{year}.txt.gz"
NDBC_REALTIME_URL = "https://www.ndbc.noaa.gov/data/realtime2/{SID}.txt"

YEARS = list(range(2019, 2025))  # complete years available in the NDBC archive

VENUES = {
    "newport": {
        "name": "Newport, RI",
        "tz": "America/New_York",
        "center": [41.48, 71.34 * -1],
        "zoom": 11,
        # Station used as the "on the race course" reference for shift statistics.
        "primary": "nwpr1",
        # Station used as the open-water / gradient reference (less land influence).
        "gradient": "buzm3",
        # Water-temperature source for the land/sea temperature contrast (sea breeze driver).
        "water_temp": "nwpr1",
        # Inland-ish air temperature source for the land/sea temperature contrast.
        "land_temp": "cptr1",
        "stations": {
            "nwpr1": {"name": "Newport harbor (NOAA 8452660)", "lat": 41.504, "lon": -71.326},
            "qptr1": {"name": "Quonset Point (NOAA 8454049)", "lat": 41.586, "lon": -71.407},
            "cptr1": {"name": "Conimicut Light (NOAA 8452944)", "lat": 41.717, "lon": -71.345},
            "buzm3": {"name": "Buzzards Bay tower (C-MAN)", "lat": 41.397, "lon": -71.033},
        },
        # Typical racing season and hours (local time) used for analysis.
        "season_months": [5, 6, 7, 8, 9, 10],
        "day_start_hour": 9,
        "day_end_hour": 19,
    }
}
