"""Detailed shoreline tiles from OpenStreetMap land polygons (osmdata.openstreetmap.de,
land-polygons-split-4326.zip; data © OpenStreetMap contributors, ODbL).

OpenStreetMap's coastline is traced from aerial imagery and is accurate to a few meters, so it lines up
with satellite maps much better than GSHHS. Writes 1x1 degree tiles (holes kept: lagoons and harbors are water)
plus tiles/index.json listing tile files and solid-land tiles.

Usage: python -m scripts.coast_shoreline_osm <land-polygons-split-4326.zip> <out_dir>
"""
from __future__ import annotations

import json
import os
import sys
import time

import shapefile
import shapely
from shapely.geometry import Polygon, box, shape

from scripts.region import BB, in_region

SIMPLIFY = 0.00005  # degrees, about 5 m


def near_region(b, BB=BB, in_region=in_region) -> bool:
    lon0, lat0, lon1, lat1 = b
    if lon1 < BB[0] or lon0 > BB[2] or lat1 < BB[1] or lat0 > BB[3]:
        return False
    # keep a piece if any corner or its center is in the coastal region (pieces are at most ~1 degree)
    pts = [(lat0, lon0), (lat0, lon1), (lat1, lon0), (lat1, lon1), ((lat0 + lat1) / 2, (lon0 + lon1) / 2)]
    return any(in_region(la, lo) for la, lo in pts)


def ring_coords(r, nd=5):
    return [[round(x, nd), round(y, nd)] for x, y in r.coords]


def main(zip_path: str, out: str, BB=BB, in_region=in_region):
    t = time.time()
    rdr = shapefile.Reader(zip_path)
    polys = []
    for shp in rdr.iterShapes(bbox=list(BB)):  # skips pieces outside the box without reading them fully
        if not near_region(shp.bbox, BB, in_region):
            continue
        g = shape(shp.__geo_interface__)
        if not g.is_valid:
            g = shapely.make_valid(g)
        for p in (g.geoms if hasattr(g, "geoms") else [g]):
            if p.geom_type == "Polygon" and not p.is_empty:
                polys.append(p)
    print(f"{len(polys)} OSM land pieces near the region, "
          f"{sum(len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors) for p in polys):,} vertices ({time.time() - t:.0f}s)", flush=True)

    os.makedirs(os.path.join(out, "tiles"), exist_ok=True)
    tree = shapely.STRtree(polys)
    index, land, total, biggest = [], [], 0, 0
    for lat in range(int(BB[1]), int(BB[3]) + 1):
        for lon in range(int(BB[0]), int(BB[2]) + 1):
            tb = box(lon, lat, lon + 1, lat + 1)
            parts = []
            for i in tree.query(tb):
                q = shapely.clip_by_rect(polys[i], lon, lat, lon + 1, lat + 1)
                for r in (q.geoms if hasattr(q, "geoms") else [q]):
                    if r.geom_type == "Polygon" and not r.is_empty and r.area > 1e-9:
                        parts.append(r)
            if not parts:
                continue
            if sum(p.area for p in parts) >= 0.9999:
                land.append(f"{lat}_{lon}")
                continue
            coords = []
            for p in parts:
                s = p.simplify(SIMPLIFY, preserve_topology=True)
                if s.is_empty or s.geom_type != "Polygon":
                    continue
                coords.append([ring_coords(s.exterior)] + [ring_coords(h) for h in s.interiors if Polygon(h).area > 1e-8])
            if not coords:
                continue
            key = f"{lat}_{lon}"
            txt = json.dumps({"type": "MultiPolygon", "coordinates": coords}, separators=(",", ":"))
            open(os.path.join(out, "tiles", key + ".json"), "w").write(txt)
            index.append(key); total += len(txt); biggest = max(biggest, len(txt))
    json.dump({"tiles": index, "land": land, "source": "OpenStreetMap"}, open(os.path.join(out, "tiles", "index.json"), "w"))
    print(f"{len(index)} tile files + {len(land)} solid-land tiles, {total // 1024 // 1024} MB total, "
          f"largest {biggest // 1024} KB ({time.time() - t:.0f}s)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
