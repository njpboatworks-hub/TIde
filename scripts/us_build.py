"""Data for the extra US regions (West Coast, Great Lakes, Hawaii), same sources as the East and Gulf Coast map.

Usage:  python -m scripts.us_build <region> [stages...]
        region: west | lakes | hawaii
        stages (default all, in order): shore stations effects live tides places export
  shore     shoreline: OpenStreetMap land polygons (west, hawaii) or GSHHS full resolution with the Great Lakes cut
            out as water (lakes). Writes data/<region>/tiles/, coast_land.json, coast_raster.json
  stations  NOAA NDBC buoys and lighthouses + coastal airports (Iowa Environmental Mesonet), 2019-2024 hourly wind
            -> typical wind by month and local hour
  effects   how each station bends the regional breeze, by regional wind direction (May-Oct, 9:00-18:00 local)
  live      latest readings + National Weather Service hourly forecast (MET Norway where NWS has no grid, e.g. Canada)
  tides     NOAA CO-OPS tide and current predictions (none for the Great Lakes, which have almost no tide)
  places    US Census 2024 Gazetteer coastal towns + station names, for the search bar
  export    site/<region>/{coast,live,tides}.json, merges shoreline tiles into site/tiles and places into site/places.json
"""
from __future__ import annotations

import base64
import csv
import gzip
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from backend.config import DATA_DIR, ROOT
from backend.data import NDBC_HIST_URL, parse_stdmet
from scripts.coast_asos import hourly as asos_hourly
from scripts.regions_us import REGIONS

SCRATCH = os.environ.get("SAILIQ_SCRATCH", "/tmp/claude-0/-home-user/65f3a666-5834-5420-9315-d2900c574ed7/scratchpad")
OSM_ZIP = os.path.join(SCRATCH, "osm", "land-polygons-split-4326.zip")
GSHHS_DIR = os.path.join(SCRATCH, "hires", "mpl_toolkits", "basemap_data")
GAZ = os.path.join(SCRATCH, "gaz", "2024_Gaz_place_national.txt")
UA = {"User-Agent": "wind-analytics-prototype", "Accept": "application/geo+json, application/json"}
YEARS = list(range(2019, 2025))

KEY = R = D = OUT = None


def setup(key):
    global KEY, R, D, OUT
    KEY, R = key, REGIONS[key]
    D, OUT = DATA_DIR / key, ROOT / "site" / key
    D.mkdir(parents=True, exist_ok=True)


def get(url, as_json=True, tries=4, timeout=120):
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                b = r.read()
            return json.loads(b) if as_json else b
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            time.sleep(2 * (attempt + 1))
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None


def raster():
    g = json.loads((D / "coast_raster.json").read_text())
    g["cells"] = np.unpackbits(np.frombuffer(base64.b64decode(g["bits"]), dtype=np.uint8))[: g["nx"] * g["ny"]].reshape(g["ny"], g["nx"]).astype(bool)
    return g


def near_water(g, lat, lon, km):
    r = int(np.ceil(km / (g["res"] * 111.32)))
    i, j = int((lon - g["lon0"]) / g["res"]), int((lat - g["lat0"]) / g["res"])
    if not (r <= i < g["nx"] - r and r <= j < g["ny"] - r):
        return False
    return (~g["cells"][j - r: j + r + 1, i - r: i + r + 1]).sum() >= 3


# ---------------------------------------------------------------- shoreline
def _osm_polys(BB, in_region):
    import shapefile
    import shapely
    from shapely.geometry import shape
    from scripts.coast_shoreline_osm import near_region
    rdr = shapefile.Reader(OSM_ZIP)
    polys = []
    for shp in rdr.iterShapes(bbox=list(BB)):
        if not near_region(shp.bbox, BB, in_region):
            continue
        g = shape(shp.__geo_interface__)
        if not g.is_valid:
            g = shapely.make_valid(g)
        polys += [p for p in (g.geoms if hasattr(g, "geoms") else [g]) if p.geom_type == "Polygon" and not p.is_empty]
    return polys


