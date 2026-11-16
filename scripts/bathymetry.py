"""Water depth near NOAA tidal-current stations, and a "channel factor" for spreading current speeds.

Sources (NOAA NCEI, public domain), read over OPeNDAP so only the sampled points are downloaded:
  * CUDEM 1/9 arc-second (~3 m) topobathy tiles where NCEI's THREDDS server has them (tiles/tiled_19as)
  * CUDEM 1/3 arc-second (~10 m) tiles next (tiles/tiled_13as)
  * U.S. Coastal Relief Model, 3 arc-second (~90 m), everywhere else (crm/crm_vol*.nc), and for the wide-area terms

Output, served with the site:
  site/depth/f_<la4>_<lo4>.png  0.25-degree tiles at 0.0005 degrees (~55 m), within ~12 km of a current station
  site/depth/c_<lat>_<lon>.png  2-degree blocks at 0.0025 degrees (~275 m), for zoomed-out views
  site/depth/index.json
  data/depth_station_factors.json   channel factor at each current station (merged into tides.json)
PNG pixels (RGB, top row = north): R = channel factor (0 = land/no data), G = depth code (depth m = (G/8)^2),
B = channel axis: 0 = none, else 1 + axis/3 (0..59) + 60 * strength level (0..3).

The channel factor is a rule of thumb, not a hydrodynamic model: where water is deeper than its surroundings, or
squeezed between land, tidal flow is usually faster, so speed ~ sqrt(depth / nearby depth) x sqrt(regional water / local water).
Usage: python -m scripts.bathymetry
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, map_coordinates

from backend.config import DATA_DIR, ROOT
from scripts.region import in_region as east_in
from scripts.regions_us import REGIONS

THREDDS = "https://www.ngdc.noaa.gov/thredds"
UA = {"User-Agent": "wind-analytics-prototype"}
CRM = {1: (-80, -64, 40, 48), 2: (-85, -68, 31, 40), 3: (-87, -78, 24, 35), 4: (-94, -87, 24, 36), 5: (-108, -94, 24, 38),
       6: (-126, -114, 32, 37), 7: (-128, -117, 37, 44), 8: (-128, -116, 44, 49), 10: (-162, -152, 18, 24)}
FINE, COARSE, BLOCK, M = 0.0005, 0.0025, 2, 0.2   # degrees
FINE_KM, COARSE_KM = 12.0, 25.0
OUT = ROOT / "site" / "depth"
CACHE = DATA_DIR / "depth_cache"
FMIN, FMAX = 0.3, 2.5


def fetch(url, tries=5):
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            time.sleep(3 * (a + 1))
        except Exception:
            time.sleep(3 * (a + 1))
    return None


def dods_grid(url):
    """Parse an OPeNDAP DAP2 binary Grid response (array + its two maps)."""
    b = fetch(url)
    if not b:
        return None
    k = b.find(b"\nData:\n")
    if k < 0:
        return None
    dds, data = b[:k].decode("ascii", "ignore"), b[k + 7:]
    dims = [int(x) for x in re.findall(r"= (\d+)", re.search(r"ARRAY:\s*\w+ \w+((?:\[\w+ = \d+\])+)", dds).group(1))]
    typ = re.search(r"ARRAY:\s*(\w+)", dds).group(1)
    dt, sz = {"Float32": (">f4", 4), "Int32": (">i4", 4), "Int16": (">i4", 4), "Float64": (">f8", 8)}[typ]
    n = dims[0] * dims[1]
    arr = np.frombuffer(data, dt, n, 8).astype(np.float32).reshape(dims)
    p = 8 + n * sz
    ny = int.from_bytes(data[p:p + 4], "big"); ys = np.frombuffer(data, ">f8", ny, p + 8); p += 8 + 8 * ny
    nx = int.from_bytes(data[p:p + 4], "big"); xs = np.frombuffer(data, ">f8", nx, p + 8)
    return arr, ys.copy(), xs.copy()


def sample_into(dst, lat_c, lon_c, arr, ys, xs, fill=None):
    """Nearest-neighbour copy of a source grid into dst (rows = lat_c ascending, cols = lon_c) where dst is NaN."""
    dy, dx = ys[1] - ys[0], xs[1] - xs[0]
    jj = np.round((lat_c - ys[0]) / dy).astype(int); ii = np.round((lon_c - xs[0]) / dx).astype(int)
    vj, vi = (jj >= 0) & (jj < len(ys)), (ii >= 0) & (ii < len(xs))
    if not vj.any() or not vi.any():
        return
    sub = arr[np.ix_(jj[vj], ii[vi])]
    if fill is not None:
        sub = np.where(sub <= fill + 1, np.nan, sub)
    tgt = dst[np.ix_(np.where(vj)[0], np.where(vi)[0])]
    m = np.isnan(tgt)
    tgt[m] = sub[m]
    dst[np.ix_(np.where(vj)[0], np.where(vi)[0])] = tgt


def crm_grid(lat0, lat1, lon0, lon1, res):
    """CRM elevation (m, + up) on cell centres of [lat0,lat1) x [lon0,lon1) at res; stride chosen to match res."""
    lat_c = lat0 + (np.arange(round((lat1 - lat0) / res)) + 0.5) * res
    lon_c = lon0 + (np.arange(round((lon1 - lon0) / res)) + 0.5) * res
    out = np.full((len(lat_c), len(lon_c)), np.nan, np.float32)
    stride = max(1, round(res * 1200))
    for v, (x0, x1, y0, y1) in CRM.items():
        if lon1 <= x0 or lon0 >= x1 or lat1 <= y0 or lat0 >= y1:
            continue
        nyv, nxv = round((y1 - y0) * 1200) + 1, round((x1 - x0) * 1200) + 1
        ja, jb = max(0, int((max(lat0, y0) - y0) * 1200) - 2), min(nyv - 1, int(math.ceil((min(lat1, y1) - y0) * 1200)) + 2)
        ia, ib = max(0, int((max(lon0, x0) - x0) * 1200) - 2), min(nxv - 1, int(math.ceil((min(lon1, x1) - x0) * 1200)) + 2)
        key = CACHE / f"crm{v}_{ja}_{jb}_{ia}_{ib}_{stride}.npz"
        if key.exists():
            z = np.load(key); g = (z["a"], z["y"], z["x"])
        else:
            g = dods_grid(f"{THREDDS}/dodsC/crm/crm_vol{v}.nc.dods?z%5B{ja}:{stride}:{jb}%5D%5B{ia}:{stride}:{ib}%5D")
            if g is None:
                print(f"    CRM vol{v} request failed", flush=True); continue
            np.savez_compressed(key, a=g[0], y=g[1], x=g[2])
        sample_into(out, lat_c, lon_c, *g)
    return out, lat_c, lon_c


CUDEM = {}


def load_cudem_lists():
    for c in ("tiled_19as", "tiled_13as"):
        txt = (fetch(f"{THREDDS}/catalog/tiles/{c}/catalog.xml") or b"").decode("utf-8", "ignore")
        for n in re.findall(r'dataset name="(ncei\d+_n\d+x\d+_w\d+x\d+_\d+v\d+\.nc)"', txt):
            m = re.match(r"ncei(\d+)_n(\d+)x(\d+)_w(\d+)x(\d+)", n)
            key = (round((int(m[2]) + int(m[3]) / 100 - 0.25) * 4), round(-(int(m[4]) + int(m[5]) / 100) * 4))
            if key not in CUDEM or m[1] == "19":   # prefer 1/9 arc-second
                CUDEM[key] = (c, n)
    print(f"{len(CUDEM)} CUDEM tiles listed", flush=True)


def cudem_tile(la4, lo4, lat_c, lon_c):
    """CUDEM elevation on the fine grid for one 0.25-degree tile, or None."""
    src = CUDEM.get((la4, lo4))
    if not src:
        return None, None
    c, n = src
    npx = 8100 if c == "tiled_19as" else 2712
    stride = max(1, int((FINE / 0.25) * (8100 if c == "tiled_19as" else 2700)))
    key = CACHE / f"{n}_{stride}.npz"
    if key.exists():
        z = np.load(key); g = (z["a"], z["y"], z["x"])
    else:
        g = dods_grid(f"{THREDDS}/dodsC/tiles/{c}/{n}.dods?Band1%5B0:{stride}:{npx - 1}%5D%5B0:{stride}:{npx - 1}%5D")
        if g is None:
            return None, None
        np.savez_compressed(key, a=g[0], y=g[1], x=g[2])
    out = np.full((len(lat_c), len(lon_c)), np.nan, np.float32)
    sample_into(out, lat_c, lon_c, *g, fill=-9999)
    return out, ("1/9" if c == "tiled_19as" else "1/3")


def gauss(a, sigma_m, lat):
    sy = sigma_m / (111320 * COARSE); sx = sigma_m / (111320 * COARSE * math.cos(math.radians(lat)))
    return gaussian_filter(a, (sy, sx), mode="nearest", truncate=3.0)


def coarse_terms(z, lat_mid):
    """Wide-area terms on the coarse grid: nearby depth, water squeeze, channel axis and its strength."""
    nan = np.isnan(z)
    water = (z < 0) | nan
    h = np.where(nan, 200.0, np.maximum(-z, 0.3)) * water
    w = water.astype(np.float32)
    href = gauss(h, 2500, lat_mid) / np.maximum(gauss(w, 2500, lat_mid), 1e-3)
    wloc, wreg = gauss(w, 1500, lat_mid), gauss(w, 8000, lat_mid)
    fc = np.clip(np.sqrt(wreg / np.maximum(wloc, 0.05)), 0.7, 1.8)
    hs = gauss(h, 600, lat_mid)
    py, px = 111320 * COARSE, 111320 * COARSE * math.cos(math.radians(lat_mid))
    gy, gx = np.gradient(hs, py, px)
    jxx, jxy, jyy = gauss(gx * gx, 1500, lat_mid), gauss(gx * gy, 1500, lat_mid), gauss(gy * gy, 1500, lat_mid)
    th = 0.5 * np.arctan2(2 * jxy, jxx - jyy)                      # dominant gradient direction, radians from east (CCW)
    axis = (180 - np.degrees(th)) % 180                            # flow axis along the depth contours, compass degrees
    coh = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / (jxx + jyy + 1e-12)
    slope = np.sqrt(jxx + jyy) * 2000 / np.maximum(href, 1.0)
    strength = coh * np.clip(slope, 0, 1)
    return water, h, href, fc, axis, strength


def encode(water, h, f, axis, strength):
    R = np.where(water, 1 + np.round((np.log(np.clip(f, FMIN, FMAX)) - math.log(FMIN)) / (math.log(FMAX) - math.log(FMIN)) * 254), 0)
    G = np.where(water, np.clip(np.round(np.sqrt(h) * 8), 1, 255), 0)
    lev = np.clip(np.floor((strength - 0.25) / 0.1875), 0, 3)
    B = np.where(water & (strength >= 0.25), 1 + np.floor(axis / 3) % 60 + 60 * lev, 0)
    rgb = np.stack([R, G, B], -1).astype(np.uint8)[::-1]          # top row = north
    return Image.fromarray(rgb, "RGB")


def decode_f(code):
    return math.exp(math.log(FMIN) + (code - 1) / 254 * (math.log(FMAX) - math.log(FMIN)))


def current_stations():
    d = json.loads(fetch("https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=currentpredictions"))
    regs = [east_in] + [REGIONS[k]["in_region"] for k in ("west", "hawaii")]
    seen = {}
    for s in d["stations"]:
        if s["type"] in ("H", "S") and any(f(s["lat"], s["lng"]) for f in regs):
            seen.setdefault(s["id"], (s["lat"], s["lng"]))
    return seen


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True); CACHE.mkdir(parents=True, exist_ok=True)
    st = current_stations()
    print(f"{len(st)} NOAA current stations (East, Gulf, West, Hawaii)", flush=True)
    if "factors" in sys.argv:
        return station_factors(st, json.loads((OUT / "index.json").read_text()), t0)
    load_cudem_lists()
    fine, blocks = set(), set()
    for lat, lon in st.values():
        dl, dk = FINE_KM / 111.32, FINE_KM / (111.32 * math.cos(math.radians(lat)))
        for a in range(math.floor((lat - dl) * 4), math.floor((lat + dl) * 4) + 1):
            for b in range(math.floor((lon - dk) * 4), math.floor((lon + dk) * 4) + 1):
                fine.add((a, b))
        dl, dk = COARSE_KM / 111.32, COARSE_KM / (111.32 * math.cos(math.radians(lat)))
        for a in range(math.floor((lat - dl) / BLOCK) * BLOCK, math.floor((lat + dl) / BLOCK) * BLOCK + 1, BLOCK):
            for b in range(math.floor((lon - dk) / BLOCK) * BLOCK, math.floor((lon + dk) / BLOCK) * BLOCK + 1, BLOCK):
                blocks.add((a, b))
    for la4, lo4 in fine:
        blocks.add((math.floor(la4 / 4 / BLOCK) * BLOCK, math.floor(lo4 / 4 / BLOCK) * BLOCK))
    print(f"{len(fine)} fine tiles in {len(blocks)} blocks; {sum(1 for k in fine if k in CUDEM)} have CUDEM", flush=True)
    index = {"fine": [], "coarse": [], "fine_res": FINE, "coarse_res": COARSE, "block": BLOCK, "fmin": FMIN, "fmax": FMAX, "src": {}}

    def do_block(blk):
        la0, lo0 = blk
        z, lat_c, lon_c = crm_grid(la0 - M, la0 + BLOCK + M, lo0 - M, lo0 + BLOCK + M, COARSE)
        if np.isnan(z).all() or not (z < 0).any():
            return blk, None
        water, h, href, fc, axis, strength = coarse_terms(z, la0 + BLOCK / 2)
        fd = np.clip(np.sqrt(h / np.maximum(href, 0.3)), 0.35, 2.2)
        f = np.clip(fd * fc, FMIN, FMAX)
        m = round(M / COARSE); n = round(BLOCK / COARSE)
        sl = (slice(m, m + n), slice(m, m + n))
        res = {"coarse": None, "fine": []}
        if water[sl].any():
            encode(water[sl], h[sl], f[sl], axis[sl], strength[sl]).save(OUT / f"c_{la0}_{lo0}.png", optimize=True)
            res["coarse"] = f"{la0}_{lo0}"
        # doubled-angle components so the axis interpolates smoothly
        ac, as_ = np.cos(np.radians(axis * 2)) * strength, np.sin(np.radians(axis * 2)) * strength
        for la4, lo4 in sorted(k for k in fine if math.floor(k[0] / 4 / BLOCK) * BLOCK == la0 and math.floor(k[1] / 4 / BLOCK) * BLOCK == lo0):
            flat = la4 / 4 + (np.arange(round(0.25 / FINE)) + 0.5) * FINE
            flon = lo4 / 4 + (np.arange(round(0.25 / FINE)) + 0.5) * FINE
            zf, src = cudem_tile(la4, lo4, flat, flon)
            base, _, _ = crm_grid(la4 / 4, la4 / 4 + 0.25, lo4 / 4, lo4 / 4 + 0.25, COARSE / 3)   # CRM at native 3"
            # upsample CRM (bilinear) under the CUDEM, to fill its gaps or stand in where there is no CUDEM tile
            bj = (np.arange(len(flat)) + 0.5) * (FINE / (COARSE / 3)) - 0.5
            up = map_coordinates(np.nan_to_num(base, nan=-200.0), np.meshgrid(bj, bj, indexing="ij"), order=1, mode="nearest")
            zf = up if zf is None else np.where(np.isnan(zf), up, zf)
            src = src or "3 (CRM)"
            jj = (flat - lat_c[0]) / COARSE; ii = (flon - lon_c[0]) / COARSE
            J, I = np.meshgrid(jj, ii, indexing="ij")
            itp = lambda a: map_coordinates(a, [J, I], order=1, mode="nearest")
            wf = zf < 0
            hf = np.maximum(-zf, 0.3)
            ff = np.clip(np.clip(np.sqrt(hf / np.maximum(itp(href), 0.3)), 0.35, 2.2) * itp(fc), FMIN, FMAX)
            c2, s2 = itp(ac), itp(as_)
            ax = (np.degrees(np.arctan2(s2, c2)) / 2) % 180; stf = np.hypot(c2, s2)
            if not wf.any():
                continue
            encode(wf, hf, ff, ax, stf).save(OUT / f"f_{la4}_{lo4}.png", optimize=True)
            res["fine"].append((f"{la4}_{lo4}", src))
        return blk, res

    done = 0
    with ThreadPoolExecutor(max_workers=3) as ex:
        for blk, res in ex.map(do_block, sorted(blocks)):
            done += 1
            if res:
                if res["coarse"]:
                    index["coarse"].append(res["coarse"])
                for k, src in res["fine"]:
                    index["fine"].append(k); index["src"][src] = index["src"].get(src, 0) + 1
            print(f"  block {blk} done ({done}/{len(blocks)}, {len(index['fine'])} fine tiles, {time.time() - t0:.0f}s)", flush=True)
    (OUT / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    return station_factors(st, index, t0)


def station_factors(st, index, t0):
    samplers = {}
    # channel factor at each station: its own pixel, or the median of water within ~250 m when it sits on a land pixel
    fac = {}
    fine_set = set(index["fine"])
    for sid, (lat, lon) in st.items():
        k = f"{math.floor(lat * 4)}_{math.floor(lon * 4)}"
        if k not in fine_set:
            continue
        if k not in samplers:
            samplers[k] = np.array(Image.open(OUT / f"f_{k}.png"))[::-1]
        im = samplers[k]
        j, i = int((lat - math.floor(lat * 4) / 4) / FINE), int((lon - math.floor(lon * 4) / 4) / FINE)
        win = im[max(0, j - 5): j + 6, max(0, i - 5): i + 6, 0]
        v = im[min(j, im.shape[0] - 1), min(i, im.shape[1] - 1), 0]
        if v == 0:
            w = win[win > 0]
            if not len(w):
                continue
            v = int(np.median(w))
        fac[sid] = round(decode_f(int(v)), 3)
    (DATA_DIR / "depth_station_factors.json").write_text(json.dumps(fac))
    size = sum(p.stat().st_size for p in OUT.glob("*.png"))
    print(f"wrote {len(index['fine'])} fine tiles ({index['src']}), {len(index['coarse'])} blocks, {size // 1024 // 1024} MB; "
          f"factors for {len(fac)} stations ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    sys.exit(main())
