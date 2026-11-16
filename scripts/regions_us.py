"""Extra US regions: West Coast, Great Lakes and Hawaii (the East and Gulf Coast region is scripts/region.py).

Each region has a bounding box, an in_region() test (what counts as this coast), a time zone rule,
the Iowa Environmental Mesonet airport networks to search, and whether NOAA publishes tide predictions there.
"""
from __future__ import annotations


def _west_in(lat, lon):
    if not (32.45 <= lat <= 49.05 and -125.0 <= lon <= -116.8):
        return False
    if lat > 35.0 and lon > -121.0:      # inland California, Oregon and Washington (Puget Sound and the Delta are west of this)
        return False
    if lat <= 35.0 and lon > -117.0:     # inland Southern California
        return False
    return True


def _lakes_in(lat, lon):
    if not (41.3 <= lat <= 49.1 and -92.4 <= lon <= -75.7):
        return False
    if lat < 43.0 and lon > -78.6:       # inland New York / Pennsylvania south of Lake Ontario, east of Lake Erie
        return False
    if lat < 41.55 and lon < -84.0:      # inland Indiana / Ohio south of Lake Michigan's tip and west of Toledo
        return lon < -86.5 and lat > 41.45
    return True


def _hawaii_in(lat, lon):
    return 18.7 <= lat <= 22.45 and -160.6 <= lon <= -154.6


def lakes_tz(lat, lon):
    """Central time on the Wisconsin, Illinois, northwest Indiana and western Lake Superior shores; Eastern elsewhere."""
    if lat < 45.9:
        if lon < -86.6 and not (lat > 45.55 and lon > -87.55):
            return "America/Chicago"
        return "America/New_York"
    if lon < -90.3 or (lon < -89.6 and lat < 48.05):
        return "America/Chicago"
    return "America/New_York"


REGIONS = {
    "west": {
        "name": "US West Coast", "BB": (-125.0, 32.45, -116.8, 49.05), "in_region": _west_in,
        "tz": lambda lat, lon: "America/Los_Angeles",
        "asos": ["CA_ASOS", "OR_ASOS", "WA_ASOS"], "states": ["CA", "OR", "WA"], "noaa_tides": True,
        "shore": "osm", "effects_sigma": 60.0, "effects_cutoff": 150.0,
    },
    "lakes": {
        "name": "Great Lakes", "BB": (-92.4, 41.3, -75.7, 49.1), "in_region": _lakes_in, "tz": lakes_tz,
        "asos": ["MN_ASOS", "WI_ASOS", "IL_ASOS", "IN_ASOS", "MI_ASOS", "OH_ASOS", "PA_ASOS", "NY_ASOS", "CA_ON_ASOS"],
        "states": ["MN", "WI", "IL", "IN", "MI", "OH", "PA", "NY"], "noaa_tides": False,
        "shore": "gshhs_lakes", "effects_sigma": 60.0, "effects_cutoff": 150.0, "min_months_frac": 0.35,
    },
    "hawaii": {
        "name": "Hawaii", "BB": (-160.6, 18.7, -154.6, 22.45), "in_region": _hawaii_in,
        "tz": lambda lat, lon: "Pacific/Honolulu",
        "asos": ["HI_ASOS"], "states": ["HI"], "noaa_tides": True,
        "shore": "osm", "effects_sigma": 60.0, "effects_cutoff": 200.0,
    },
}
