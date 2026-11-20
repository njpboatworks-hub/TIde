"""Land shapes for the Forecast map, so land can be drawn as a light veil with a crisp coastline over the weather
colours (the way Windy shows it). Natural Earth land polygons (public domain), from the world-atlas npm package
converted to GeoJSON:

  npm install world-atlas@2.0.2 topojson-client@3.1.0
  node -e 'const fs=require("fs"),tc=require("topojson-client");for(const n of ["50m","10m"]){const t=JSON.parse(
    fs.readFileSync(`node_modules/world-atlas/land-${n}.json`));fs.writeFileSync(`land-${n}.geojson`,
    JSON.stringify(tc.feature(t,t.objects.land)))}'

Output:
  site/world/land-50m.json      [[ring, hole, ...], ...]  whole world, ~2 km detail (zoomed out)
  site/world/l10/{lat}_{lon}.json  same, 1:10m detail, clipped to 10 x 10 degree tiles (zoomed in)
  site/world/l10/index.json     list of tile keys that hold land
Usage: python -m scripts.world_land DIR_WITH_GEOJSON
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from shapely.geometry import box, shape

from backend.config import ROOT

OUT = ROOT / "site" / "world"


def polys(g, nd):
    out = []
    for p in ([g] if g.geom_type == "Polygon" else list(getattr(g, "geoms", []))):
        if p.is_empty or p.geom_type != "Polygon":
            continue
        rs = []
        for r in [p.exterior, *p.interiors]:
            c = [[round(x, nd), round(y, nd)] for x, y in r.coords]
            if len(c) >= 4:
                rs.append(c)
        if rs:
            out.append(rs)
    return out


def load(path):
    d = json.loads(Path(path).read_text())
    f = d["features"][0] if "features" in d else d
    return shape(f["geometry"]).buffer(0)


def main(src):
    src = Path(src)
    g50 = load(src / "land-50m.geojson").simplify(0.01, preserve_topology=True)
    (OUT / "land-50m.json").write_text(json.dumps(polys(g50, 2), separators=(",", ":")))
    g10 = load(src / "land-10m.geojson").simplify(0.002, preserve_topology=True)
    tiles = OUT / "l10"
    tiles.mkdir(parents=True, exist_ok=True)
    keys = []
    for la in range(-90, 90, 10):
        for lo in range(-180, 180, 10):
            part = g10.intersection(box(lo, la, lo + 10, la + 10))
            if part.is_empty:
                continue
            p = polys(part, 3)
            if not p:
                continue
            k = f"{la}_{lo}"
            (tiles / f"{k}.json").write_text(json.dumps(p, separators=(",", ":")))
            keys.append(k)
    (tiles / "index.json").write_text(json.dumps(keys))
    size = sum(f.stat().st_size for f in tiles.glob("*.json")) // 1024
    print(f"land-50m {(OUT / 'land-50m.json').stat().st_size // 1024} KB; {len(keys)} detailed tiles, {size} KB")


if __name__ == "__main__":
    main(sys.argv[1])
