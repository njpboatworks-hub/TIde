"""US harbors, bays, coves, inlets, channels, sounds, anchorages, points, islands and lighthouses for search and map labels.

Source: GeoNames US dump (download.geonames.org/export/dump/US.zip, CC BY 4.0), which carries the USGS GNIS names.
Kept: features in a Nockd US region and within ~1 km of that region's mapped salt water / Great Lakes water.
Merges into site/places.json as [name, "<Type>, <ST>", lat, lon, "place", region].
Usage: python -m scripts.us_marine_places <path to US.txt>
"""
from __future__ import annotations

import base64
import json
import sys

import numpy as np
from scipy.ndimage import binary_dilation

from backend.config import DATA_DIR, ROOT
from scripts.region import in_region as east_in
from scripts.regions_us import REGIONS

TYPES = {"HBR": "Harbor", "PRT": "Port", "ANCH": "Anchorage", "RDST": "Anchorage", "BAY": "Bay", "BAYS": "Bays", "COVE": "Cove",
         "INLT": "Inlet", "CHN": "Channel", "CHNM": "Channel", "SD": "Sound", "STRT": "Strait", "LGN": "Lagoon", "ESTY": "Estuary",
         "LTHSE": "Lighthouse", "PIER": "Pier", "CAPE": "Point", "PT": "Point", "ISL": "Island", "ISLS": "Islands", "SHOL": "Shoal", "RF": "Reef"}
RANK = {"HBR": 0, "PRT": 0, "ANCH": 1, "RDST": 1, "SD": 1, "STRT": 1, "INLT": 2, "BAY": 2, "BAYS": 2, "LGN": 2, "ESTY": 2, "CHN": 3, "CHNM": 3,
        "COVE": 3, "LTHSE": 3, "CAPE": 4, "PT": 4, "ISL": 5, "ISLS": 5, "PIER": 6, "SHOL": 6, "RF": 6}


def raster(d):
    g = json.loads((d / "coast_raster.json").read_text())
    cells = np.unpackbits(np.frombuffer(base64.b64decode(g["bits"]), dtype=np.uint8))[: g["nx"] * g["ny"]].reshape(g["ny"], g["nx"]).astype(bool)
    g["near"] = binary_dilation(~cells, iterations=2)  # water, or land within ~1 km of it
    return g


def main(path):
    regs = [("us", east_in, raster(DATA_DIR))] + [(k, REGIONS[k]["in_region"], raster(DATA_DIR / k)) for k in ("west", "lakes", "hawaii")]
    out = {k: [] for k, _, _ in regs}
    with open(path, encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) < 11 or c[7] not in TYPES:
                continue
            lat, lon = float(c[4]), float(c[5])
            for key, inside, g in regs:
                if key == "us" and REGIONS["lakes"]["in_region"](lat, lon):
                    continue
                if not inside(lat, lon):
                    continue
                i, j = int((lon - g["lon0"]) / g["res"]), int((lat - g["lat0"]) / g["res"])
                if 0 <= i < g["nx"] and 0 <= j < g["ny"] and g["near"][j, i]:
                    out[key].append((RANK[c[7]], c[1], f"{TYPES[c[7]]}, {c[10]}", round(lat, 4), round(lon, 4)))
                break
    # population per (name, state) from GeoNames populated places, to list big towns first (map labels and search)
    pop = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) > 14 and c[6] == "P" and c[14].isdigit():
                k = (c[1].lower(), c[10]); pop[k] = max(pop.get(k, 0), int(c[14]))
    pl = json.loads((ROOT / "site" / "places.json").read_text())
    towns = [p for p in pl if p[4] == "town" and (p[5] if len(p) > 5 else "us") in out]
    rest = [p for p in pl if not (p[4] == "town" and (p[5] if len(p) > 5 else "us") in out)]
    towns.sort(key=lambda p: -pop.get((p[0].lower(), p[1]), 0))  # stable: equal population keeps the old order
    print(f"ranked {len(towns)} US towns by population ({sum(1 for p in towns if pop.get((p[0].lower(), p[1]), 0))} matched)")
    pl = towns + rest
    pl = [p for p in pl if not (p[4] == "place" and (p[5] if len(p) > 5 else "us") in out)]
    n = 0
    for key, rows in out.items():
        rows.sort()
        for _, name, detail, lat, lon in rows:
            pl.append([name, detail, lat, lon, "place", key]); n += 1
        print(f"{key}: {len(rows)} marine places ({sum(1 for r in rows if r[0] == 0)} harbors and ports)")
    (ROOT / "site" / "places.json").write_text(json.dumps(pl, ensure_ascii=False, separators=(",", ":")))
    print(f"places.json: {len(pl)} entries, {(ROOT / 'site' / 'places.json').stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1])
