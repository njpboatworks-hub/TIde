"""US state borders for the maps (land borders between states only, not the coastline, which the map already draws).

Source: US Census Bureau cartographic boundary file cb_2023_us_state_5m (public domain),
www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_state_5m.zip. Shared edges between neighbouring states are
found with shapely and simplified to about 200 m. Writes site/states.json (GeoJSON MultiLineString).
Usage: python -m scripts.state_lines <path to cb_2023_us_state_5m.zip>
"""
from __future__ import annotations

import json
import sys

import shapefile
from shapely.geometry import shape, mapping
from shapely.ops import linemerge, unary_union

from backend.config import ROOT


def main(zpath):
    r = shapefile.Reader(zpath)
    names = [f[0] for f in r.fields[1:]]
    st = []
    for sr in r.shapeRecords():
        rec = dict(zip(names, sr.record))
        if rec["STUSPS"] in ("PR", "VI", "GU", "MP", "AS"):
            continue
        st.append((rec["STUSPS"], shape(sr.shape.__geo_interface__).buffer(0)))
    lines = []
    for i in range(len(st)):  # each state's edge where a later state lies right next to it (each border once)
        a = st[i][1]
        later = [b for _, b in st[i + 1:] if a.envelope.buffer(0.01).intersects(b.envelope)]
        if not later:
            continue
        x = a.boundary.intersection(unary_union(later).buffer(0.003))
        if not x.is_empty and x.length > 0.01:
            lines.append(x)
    m = linemerge(unary_union(lines)).simplify(0.002, preserve_topology=False)
    geo = mapping(m)
    rnd = lambda c: [[round(p[0], 4), round(p[1], 4)] for p in c]
    if geo["type"] == "LineString":
        coords = [rnd(geo["coordinates"])]
    else:
        coords = [rnd(c) for c in geo["coordinates"] if len(c) > 1]
    out = {"type": "Feature", "properties": {"source": "US Census Bureau cb_2023_us_state_5m"}, "geometry": {"type": "MultiLineString", "coordinates": coords}}
    (ROOT / "site" / "states.json").write_text(json.dumps(out, separators=(",", ":")))
    print(f"{len(coords)} border lines, {(ROOT / 'site' / 'states.json').stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1])
