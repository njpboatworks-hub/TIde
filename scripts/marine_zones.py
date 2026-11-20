"""Outlines of the National Weather Service marine forecast zones (coastal and offshore), so the Cruising tab can draw
live marine warnings (small craft advisories, gales, storms...) from api.weather.gov on the map.

The alerts themselves are read live by the page; only the zone outlines are stored here, simplified to about 1 km.
Zones rarely change, so the daily job rebuilds this file only when it is more than 30 days old.

Output site/marinezones.json: {"built": iso, "z": {"ANZ236": ["Narragansett Bay", [[[lon, lat], ...], ...]], ...}}
Usage: python -m scripts.marine_zones [--if-older DAYS]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from shapely.geometry import shape

from backend.config import ROOT

API = "https://api.weather.gov/zones"
UA = {"User-Agent": "wind-analytics-prototype", "Accept": "application/geo+json"}
OUT = ROOT / "site" / "marinezones.json"


def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.load(r)
        except Exception:  # NWS API is busy at times: back off and retry
            if i == tries - 1:
                raise
            time.sleep(2 + 3 * i)


def zone(kind, zid):
    try:
        f = get(f"{API}/{kind}/{zid}")
    except Exception:
        return zid, None
    g = f.get("geometry")
    if not g:
        return zid, None
    geom = shape(g).simplify(0.01, preserve_topology=True)
    polys = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
    rings = []
    for p in polys:
        if p.is_empty or p.area < 1e-5:
            continue
        rings.append([[round(x, 3), round(y, 3)] for x, y in p.exterior.coords])
    return zid, [f.get("properties", {}).get("name", zid), rings] if rings else None


def main():
    if "--if-older" in sys.argv and OUT.exists():
        days = float(sys.argv[sys.argv.index("--if-older") + 1])
        if time.time() - OUT.stat().st_mtime < days * 86400:
            print("marine zones are recent; not rebuilt")
            return
    jobs = []
    for kind in ("coastal", "offshore"):
        for f in get(f"{API}?type={kind}")["features"]:
            jobs.append((kind, f["properties"]["id"]))
    out, t0 = {}, time.time()
    with ThreadPoolExecutor(6) as ex:
        for zid, rec in ex.map(lambda j: zone(*j), jobs):
            if rec:
                out[zid] = rec
    if len(out) < len(jobs) * 0.8:
        raise SystemExit(f"only {len(out)} of {len(jobs)} zones downloaded; keeping the previous file")
    OUT.write_text(json.dumps({"built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "z": out}, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {time.time() - t0:.0f}s): {len(out)} of {len(jobs)} zones")


if __name__ == "__main__":
    main()