def _gshhs_lakes_polys(BB, min_lake_km2=900.0):
    """GSHHS land with big lakes (the Great Lakes, Lake St. Clair) as water and their islands as land."""
    import shapely
    from shapely.geometry import Polygon, box
    data = open(os.path.join(GSHHS_DIR, "gshhs_f.dat"), "rb").read()
    by_level = {1: [], 2: [], 3: []}
    for line in open(os.path.join(GSHHS_DIR, "gshhsmeta_f.dat")):
        f = line.split()
        level, area, npts, south, north, off, nbytes = int(f[0]), float(f[1]), int(f[2]), float(f[3]), float(f[4]), int(f[5]), int(f[6])
        if level not in by_level or north < BB[1] or south > BB[3] or npts < 4:
            continue
        if level == 2 and area < min_lake_km2:
            continue
        xy = np.frombuffer(data, dtype="<f4", count=nbytes // 4, offset=off).reshape(-1, 2).astype(np.float64)
        lon = np.where(xy[:, 0] > 180, xy[:, 0] - 360, xy[:, 0])
        if lon.max() < BB[0] or lon.min() > BB[2]:
            continue
        p = Polygon(np.column_stack([lon, xy[:, 1]]))
        if not p.is_valid:
            p = shapely.make_valid(p)
        by_level[level].append(p)
    lakes = shapely.unary_union(by_level[2]) if by_level[2] else None
    islands = [p for p in by_level[3] if lakes is not None and lakes.intersects(p)]
    print(f"  GSHHS: {len(by_level[1])} land, {len(by_level[2])} big lakes, {len(islands)} lake islands", flush=True)
    out = []
    for p in by_level[1]:
        q = shapely.clip_by_rect(p, *BB)
        if lakes is not None:
            q = q.difference(lakes)
        out += [r for r in (q.geoms if hasattr(q, "geoms") else [q]) if r.geom_type == "Polygon" and not r.is_empty]
    for p in islands:
        q = shapely.clip_by_rect(p, *BB)
        out += [r for r in (q.geoms if hasattr(q, "geoms") else [q]) if r.geom_type == "Polygon" and not r.is_empty]
    return out


def _rings(p, simplify, nd=5):
    from shapely.geometry import Polygon
    s = p.simplify(simplify, preserve_topology=True)
    if s.is_empty or s.geom_type != "Polygon":
        return None
    rc = lambda r: [[round(x, nd), round(y, nd)] for x, y in r.coords]
    return [rc(s.exterior)] + [rc(h) for h in s.interiors if Polygon(h).area > simplify * simplify * 4]


def shore():
    import shapely
    from PIL import Image, ImageDraw
    from shapely.geometry import box
    BB, in_region = R["BB"], R["in_region"]
    t = time.time()
    polys = _osm_polys(BB, in_region) if R["shore"] == "osm" else _gshhs_lakes_polys(BB)
    print(f"{len(polys)} land pieces ({time.time() - t:.0f}s)", flush=True)
    simp = 0.00005 if R["shore"] == "osm" else 0.0001
    tdir = D / "tiles"; tdir.mkdir(exist_ok=True)
    for f in tdir.glob("*.json"):
        f.unlink()
    tree = shapely.STRtree(polys)
    index, land, total = [], [], 0
    for lat in range(int(np.floor(BB[1])), int(np.ceil(BB[3]))):
        for lon in range(int(np.floor(BB[0])), int(np.ceil(BB[2]))):
            if not any(in_region(la, lo) for la in (lat, lat + 0.5, lat + 1) for lo in (lon, lon + 0.5, lon + 1)):
                continue
            parts = []
            for i in tree.query(box(lon, lat, lon + 1, lat + 1)):
                q = shapely.clip_by_rect(polys[i], lon, lat, lon + 1, lat + 1)
                parts += [r for r in (q.geoms if hasattr(q, "geoms") else [q]) if r.geom_type == "Polygon" and not r.is_empty and r.area > 1e-9]
            if not parts:
                continue
            if sum(p.area for p in parts) >= 0.9999:
                land.append(f"{lat}_{lon}"); continue
            coords = [c for c in (_rings(p, simp) for p in parts) if c]
            if not coords:
                continue
            txt = json.dumps({"type": "MultiPolygon", "coordinates": coords}, separators=(",", ":"))
            (tdir / f"{lat}_{lon}.json").write_text(txt); index.append(f"{lat}_{lon}"); total += len(txt)
    (tdir / "index.json").write_text(json.dumps({"tiles": index, "land": land}))
    print(f"{len(index)} tiles + {len(land)} solid-land tiles, {total // 1024:,} KB ({time.time() - t:.0f}s)", flush=True)
    # overview coastline for zoomed-out maps (~150 m), keeping lakes and lagoons as holes
    coarse = [c for c in (_rings(p, 0.0015, 4) for p in polys if p.area >= 4e-6) if c]
    # merge OSM's split pieces first so the overview has no seams
    if R["shore"] == "osm":
        merged = shapely.unary_union([p.buffer(0) for p in polys if p.area >= 4e-6])
        coarse = [c for c in (_rings(p, 0.0015, 4) for p in (merged.geoms if hasattr(merged, "geoms") else [merged])) if c]
    (D / "coast_land.json").write_text(json.dumps({"type": "MultiPolygon", "coordinates": coarse}, separators=(",", ":")))
    print(f"overview coastline {(D / 'coast_land.json').stat().st_size // 1024:,} KB", flush=True)
    res = 0.006
    nx, ny = int(np.ceil((BB[2] - BB[0]) / res)), int(np.ceil((BB[3] - BB[1]) / res))
    img = Image.new("1", (nx, ny), 0)
    dr = ImageDraw.Draw(img)
    px = lambda cs: [((x - BB[0]) / res, (y - BB[1]) / res) for x, y in cs]
    for p in polys:
        dr.polygon(px(p.exterior.coords), fill=1)
        for h in p.interiors:
            dr.polygon(px(h.coords), fill=0)
    cells = np.array(img, dtype=bool)
    (D / "coast_raster.json").write_text(json.dumps({"lon0": BB[0], "lat0": BB[1], "res": res, "nx": nx, "ny": ny,
                                                      "bits": base64.b64encode(np.packbits(cells.ravel()).tobytes()).decode()}, separators=(",", ":")))
    print(f"land raster {nx}x{ny}, {cells.mean() * 100:.0f}% land ({time.time() - t:.0f}s)", flush=True)


# ---------------------------------------------------------------- stations
def ndbc_hourly(sid):
    cache = DATA_DIR / "ndbc"; cache.mkdir(exist_ok=True)
    f = cache / f"{sid}.npz"
    if f.exists():
        z = np.load(f)
        return None if len(z["t"]) == 0 else pd.DataFrame({"u": z["u"], "v": z["v"], "s": z["s"]}, index=pd.to_datetime(z["t"], utc=True))
    parts = []
    for y in YEARS:
        blob = get(NDBC_HIST_URL.format(sid=sid, year=y), as_json=False, timeout=90)
        if not blob:
            continue
        try:
            df = parse_stdmet(gzip.decompress(blob).decode("ascii", "ignore"))[["WDIR", "WSPD_KT"]].dropna()
        except Exception:
            continue
        if len(df):
            parts.append(df)
    if not parts:
        np.savez_compressed(f, t=np.array([], dtype=np.int64), u=[], v=[], s=[])
        return None
    df = pd.concat(parts)
    r = np.deg2rad(df["WDIR"])
    h = pd.DataFrame({"u": np.sin(r) * df["WSPD_KT"], "v": np.cos(r) * df["WSPD_KT"], "s": df["WSPD_KT"]}).resample("1h").mean().dropna()
    np.savez_compressed(f, t=h.index.values.astype("datetime64[ns]").astype(np.int64), u=h["u"].values, v=h["v"].values, s=h["s"].values)
    return h


def hourly(st):
    return asos_hourly({"id": st["id"], "sid": st["id"][5:]}) if st["id"].startswith("asos_") else ndbc_hourly(st["id"])


def typical(st, h, min_frac):
    if h is None or len(h) < 24 * 200:
        return None
    loc = h.index.tz_convert(st["tz"])
    h = h.assign(mo=loc.month, hr=loc.hour)
    gr = h.groupby(["mo", "hr"])
    mu, mv, ms, med, n = gr["u"].mean(), gr["v"].mean(), gr["s"].mean(), gr["s"].median(), gr["s"].size()
    Dd = [[None] * 24 for _ in range(12)]; S = [[None] * 24 for _ in range(12)]; T = [[None] * 24 for _ in range(12)]
    for (mo, hr), cnt in n.items():
        if cnt < 30:
            continue
        u, v = mu[(mo, hr)], mv[(mo, hr)]
        Dd[mo - 1][hr] = int(round((np.degrees(np.arctan2(u, v)) + 360) % 360))
        S[mo - 1][hr] = round(float(med[(mo, hr)]), 1)
        T[mo - 1][hr] = round(float(np.hypot(u, v) / ms[(mo, hr)]), 2) if ms[(mo, hr)] > 0 else 0
    if sum(x is not None for row in Dd for x in row) < 288 * min_frac:
        return None
    return {k: st[k] for k in ("id", "name", "lat", "lon", "tz") if k in st} | ({"kind": st["kind"]} if "kind" in st else {}) | {"dir": Dd, "spd": S, "steady": T}


def stations():
    g, in_region, tz = raster(), R["in_region"], R["tz"]
    table = (get("https://www.ndbc.noaa.gov/data/stations/station_table.txt", as_json=False) or b"").decode("utf-8", "ignore")
    index = (get("https://www.ndbc.noaa.gov/data/historical/stdmet/", as_json=False) or b"").decode("utf-8", "ignore")
    have = {}
    for sid, yr in re.findall(r'href="([a-z0-9]+)h(\d{4})\.txt\.gz"', index):
        if int(yr) in YEARS:
            have.setdefault(sid, set()).add(int(yr))
    cands = []
    for line in table.splitlines():
        f = line.split("|")
        if line.startswith("#") or len(f) < 7:
            continue
        m = re.match(r"\s*([\d.]+) ([NS]) ([\d.]+) ([EW])", f[6])
        if not m:
            continue
        lat = float(m.group(1)) * (1 if m.group(2) == "N" else -1)
        lon = float(m.group(3)) * (-1 if m.group(4) == "W" else 1)
        sid = f[0].strip().lower()
        if sid in have and in_region(lat, lon):
            cands.append({"id": sid, "name": re.sub(r"<[^>]+>", "", f[4]).strip() or sid.upper(), "lat": lat, "lon": lon, "tz": tz(lat, lon)})
    n_ndbc = len(cands)
    for net in R["asos"]:
        d = get(f"https://mesonet.agron.iastate.edu/geojson/network/{net}.geojson") or {}
        for f in d.get("features", []):
            lon, lat = f["geometry"]["coordinates"]
            p = f["properties"]
            if in_region(lat, lon) and near_water(g, lat, lon, 3.0):
                cands.append({"id": "asos_" + p["sid"], "name": p.get("sname", p["sid"]).title() + " (airport)",
                              "lat": round(lat, 4), "lon": round(lon, 4), "kind": "airport", "tz": tz(lat, lon)})
    print(f"{n_ndbc} NDBC stations and {len(cands) - n_ndbc} coastal airports to process", flush=True)
    min_frac = R.get("min_months_frac", 0.5)
    out = []

    def one(st):
        return typical(st, hourly(st), min_frac)
    with ThreadPoolExecutor(max_workers=4) as ex:
        for i, r in enumerate(ex.map(one, cands)):
            if r:
                out.append(r)
            if (i + 1) % 20 == 0:
                print(f"  {i + 1}/{len(cands)} processed, {len(out)} usable", flush=True)
    (D / "stations.json").write_text(json.dumps({"years": [YEARS[0], YEARS[-1]], "stations": out}, separators=(",", ":")))
    print(f"wrote {len(out)} stations with typical wind", flush=True)


# ---------------------------------------------------------------- effects
def effects(MIN_REG_KT=6.0, MIN_STN_KT=3.0, NSECT=16):
    SIGMA_KM, CUTOFF_KM = R["effects_sigma"], R["effects_cutoff"]
    sts = json.loads((D / "stations.json").read_text())["stations"]
    idx = pd.date_range(f"{YEARS[0]}-01-01", f"{YEARS[-1]}-12-31 23:00", freq="1h", tz="UTC")
    U = np.full((len(sts), len(idx)), np.nan, np.float32); V = U.copy(); S = U.copy(); keep = np.zeros((len(sts), len(idx)), bool)
    with ThreadPoolExecutor(max_workers=4) as ex:
        hs = list(ex.map(hourly, sts))
    for i, (st, h) in enumerate(zip(sts, hs)):
        if h is None:
            continue
        h = h.reindex(idx)
        U[i], V[i], S[i] = h["u"].values, h["v"].values, h["s"].values
        loc = idx.tz_convert(st["tz"])
        keep[i] = (loc.month >= 5) & (loc.month <= 10) & (loc.hour >= 9) & (loc.hour <= 18)
    lat = np.array([s["lat"] for s in sts]); lon = np.array([s["lon"] for s in sts])
    dist = np.hypot((lat[:, None] - lat[None, :]) * 111.32, (lon[:, None] - lon[None, :]) * 111.32 * np.cos(np.deg2rad((lat[:, None] + lat[None, :]) / 2)))
    W = np.exp(-(dist ** 2) / (2 * SIGMA_KM ** 2)) * (dist <= CUTOFF_KM); np.fill_diagonal(W, 0)
    valid = ~np.isnan(U)
    wsum = W @ valid.astype(np.float32)
    RU = (W @ np.nan_to_num(U)) / np.where(wsum > 0, wsum, np.nan); RV = (W @ np.nan_to_num(V)) / np.where(wsum > 0, wsum, np.nan)
    nref = (W > 0.05).astype(np.float32) @ valid.astype(np.float32)
    rdir = (np.degrees(np.arctan2(RU, RV)) + 360) % 360; rspd = np.hypot(RU, RV); sdir = (np.degrees(np.arctan2(U, V)) + 360) % 360
    out = []
    for i, st in enumerate(sts):
        ok = valid[i] & keep[i] & (nref[i] >= 1) & (rspd[i] >= MIN_REG_KT) & (S[i] >= MIN_STN_KT)
        if ok.sum() < 200:
            continue
        rd, sd = rdir[i][ok], sdir[i][ok]
        off = ((sd - rd + 180) % 360) - 180; ratio = S[i][ok] / rspd[i][ok]
        sect = np.floor(((rd + 360 / NSECT / 2) % 360) / (360 / NSECT)).astype(int)
        O, Rr, C = [], [], []
        for k in range(NSECT):
            m = sect == k
            if m.sum() < 30:
                O.append(None); Rr.append(None); C.append(None); continue
            a = np.deg2rad(off[m])
            O.append(round(float(np.degrees(np.arctan2(np.sin(a).mean(), np.cos(a).mean()))), 1))
            Rr.append(round(float(np.median(ratio[m])), 2)); C.append(round(float(np.hypot(np.sin(a).mean(), np.cos(a).mean())), 2))
        out.append({"id": st["id"], "off": O, "ratio": Rr, "cons": C})
    (D / "effects.json").write_text(json.dumps({"sectors": NSECT, "season": "May-Oct, 9:00-18:00 local", "stations": out}, separators=(",", ":")))
    print(f"wrote direction effects for {len(out)} of {len(sts)} stations", flush=True)


# ---------------------------------------------------------------- live + forecast
def _expand(series, start, hours, conv=1.0):
    out = [None] * hours
    for v in series.get("values", []):
        if v["value"] is None:
            continue
        t_str, dur = v["validTime"].split("/")
        t = datetime.fromisoformat(t_str)
        m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?", dur)
        n = int(m.group(1) or 0) * 24 + int(m.group(2) or 0) or 1
        for k in range(n):
            i = int((t + timedelta(hours=k) - start).total_seconds() // 3600)
            if 0 <= i < hours:
                out[i] = v["value"] * conv
    return out


def _metno(p, start, HOURS):
    d = get(f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={p[0]:.3f}&lon={p[1]:.3f}")
    ts = ((d or {}).get("properties") or {}).get("timeseries") or []
    if len(ts) < 24:
        return None
    t = np.array([(datetime.fromisoformat(x["time"].replace("Z", "+00:00")) - start).total_seconds() / 3600 for x in ts])
    det = [x["data"]["instant"]["details"] for x in ts]
    sp = np.array([x.get("wind_speed", np.nan) for x in det]) * 1.943844
    dr = np.deg2rad([x.get("wind_from_direction", np.nan) for x in det])
    u, v = np.sin(dr) * sp, np.cos(dr) * sp
    hrs = np.arange(HOURS); ok = (hrs >= t.min()) & (hrs <= t.max())
    ui, vi = np.interp(hrs, t, u), np.interp(hrs, t, v)
    return {"lat": p[0], "lon": p[1], "d": [int(round((np.degrees(np.arctan2(a, b)) + 360) % 360)) if k else None for a, b, k in zip(ui, vi, ok)],
            "s": [round(float(np.hypot(a, b)), 1) if k else None for a, b, k in zip(ui, vi, ok)], "src": "metno"}


def live(HOURS=168):
    from scipy.ndimage import binary_dilation
    now = datetime.now(timezone.utc)
    g, in_region, BB = raster(), R["in_region"], R["BB"]
    obs = []
    txt = (get("https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt", as_json=False) or b"").decode("utf-8", "ignore")
    for line in txt.splitlines():
        f = line.split()
        if line.startswith("#") or len(f) < 11:
            continue
        try:
            lat, lon = float(f[1]), float(f[2])
        except ValueError:
            continue
        if not in_region(lat, lon) or f[8] == "MM" or f[9] == "MM":
            continue
        t = datetime(int(f[3]), int(f[4]), int(f[5]), int(f[6]), int(f[7]), tzinfo=timezone.utc)
        if now - t > timedelta(hours=3):
            continue
        obs.append({"id": f[0].lower(), "lat": lat, "lon": lon, "dir": int(f[8]), "spd": round(float(f[9]) * 1.943844, 1),
                    "gust": None if f[10] == "MM" else round(float(f[10]) * 1.943844, 1), "time": t.isoformat(), "kind": "buoy"})
    for net in R["asos"]:
        d = get(f"https://mesonet.agron.iastate.edu/api/1/currents.json?network={net}") or {}
        for r in d.get("data", []):
            lat, lon = r.get("lat"), r.get("lon")
            if lat is None or not in_region(lat, lon) or not near_water(g, lat, lon, 3.0) or r.get("drct") is None or r.get("sknt") is None:
                continue
            try:
                t = datetime.fromisoformat(r["utc_valid"].replace("Z", "+00:00"))
            except Exception:
                continue
            if now - t > timedelta(hours=3):
                continue
            obs.append({"id": "asos_" + r["station"], "name": r.get("name"), "lat": lat, "lon": lon, "dir": int(r["drct"]),
                        "spd": round(float(r["sknt"]), 1), "gust": r.get("gust"), "time": t.isoformat(), "kind": "airport"})
    print(f"{len(obs)} live readings", flush=True)
    band_km = 120 if KEY == "lakes" else 40  # the Great Lakes are wide: cover all of their water
    band = binary_dilation(g["cells"], iterations=int(band_km / (g["res"] * 111.32))) & ~g["cells"]
    sts = json.loads((D / "stations.json").read_text())["stations"]
    pts = {(round(s["lat"], 3), round(s["lon"], 3)) for s in sts}
    step = 0.2 if KEY == "hawaii" else 0.25
    for la in np.arange(BB[1] + 0.1, BB[3], step):
        for lo in np.arange(BB[0] + 0.1, BB[2], step):
            i, j = int((lo - g["lon0"]) / g["res"]), int((la - g["lat0"]) / g["res"])
            if 0 <= i < g["nx"] and 0 <= j < g["ny"] and band[j, i] and in_region(la, lo):
                pts.add((round(float(la), 3), round(float(lo), 3)))
    pts = sorted(pts)
    start = now.replace(minute=0, second=0, microsecond=0)
    cache_f = DATA_DIR / "nws_points.json"
    cache = json.loads(cache_f.read_text()) if cache_f.exists() else {}

    def grid_url(p):
        k = f"{p[0]},{p[1]}"
        if k not in cache:
            d = get(f"https://api.weather.gov/points/{p[0]},{p[1]}")
            cache[k] = ((d or {}).get("properties") or {}).get("forecastGridData")
        return cache[k]
    with ThreadPoolExecutor(max_workers=6) as ex:
        urls = list(ex.map(grid_url, pts))
    cache_f.write_text(json.dumps(cache))
    by_url, no_nws = {}, []
    for p, u in zip(pts, urls):
        if u:
            by_url.setdefault(u, p)
        else:
            no_nws.append(p)

    def nws(item):
        u, p = item
        props = (get(u) or {}).get("properties")
        if not props:
            return _metno(p, start, HOURS)
        dirs = _expand(props.get("windDirection", {}), start, HOURS)
        spds = _expand(props.get("windSpeed", {}), start, HOURS, 0.539957)
        if sum(x is not None for x in dirs) < 24:
            return _metno(p, start, HOURS)
        return {"lat": p[0], "lon": p[1], "d": [None if x is None else int(round(x)) for x in dirs], "s": [None if x is None else round(x, 1) for x in spds]}
    with ThreadPoolExecutor(max_workers=6) as ex:
        series = [f for f in ex.map(nws, by_url.items()) if f]
        series += [f for f in ex.map(lambda p: _metno(p, start, HOURS), no_nws) if f]
    n_met = sum(1 for s in series if s.get("src") == "metno")
    for s in series:
        s.pop("src", None)
    print(f"{len(series)} forecast series ({len(pts)} points; {n_met} from MET Norway where NWS has no grid)", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    src = "NOAA NDBC, Iowa Environmental Mesonet (airports), National Weather Service" + (", MET Norway (CC BY 4.0)" if n_met else "")
    (OUT / "live.json").write_text(json.dumps({"generated": now.isoformat(timespec="minutes"), "obs": obs,
                                               "fc": {"start": start.isoformat(), "hours": HOURS, "points": series}, "sources": src},
                                              separators=(",", ":")))


# ---------------------------------------------------------------- tides
def tides(days_ahead=8):
    from scripts.tides_snapshot import main as tides_main
    OUT.mkdir(parents=True, exist_ok=True)
    tz = R["tz"]((R["BB"][1] + R["BB"][3]) / 2, (R["BB"][0] + R["BB"][2]) / 2)
    if not R["noaa_tides"]:
        start = datetime.now().date() - timedelta(days=1)
        (OUT / "tides.json").write_text(json.dumps({"start": f"{start}T00:00", "tz": tz, "source": "none", "currents": [], "tides": [],
                                                    "note": "The Great Lakes have almost no tide (a few centimetres); water level changes there come from wind and pressure."}))
        print("no tide predictions for this region (Great Lakes)", flush=True)
        return
    tides_main(days_ahead, in_region=R["in_region"], out=OUT / "tides.json", tz=tz)


# ---------------------------------------------------------------- places
SUFFIX = re.compile(r"\s+(CDP|city|town|village|borough|municipality|city and borough|urban county|consolidated government.*|metro government.*|unified government.*|\(balance\))$", re.I)


def places():
    from scipy.ndimage import binary_dilation
    g = raster()
    near = binary_dilation(~g["cells"], iterations=int(20 / (g["res"] * 111.32)))

    def coastal(lat, lon):
        i, j = int((lon - g["lon0"]) / g["res"]), int((lat - g["lat0"]) / g["res"])
        return 0 <= i < g["nx"] and 0 <= j < g["ny"] and bool(near[j, i])
    out, seen = [], set()
    with open(GAZ, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            r = {k.strip(): v.strip() for k, v in r.items()}
            if r["USPS"] not in R["states"]:
                continue
            lat, lon = float(r["INTPTLAT"]), float(r["INTPTLONG"])
            if not R["in_region"](lat, lon) or not coastal(lat, lon):
                continue
            name = SUFFIX.sub("", r["NAME"]).strip()
            if (name.lower(), r["USPS"]) in seen:
                continue
            seen.add((name.lower(), r["USPS"]))
            out.append([name, r["USPS"], round(lat, 4), round(lon, 4), "town", float(r["ALAND_SQMI"] or 0)])
    out.sort(key=lambda x: -x[5])
    (D / "places.json").write_text(json.dumps([x[:5] for x in out], ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {len(out)} coastal towns", flush=True)


# ---------------------------------------------------------------- export
def export():
    import shutil
    OUT.mkdir(parents=True, exist_ok=True)
    st = json.loads((D / "stations.json").read_text())
    coast = {"years": st["years"], "stations": st["stations"], "land": json.loads((D / "coast_land.json").read_text()),
             "raster": json.loads((D / "coast_raster.json").read_text())}
    if (D / "effects.json").exists():
        coast["effects"] = json.loads((D / "effects.json").read_text())
    (OUT / "coast.json").write_text(json.dumps(coast, separators=(",", ":")))
    tiles_dir = ROOT / "site" / "tiles"
    idx = json.loads((tiles_dir / "index.json").read_text())
    have = set(idx["tiles"]) | set(idx["land"])
    sub = json.loads((D / "tiles" / "index.json").read_text())
    added = 0
    # The East Coast build made plain GSHHS tiles across its whole box, where the Great Lakes are land.
    # This region's tiles (lakes cut out as water) replace those.
    override = KEY == "lakes"
    if override:
        mine = set(sub["tiles"]) | set(sub["land"])
        idx["tiles"] = [k for k in idx["tiles"] if k not in mine]; idx["land"] = [k for k in idx["land"] if k not in mine]
        have = set(idx["tiles"]) | set(idx["land"])
    for k in sub["tiles"]:
        if k not in have:
            shutil.copy(D / "tiles" / f"{k}.json", tiles_dir / f"{k}.json"); idx["tiles"].append(k); have.add(k); added += 1
    for k in sub["land"]:
        if k not in have:
            idx["land"].append(k); have.add(k)
    (tiles_dir / "index.json").write_text(json.dumps(idx))
    pl = [p for p in json.loads((ROOT / "site" / "places.json").read_text()) if not (len(p) > 5 and p[5] == KEY)]
    for p in json.loads((D / "places.json").read_text()):
        pl.append(p + [KEY])
    for s in st["stations"]:
        pl.append([re.sub(r"^\d+\s*-\s*", "", s["name"]), "wind station", round(s["lat"], 4), round(s["lon"], 4), "wind", KEY])
    tdf = OUT / "tides.json"
    if tdf.exists():
        td = json.loads(tdf.read_text())
        pl += [[t["name"], "NOAA tide station", t["lat"], t["lon"], "tide", KEY] for t in td["tides"]]
        pl += [[c["name"], "NOAA current station", c["lat"], c["lon"], "current", KEY] for c in td["currents"]]
    (ROOT / "site" / "places.json").write_text(json.dumps(pl, ensure_ascii=False, separators=(",", ":")))
    print(f"exported site/{KEY}/coast.json ({(OUT / 'coast.json').stat().st_size // 1024} KB), {added} new shoreline tiles, places merged", flush=True)


if __name__ == "__main__":
    setup(sys.argv[1])
    for stage in sys.argv[2:] or ["shore", "stations", "effects", "live", "tides", "places", "export"]:
        t = time.time()
        globals()[stage]()
        print(f"[{KEY} {stage}] done in {time.time() - t:.0f}s", flush=True)
