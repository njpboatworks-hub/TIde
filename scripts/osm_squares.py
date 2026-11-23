"""Adds coastal squares for more of the world's cruising grounds to data/osm_world_squares.json, which the cruising
data job (scripts.boating_places, .github/workflows/cruise-data.yml) works through: squares never fetched first.
A square is kept when the Natural Earth 1:10m coastline (site/world/l10, built by scripts.world_land) passes through it.
Squares already listed under another region are skipped.
Usage: python -m scripts.osm_squares
"""
from __future__ import annotations

import json
import math

from shapely.geometry import Polygon, box
from shapely.strtree import STRtree

from backend.config import ROOT

# name: list of (south, west, north, east) areas, in the order the job should fill them
REGIONS = {
    "Norway, Iceland and the Faroes": [(56, -25, 72, 32)],
    "Atlantic islands: Azores, Madeira, Canaries, Cape Verde": [(14, -32, 40, -12)],
    "Black Sea": [(40, 27, 47, 42)],
    "Red Sea and the Gulf": [(12, 32, 30, 44), (22, 47, 31, 57), (12, 42, 27, 60)],
    "Southeast Asia": [(-10, 95, 22, 128)],
    "Japan and Korea": [(30, 124, 46, 146)],
    "China coast, Taiwan and Hong Kong": [(18, 108, 32, 124)],
    "India, Sri Lanka and the Maldives": [(-2, 68, 24, 92)],
    "East Africa, Seychelles and Mauritius": [(-26, 32, -2, 58)],
    "South Africa and Namibia": [(-36, 12, -22, 34)],
    "West Africa": [(4, -18, 14, 10)],
    "Pacific islands": [(-24, 160, -8, 180), (-24, -180, -8, -134), (-8, 140, 2, 160)],
    "Chile and Peru": [(-56, -82, -3, -68)],
    "Colombia, Venezuela and the Guianas": [(0, -80, 13, -50)],
    "Alaska": [(54, -166, 62, -140)],
    "St Lawrence and Newfoundland": [(45, -75, 52, -52)],
}
LAT, LON = 2, 2.5


def load_land():
    d = ROOT / "site" / "world" / "l10"
    polys = []
    for k in json.loads((d / "index.json").read_text()):
        for rings in json.loads((d / f"{k}.json").read_text()):
            try:
                p = Polygon(rings[0], rings[1:]).buffer(0)
            except Exception:
                continue
            if not p.is_empty:
                polys.append(p)
    return polys


def main():
    out_f = ROOT / "data" / "osm_world_squares.json"
    W = json.loads(out_f.read_text())
    have = {tuple(b) for v in W.values() for b in v}
    land = load_land()
    tree = STRtree(land)
    added = 0
    for name, areas in REGIONS.items():
        got = []
        for s, w, n, e in areas:
            for la in range(math.floor(s / LAT) * LAT, n, LAT):
                lo = math.floor(w / LON) * LON
                while lo < e:
                    b = (la, lo, la + LAT, lo + LON)
                    lo += LON
                    if b in have:
                        continue
                    g = box(b[1], b[0], b[3], b[2])
                    hits = [land[i] for i in tree.query(g)]
                    inter = sum(p.intersection(g).area for p in hits if p.intersects(g))
                    if inter <= 0 or inter >= g.area * 0.995:  # all water or all land: no coastline here
                        continue
                    have.add(b); got.append(list(b))
        if got:
            W[name] = W.get(name, []) + got
        added += len(got)
        print(f"{name}: {len(got)} squares")
    out_f.write_text(json.dumps(W, separators=(",", ":")))
    print(f"added {added}; {sum(len(v) for v in W.values())} squares in all")


if __name__ == "__main__":
    main()
