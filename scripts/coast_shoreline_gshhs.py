"""High-resolution East Coast shoreline from GSHHS full resolution (GSHHG, Wessel & Smith, LGPL),
as packaged in the PyPI package basemap-data-hires (mpl_toolkits/basemap_data/gshhs_f.dat + gshhsmeta_f.dat).

Writes into an output folder:
  tiles/<lat>_<lon>.json   1x1 degree land tiles, ~10 m simplification, for zoomed-in maps
  tiles/index.json         list of tile keys
  coast_land.json          simplified land (~150 m) for zoomed-out maps
  coast_raster.json        bit-packed land/water grid (~600 m cells) for the land-effect model

Usage: python -m scripts.coast_shoreline_gshhs <basemap_data_dir> <out_dir>
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time

import numpy as np
import shapely
from PIL import Image, ImageDraw
from shapely.geometry import Polygon, box

from scripts.region import BB


def read_gshhs(src: str, BB=BB) -> list[Polygon]:
    data = open(os.path.join(src, "gshhs_f.dat"), "rb").read()
    polys = []
    for line in open(os.path.join(src, "gshhsmeta_f.dat")):
        f = line.split()
        level, npts, south, north, off, nbytes = int(f[0]), int(f[2]), float(f[3]), float(f[4]), int(f[5]), int(f[6])
        if level != 1 or north < BB[1] or south > BB[3] or npts < 4:
            continue  # level 1 = land/sea boundary; skip lakes and islands in lakes
        xy = np.frombuffer(data, dtype="<f4", count=nbytes // 4, offset=off).reshape(-1, 2).astype(np.float64)
        lon = np.where(xy[:, 0] > 180, xy[:, 0] - 360, xy[:, 0])
        if lon.max() < BB[0] or lon.min() > BB[2]:
            continue
        p = Polygon(np.column_stack([lon, xy[:, 1]]))
        q = shapely.clip_by_rect(p, *BB)
        for r in (q.geoms if hasattr(q, "geoms") else [q]):
            if r.geom_type == "Polygon" and not r.is_empty:
                polys.append(Polygon(r.exterior))
    return polys


def ring(p: Polygon, nd=5):
    return [[[round(x, nd), round(y, nd)] for x, y in p.exterior.coords]]


def main(src: str, out: str, BB=BB):
    t = time.time()
    polys = read_gshhs(src, BB)
    print(f"{len(polys)} land polygons, {sum(len(p.exterior.coords) for p in polys):,} vertices ({time.time() - t:.0f}s)", flush=True)
    os.makedirs(os.path.join(out, "tiles"), exist_ok=True)
    tree = shapely.STRtree(polys)
    index, total, full_land = [], 0, []
    for lat in range(int(BB[1]), int(np.ceil(BB[3]))):
        for lon in range(int(BB[0]), int(np.ceil(BB[2]))):
            rings = []
            for i in tree.query(box(lon, lat, lon + 1, lat + 1)):
                q = shapely.clip_by_rect(polys[i], lon, lat, lon + 1, lat + 1)
                for r in (q.geoms if hasattr(q, "geoms") else [q]):
                    if r.geom_type != "Polygon" or r.is_empty or r.area < 5e-9:
                        continue
                    s = r.simplify(0.0001, preserve_topology=True)
                    if not s.is_empty and s.geom_type == "Polygon":
                        rings.append(ring(s))
            if len(rings) == 1 and Polygon(rings[0][0]).area >= 0.999:
                full_land.append(f"{lat}_{lon}")  # solid land: the page fills it, no file needed
                continue
            if rings:
                key = f"{lat}_{lon}"
                txt = json.dumps({"type": "MultiPolygon", "coordinates": rings}, separators=(",", ":"))
                open(os.path.join(out, "tiles", key + ".json"), "w").write(txt)
                index.append(key); total += len(txt)
    json.dump({"tiles": index, "land": full_land}, open(os.path.join(out, "tiles", "index.json"), "w"))
    print(f"{len(index)} tile files + {len(full_land)} solid-land tiles, {total // 1024:,} KB total ({time.time() - t:.0f}s)", flush=True)

    coarse = []
    for p in polys:
        if p.area < 4e-6:
            continue
        s = p.simplify(0.0015, preserve_topology=True)
        if not s.is_empty and s.geom_type == "Polygon":
            coarse.append(ring(s, 4))
    txt = json.dumps({"type": "MultiPolygon", "coordinates": coarse}, separators=(",", ":"))
    open(os.path.join(out, "coast_land.json"), "w").write(txt)
    print(f"overview coastline {len(txt) // 1024:,} KB", flush=True)

    res = 0.006
    nx, ny = int(np.ceil((BB[2] - BB[0]) / res)), int(np.ceil((BB[3] - BB[1]) / res))
    img = Image.new("1", (nx, ny), 0)
    dr = ImageDraw.Draw(img)
    for p in polys:
        dr.polygon([((x - BB[0]) / res, (y - BB[1]) / res) for x, y in p.exterior.coords], fill=1)
    cells = np.array(img, dtype=bool)
    json.dump({"lon0": BB[0], "lat0": BB[1], "res": res, "nx": nx, "ny": ny,
               "bits": base64.b64encode(np.packbits(cells.ravel()).tobytes()).decode()},
              open(os.path.join(out, "coast_raster.json"), "w"), separators=(",", ":"))

    def land(lat, lon):
        j, i = int((lat - BB[1]) / res), int((lon - BB[0]) / res)
        return bool(cells[j, i]) if 0 <= j < ny and 0 <= i < nx else None  # None: outside this region
    for name, la, lo, exp in [("Newport town", 41.49, -71.31, True), ("RI Sound", 41.40, -71.40, False),
                              ("Chesapeake mid-bay", 38.5, -76.4, False), ("Washington DC", 38.9, -77.03, True),
                              ("off Hatteras", 35.0, -75.0, False), ("Orlando", 28.54, -81.38, True),
                              ("Gulf of Maine", 43.0, -69.0, False), ("Boston Common", 42.355, -71.066, True)]:
        print(f"  check {name:20s} land={land(la, lo)} expected={exp}")
    print(f"done ({time.time() - t:.0f}s)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
