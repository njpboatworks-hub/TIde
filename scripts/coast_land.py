"""Clip OpenStreetMap-derived land polygons (npm package @geo-maps/earth-lands-10m, ODbL data)
to the US East Coast and rasterize a land/water grid.

Run from a folder containing npm/lands/package/map.geo.json (npm pack @geo-maps/earth-lands-10m, then untar).
Writes coast_land.json and coast_raster.json; copy both into data/ and run
python -m scripts.export_static --coast
"""
import json, time, base64, numpy as np
from shapely.geometry import shape, box, mapping, Polygon
import shapely
from PIL import Image, ImageDraw
t = time.time()
d = json.load(open("npm/lands/package/map.geo.json"))
BB = (-82.0, 24.0, -65.5, 45.8)
bb = box(*BB)
polys = []
for g in d["geometries"]:
    g = shape(g)
    for p in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
        if not p.intersects(bb):
            continue
        q = shapely.clip_by_rect(p, *BB)  # fast, tolerant of invalid input
        for r in (q.geoms if hasattr(q, "geoms") else [q]):
            if r.geom_type == "Polygon" and not r.is_empty and r.area > 2e-6:
                polys.append(Polygon(r.exterior))
print("clipped", len(polys), "polygons", round(time.time() - t, 1), "s", flush=True)

def rnd(ring): return [[round(x, 4), round(y, 4)] for x, y in ring]
out = []
for p in polys:
    s = p.simplify(0.003, preserve_topology=True)
    if s.is_empty or s.area < 4e-6: continue
    out.append([rnd(s.exterior.coords)])
txt = json.dumps({"type": "MultiPolygon", "coordinates": out}, separators=(",", ":"))
open("coast_land.json", "w").write(txt)
print("land", len(out), "polys", len(txt) // 1024, "KB", flush=True)

res = 0.006
nx = int(np.ceil((BB[2] - BB[0]) / res)); ny = int(np.ceil((BB[3] - BB[1]) / res))
img = Image.new("1", (nx, ny), 0)
dr = ImageDraw.Draw(img)
for p in polys:  # full-detail rings for the raster
    pts = [((x - BB[0]) / res, (y - BB[1]) / res) for x, y in p.exterior.coords]  # row 0 = southern edge
    dr.polygon(pts, fill=1)
cells = np.array(img, dtype=bool)
bits = np.packbits(cells.ravel())
json.dump({"lon0": BB[0], "lat0": BB[1], "res": res, "nx": nx, "ny": ny, "bits": base64.b64encode(bits.tobytes()).decode()},
          open("coast_raster.json", "w"), separators=(",", ":"))
print("raster", nx, ny, "land share", round(cells.mean(), 3), "total", round(time.time() - t, 1), "s", flush=True)
# Spot checks: (lat, lon, expected)
def land(lat, lon):
    i = int((lon - BB[0]) / res); j = int((lat - BB[1]) / res); return bool(cells[j, i])
for name, lat, lon, exp in [("Newport town", 41.49, -71.31, True), ("RI Sound", 41.40, -71.40, False), ("Chesapeake mid-bay", 38.5, -76.4, False),
                            ("Washington DC", 38.9, -77.03, True), ("Atlantic off Hatteras", 35.0, -75.0, False), ("Orlando", 28.54, -81.38, True),
                            ("Gulf of Maine", 43.0, -69.0, False), ("Boston Common", 42.355, -71.066, True)]:
    print(f"  {name:22s} land={land(lat, lon)} expected={exp}")
