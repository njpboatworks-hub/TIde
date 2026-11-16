"""Searchable place list for the map: coastal towns (US Census 2024 Gazetteer, public domain),
NOAA tide/current station names, wind station names and featured venues.

Usage: python -m scripts.build_places <path to 2024_Gaz_place_national.txt>
Writes site/places.json as [[name, detail, lat, lon, kind], ...]  (kind: town, tide, current, wind)
"""
from __future__ import annotations

import csv
import json
import re
import sys

import numpy as np
from scipy.ndimage import binary_dilation

from backend.config import DATA_DIR, ROOT
from scripts.coast_asos import load_raster
from scripts.region import in_region

SUFFIX = re.compile(r"\s+(CDP|city|town|village|borough|municipality|city and borough|urban county|consolidated government.*|metro government.*|unified government.*|\(balance\))$", re.I)


def main(gaz_path: str):
    g = load_raster()
    water = ~g["cells"]
    near = binary_dilation(water, iterations=int(20 / (g["res"] * 111.32)))  # within ~20 km of salt water

    def coastal(lat, lon):
        i, j = int((lon - g["lon0"]) / g["res"]), int((lat - g["lat0"]) / g["res"])
        return 0 <= i < g["nx"] and 0 <= j < g["ny"] and bool(near[j, i])

    out, seen = [], set()
    with open(gaz_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    for r in rows:
        r = {k.strip(): v.strip() for k, v in r.items()}
        lat, lon = float(r["INTPTLAT"]), float(r["INTPTLONG"])
        if not in_region(lat, lon) or not coastal(lat, lon):
            continue
        name = SUFFIX.sub("", r["NAME"]).strip()
        key = (name.lower(), r["USPS"])
        if key in seen:
            continue
        seen.add(key)
        out.append([name, r["USPS"], round(lat, 4), round(lon, 4), "town", float(r["ALAND_SQMI"] or 0)])
    out.sort(key=lambda x: -x[5])  # larger places first when names tie in search
    out = [x[:5] for x in out]
    n_towns = len(out)

    tides = json.loads((ROOT / "site" / "tides.json").read_text())
    for s in tides["tides"]:
        out.append([s["name"], "NOAA tide station", s["lat"], s["lon"], "tide"])
    for s in tides["currents"]:
        out.append([s["name"], "NOAA current station", s["lat"], s["lon"], "current"])
    for s in json.loads((DATA_DIR / "coast_stations.json").read_text())["stations"]:
        out.append([re.sub(r"^\d+\s*-\s*", "", s["name"]), "wind station", round(s["lat"], 4), round(s["lon"], 4), "wind"])
    (ROOT / "site" / "places.json").write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote site/places.json: {n_towns} coastal towns, {len(out) - n_towns} stations, "
          f"{(ROOT / 'site' / 'places.json').stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1])
