"""Add lagoons, sounds and other inland tidal water to the US shoreline tiles.

OpenStreetMap's coastline and GSHHS both treat some big coastal lagoons as land (for example Florida's Indian River and
Banana River). This reads the water areas of the USGS National Hydrography Dataset from The National Map's cached
hydrography tiles (USGSHydroCached, public domain), keeps connected water bodies larger than MIN_KM2, and cuts them out of:
  site/tiles/<lat>_<lon>.json   detailed shoreline (and turns "solid land" tiles that contain such water into files)
  the ~600 m land raster and overview coastline of each US region (data/coast_*.json, data/<region>/coast_*.json)

Usage: python -m scripts.hydro_water           (then re-export: python -m scripts.export_static --coast and us_build <r> export)
"""
from __future__ import annotations

import base64
import io
import json
import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import shapely
from PIL import Image
from scipy.ndimage import binary_closing, label
from shapely.geometry import MultiPolygon, Polygon, box, shape

from backend.config import DATA_DIR, ROOT
from scripts.region import in_region as east_in
from scripts.regions_us import REGIONS

URL = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}"
Z = 11
FILL = np.array([203, 230, 255])  # water-area fill color of the USGSHydroCached style
MIN_KM2 = 0.5
CACHE = DATA_DIR / "hydro_cache"
TILES = ROOT / "site" / "tiles"


def fetch(url, tries=4):
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "wind-analytics-prototype"}), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            time.sleep(2 * (a + 1))
        except Exception:
            time.sleep(2 * (a + 1))
    return None


def tx(lon, z=Z):
    return (lon + 180) / 360 * 2 ** z


def ty(lat, z=Z):
    return (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2 ** z


def lat_of(y, z=Z):
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 2 ** z))))


def tile_png(x, y):
    f = CACHE / f"{Z}_{x}_{y}.png"
    if f.exists():
        return f.read_bytes()
    b = fetch(URL.format(z=Z, x=x, y=y))
    f.write_bytes(b or b"")
    return b


