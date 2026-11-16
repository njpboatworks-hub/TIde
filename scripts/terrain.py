"""Land height near the water, for the wind model (hills and cliffs shelter the water downwind of them).

Source: NOAA NCEI ETOPO 2022, 15 arc-second global relief (public domain), via NOAA CoastWatch ERDDAP, sampled every
3rd cell (~0.0125 degrees, ~1.2 km). Heights are above the nearby water: sea level on the coasts, lake level on the
Great Lakes (about 176 m; Lake Ontario about 75 m).

Writes <site region folder>/terrain.png: 8-bit grey, top row = north, covering the region's land raster box;
value = round(sqrt(height m) * 6) (0 = water or at water level), so height = (value / 6)^2.
Usage: python -m scripts.terrain [us west lakes hawaii iberia]
"""
from __future__ import annotations

import io
import json
import math
import sys
import time
import urllib.request

import numpy as np
from PIL import Image
from scipy.io import netcdf_file

from backend.config import DATA_DIR, ROOT

URL = "https://coastwatch.pfeg.noaa.gov/erddap/griddap/ETOPO_2022_v1_15s.nc?z%5B({a}):{s}:({b})%5D%5B({c}):{s}:({d})%5D"
STRIDE, RES = 3, 15 / 3600 * 3
REG = {"us": (DATA_DIR, ROOT / "site"), "west": (DATA_DIR / "west", ROOT / "site" / "west"), "lakes": (DATA_DIR / "lakes", ROOT / "site" / "lakes"),
       "hawaii": (DATA_DIR / "hawaii", ROOT / "site" / "hawaii"), "iberia": (DATA_DIR / "iberia", ROOT / "site" / "iberia")}


def fetch(url, tries=5):
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "wind-analytics-prototype"}), timeout=600) as r:
                return r.read()
        except Exception:
            time.sleep(5 * (a + 1))
    return None


def chunk(a, b, c, d):
    blob = fetch(URL.format(a=a, b=b, c=c, d=d, s=STRIDE))
    if not blob:
        raise RuntimeError(f"ETOPO request failed for {a},{b},{c},{d}")
    f = netcdf_file(io.BytesIO(blob), "r", mmap=False)
    return f.variables["z"][:].astype(np.float32), f.variables["latitude"][:].copy(), f.variables["longitude"][:].copy()


def main(keys):
    for key in keys:
        t0 = time.time()
        src, out = REG[key]
        g = json.loads((src / "coast_raster.json").read_text())
        lat0, lon0 = g["lat0"], g["lon0"]
        lat1, lon1 = lat0 + g["ny"] * g["res"], lon0 + g["nx"] * g["res"]
        ny, nx = int(math.ceil((lat1 - lat0) / RES)), int(math.ceil((lon1 - lon0) / RES))
        H = np.zeros((ny, nx), np.float32)
        for a in np.arange(lat0, lat1, 4.0):
            for c in np.arange(lon0, lon1, 4.0):
                z, la, lo = chunk(a, min(a + 4, lat1), c, min(c + 4, lon1))
                jj = np.clip(np.round((la - lat0) / RES - 0.5).astype(int), 0, ny - 1)
                ii = np.clip(np.round((lo - lon0) / RES - 0.5).astype(int), 0, nx - 1)
                H[np.ix_(jj, ii)] = z
        if key == "lakes":  # height above the lake surface, not above the sea
            la = lat0 + (np.arange(ny) + 0.5) * RES; lo = lon0 + (np.arange(nx) + 0.5) * RES
            LA, LO = np.meshgrid(la, lo, indexing="ij")
            H = H - np.where((LO > -79.95) & (LA < 44.4), 75.0, 176.0)
        code = np.clip(np.round(np.sqrt(np.clip(H, 0, None)) * 6), 0, 255).astype(np.uint8)
        Image.fromarray(code[::-1], "L").save(out / "terrain.png", optimize=True)
        meta = {"lat0": lat0, "lon0": lon0, "res": RES, "nx": nx, "ny": ny}
        (out / "terrain.json").write_text(json.dumps(meta))
        print(f"{key}: {nx}x{ny}, highest {float(H.max()):.0f} m, {(out / 'terrain.png').stat().st_size // 1024} KB ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:] or list(REG))