def water_for_cell(lat, lon):
    """Water polygons (lon/lat) inside the 1-degree cell, from the hydrography tiles."""
    x0, x1 = int(tx(lon)), int(tx(lon + 1) - 1e-9)
    y0, y1 = int(ty(lat + 1)), int(ty(lat) - 1e-9)
    W, H = (x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256
    mask = np.zeros((H, W), bool)
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            b = tile_png(x, y)
            if not b:
                continue
            a = np.asarray(Image.open(io.BytesIO(b)).convert("RGBA"))
            m = (a[..., 3] > 200) & (np.abs(a[..., :3].astype(int) - FILL).sum(-1) < 30)
            mask[(y - y0) * 256:(y - y0 + 1) * 256, (x - x0) * 256:(x - x0 + 1) * 256] = m
    if not mask.any():
        return None
    mask = binary_closing(mask, iterations=2)  # flowlines drawn across water break the fill: close those gaps
    lab, n = label(mask)
    px_km = 40075 * math.cos(math.radians(lat + 0.5)) / (256 * 2 ** Z)
    sizes = np.bincount(lab.ravel())
    keep = np.zeros(n + 1, bool); keep[1:] = sizes[1:] * px_km * px_km >= MIN_KM2
    mask = keep[lab]
    if not mask.any():
        return None
    # pixels -> run-length boxes (in lon/lat) -> polygons
    boxes = []
    for r in range(H):
        row = mask[r]
        if not row.any():
            continue
        d = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
        starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
        la_top, la_bot = lat_of(y0 + r / 256), lat_of(y0 + (r + 1) / 256)
        for s, e in zip(starts, ends):
            lo0 = (x0 + s / 256) / 2 ** Z * 360 - 180
            lo1 = (x0 + e / 256) / 2 ** Z * 360 - 180
            boxes.append(box(lo0, la_bot, lo1, la_top))
    g = shapely.unary_union(boxes)
    # smooth the pixel staircase (~67 m cells) and simplify, so tiles stay small
    g = g.buffer(0.0004, join_style="round").buffer(-0.0004, join_style="round").simplify(0.0003, preserve_topology=True)
    g = shapely.clip_by_rect(g, lon, lat, lon + 1, lat + 1)
    return None if g.is_empty else g


_crm = {}


def tidal_elevation_ok(p, la, lo):
    """Median Coastal Relief Model elevation inside the piece is below +1 m (water connected to the tide)."""
    from scripts.bathymetry import crm_grid
    key = (la, lo)
    if key not in _crm:
        _crm[key] = crm_grid(la, la + 1, lo, lo + 1, 0.0025)
    z, lat_c, lon_c = _crm[key]
    if np.isnan(z).all():
        return True  # no elevation data here: keep the hydrography's answer
    minx, miny, maxx, maxy = p.bounds
    pts = []
    for x in np.linspace(minx, maxx, 12):
        for y in np.linspace(miny, maxy, 12):
            if p.contains(shapely.Point(x, y)):
                j, i = int((y - lat_c[0]) / 0.0025 + 0.5), int((x - lon_c[0]) / 0.0025 + 0.5)
                if 0 <= j < len(lat_c) and 0 <= i < len(lon_c) and not np.isnan(z[j, i]):
                    pts.append(z[j, i])
    return not pts or float(np.median(pts)) < 1.0


def us_region(lat, lon):
    pts = [(lat + a, lon + b) for a in (0, 0.5, 1) for b in (0, 0.5, 1)]
    if any(REGIONS["lakes"]["in_region"](*p) for p in pts):
        return None  # Great Lakes tiles come from their own build
    if any(east_in(*p) for p in pts):
        return "us"
    for r in ("west", "hawaii"):
        if any(REGIONS[r]["in_region"](*p) for p in pts):
            return r
    return None


def main():
    t0 = time.time()
    CACHE.mkdir(parents=True, exist_ok=True)
    idx = json.loads((TILES / "index.json").read_text())
    files, land = set(idx["tiles"]), set(idx["land"])
    coast = {k for k in files if us_region(*map(int, k.split("_")))}
    near = {f"{a + da}_{b + db}" for a, b in (map(int, k.split("_")) for k in coast) for da in (-1, 0, 1) for db in (-1, 0, 1)}
    cells = sorted(coast | (near & land))
    print(f"{len(cells)} one-degree cells to check ({len(coast)} with shoreline)", flush=True)
    water = {}
    done = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for k, g in zip(cells, ex.map(lambda k: water_for_cell(*map(int, k.split("_"))), cells)):
            done += 1
            if g is not None:
                water[k] = g
            if done % 20 == 0:
                print(f"  {done}/{len(cells)} cells ({time.time() - t0:.0f}s)", flush=True)
    out = DATA_DIR / "us_water"; out.mkdir(exist_ok=True)
    changed, kept = 0, {}  # kept: only the water actually cut into the shoreline, so the land raster always matches the tiles
    for k, g in list(water.items()):
        (out / f"{k}.json").write_text(json.dumps(shapely.geometry.mapping(g), separators=(",", ":")))
        la, lo = map(int, k.split("_"))
        if k in files:
            landg = shape(json.loads((TILES / f"{k}.json").read_text()))
        elif k in land:
            landg = box(lo, la, lo + 1, la + 1)
        else:
            continue
        landg = shapely.make_valid(landg) if not landg.is_valid else landg
        # only coastal water: big bodies (> 5 km2) or ones touching the sea already in the tile; inland ponds stay land
        sea = box(lo, la, lo + 1, la + 1).difference(landg)
        km2 = (111.32 ** 2) * math.cos(math.radians(la + 0.5))
        # keep: water that really joins the sea (within ~30 m), or a big lagoon (> 5 km2) lying within ~4 km of the sea.
        # Inland ponds and lakes stay land (they are not tidal, and the wind/current model is for sea water).
        def coastal(p):
            if sea.is_empty:  # a "solid land" square: only a big water body (e.g. Lake Pontchartrain), checked for sea level below
                return p.area * km2 > 5
            if p.buffer(0.0003).intersects(sea):
                return True
            return p.area * km2 > 5 and p.distance(sea) < 0.04
        parts = [p for p in (g.geoms if hasattr(g, "geoms") else [g]) if p.geom_type == "Polygon" and coastal(p)]
        if not parts:
            continue
        g = shapely.unary_union(parts)
        # Only add water the shoreline is really missing: pieces of it that lie on "land" and are big (> 1 km2) and wide
        # (> ~300 m). The original OpenStreetMap shoreline is far more exact; the hydrography image is only trusted for
        # whole lagoons it lacks (e.g. Florida's Indian River), never for slivers, coves or edges.
        add = g.intersection(landg)
        add = [p for p in (add.geoms if hasattr(add, "geoms") else [add]) if p.geom_type == "Polygon" and p.area * km2 > 1.0 and not p.buffer(-0.0015).is_empty]
        # Elevation check (NOAA Coastal Relief Model): tidal water lies at or below sea level; ponds and lakes sit above it.
        add = [p for p in add if tidal_elevation_ok(p, la, lo)]
        if not add:
            continue
        g = shapely.unary_union([p.buffer(-0.0003).buffer(0.0003) for p in add])
        if g.is_empty:
            continue
        kept[k] = g
        new = landg.difference(g)
        if new.area > landg.area - 1e-6:
            continue
        polys = [p for p in (new.geoms if hasattr(new, "geoms") else [new]) if p.geom_type == "Polygon" and p.area > 1e-9]
        rc = lambda r: [[round(x, 5), round(y, 5)] for x, y in r.coords]
        coords = [[rc(p.exterior)] + [rc(h) for h in p.interiors if Polygon(h).area > 1e-8] for p in polys]
        (TILES / f"{k}.json").write_text(json.dumps({"type": "MultiPolygon", "coordinates": coords}, separators=(",", ":")))
        if k in land:
            land.discard(k); files.add(k)
        changed += 1
    idx["tiles"], idx["land"] = sorted(files), sorted(land)
    (TILES / "index.json").write_text(json.dumps(idx))
    print(f"water found in {len(water)} cells; shoreline tiles updated: {changed}", flush=True)
    # ~600 m land rasters and overview coastlines of the US regions: draw the water polygons onto them
    from PIL import ImageDraw
    polys = [p for g in kept.values() for p in (g.geoms if hasattr(g, "geoms") else [g]) if p.geom_type == "Polygon"]
    for d in (DATA_DIR, DATA_DIR / "west", DATA_DIR / "hawaii"):
        g = json.loads((d / "coast_raster.json").read_text())
        cells_ = np.unpackbits(np.frombuffer(base64.b64decode(g["bits"]), dtype=np.uint8))[: g["nx"] * g["ny"]].reshape(g["ny"], g["nx"]).astype(bool)
        x1, y1 = g["lon0"] + g["nx"] * g["res"], g["lat0"] + g["ny"] * g["res"]
        inside = [p for p in polys if not (p.bounds[2] < g["lon0"] or p.bounds[0] > x1 or p.bounds[3] < g["lat0"] or p.bounds[1] > y1)]
        img = Image.new("1", (g["nx"], g["ny"]), 0); dr = ImageDraw.Draw(img)
        px = lambda cs: [((x - g["lon0"]) / g["res"], (y - g["lat0"]) / g["res"]) for x, y in cs]
        for p in inside:
            dr.polygon(px(p.exterior.coords), fill=1)
            for h in p.interiors:
                dr.polygon(px(h.coords), fill=0)
        w = np.array(img, dtype=bool)
        turned = int((cells_ & w).sum()); cells_ &= ~w
        g["bits"] = base64.b64encode(np.packbits(cells_.ravel()).tobytes()).decode()
        (d / "coast_raster.json").write_text(json.dumps(g, separators=(",", ":")))
        big = shapely.unary_union([p for p in inside if p.area > 2e-3])
        lo = shape(json.loads((d / "coast_land.json").read_text())); lo = shapely.make_valid(lo) if not lo.is_valid else lo
        nl = lo.difference(big) if not big.is_empty else lo
        ps = []  # keep the overview light: same ~150 m simplification as when it was built
        for p in (nl.geoms if hasattr(nl, "geoms") else [nl]):
            if p.geom_type == "Polygon" and p.area >= 4e-6:
                q = p.simplify(0.0015, preserve_topology=True)
                ps += [r for r in (q.geoms if hasattr(q, "geoms") else [q]) if r.geom_type == "Polygon" and not r.is_empty]
        rc = lambda r: [[round(x, 4), round(y, 4)] for x, y in r.coords]
        (d / "coast_land.json").write_text(json.dumps({"type": "MultiPolygon", "coordinates": [[rc(p.exterior)] + [rc(h) for h in p.interiors] for p in ps]}, separators=(",", ":")))
        print(f"  {d.name}: {turned:,} raster cells turned to water; overview coastline updated", flush=True)
    print(f"done ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    sys.exit(main())
