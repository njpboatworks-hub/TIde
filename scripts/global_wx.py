"""Worldwide wind, weather and ocean-current forecast for the Wind Map and Forecast tabs.

Sources (both NOAA, public domain, read from the NOAA Open Data Dissemination buckets on AWS):
  * GFS (Global Forecast System) 0.5 degree, newest run with all 384 hours (16 days): 10 m wind, gusts, 2 m temperature,
    precipitation rate, total cloud, sea-level pressure. Only the needed fields are read, with HTTP byte ranges from
    the .idx inventory files.
  * RTOFS (Global Real-Time Ocean Forecast System, HYCOM 1/12 degree), newest run: surface current = layer-1
    velocity + barotropic velocity, rotated from the model grid to east/north, averaged onto 0.25 degree cells.
    Out to 8 days. RTOFS is ocean circulation (Gulf Stream, eddies, wind drift); it does not include tidal currents,
    which the Coast Map models separately near shore.

Writes site/global/:
  meta.json            run times, step times, encodings
  w/<k>.png            GFS step k, 720x361 RGB: R,G = u,v wind (m/s, -40..40), B = gust (0..60 m/s); row 0 = 90N, col 0 = 180W
  x/<k>.png            GFS step k, RGB: R = temperature (-50..50 C), G = sqrt(rain mm/h / 50), B = cloud %
  c/<k>.png            RTOFS step k, 1440x720 RGB: R,G = u,v current (m/s, -2.5..2.5), B = 255 on water, 0 on land
  p/<la>_<lo>.bin.gz     point-forecast tiles, 10x10 degrees at 1 degree (11x11 points with the shared edges),
                       uint8 [var][step][row][col], vars u,v,gust,temp,rain,cloud,pressure (scales in meta.json)
  q/<la>_<lo>.bin.gz     current tiles, 10x10 degrees at 0.25 degree (41x41 points), uint8 [u|v][step][row][col], 255 = land

Usage: python -m scripts.global_wx [gfs] [rtofs]
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import gzip
import io
import json
import math
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

from backend.config import ROOT

OUT = ROOT / "site" / "global"
UA = {"User-Agent": "wind-analytics-prototype"}
GFS = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
RTOFS = "https://noaa-nws-rtofs-pds.s3.amazonaws.com"
GFS_STEPS = list(range(0, 121, 3)) + list(range(126, 241, 6)) + list(range(252, 385, 12))
RTOFS_STEPS = list(range(6, 49, 6)) + list(range(60, 193, 12))
FIELDS = [("u", "UGRD:10 m above ground"), ("v", "VGRD:10 m above ground"), ("gust", "GUST:surface"),
          ("temp", "TMP:2 m above ground"), ("rain", "PRATE:surface"), ("cloud", "TCDC:entire atmosphere"),
          ("pres", "PRMSL:mean sea level")]
# uint8 encodings for the point tiles: value = lo + code * (hi - lo) / 254, code 255 = missing
TILE_ENC = {"u": (-40, 40), "v": (-40, 40), "gust": (0, 60), "temp": (-50, 50), "rain": (0, 1), "cloud": (0, 100), "pres": (940, 1060)}


def get(url, rng=None, tries=5, timeout=120):
    h = dict(UA)
    if rng:
        h["Range"] = f"bytes={rng[0]}-{rng[1] if rng[1] is not None else ''}"
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(3 * (a + 1))
        except Exception:
            time.sleep(3 * (a + 1))
    return None


def download(url, path, tries=4):
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600) as r, open(path, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 22)
            return True
        except Exception:
            time.sleep(10 * (a + 1))
    return False


def png(path, r, g, b):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.dstack([r, g, b]).astype(np.uint8), "RGB").save(path, optimize=True)


def q8(a, lo, hi):  # 0..255 linear, NaN -> 0
    return np.clip(np.round((np.nan_to_num(a, nan=lo) - lo) / (hi - lo) * 255), 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- GFS
def gfs_run():
    """Newest GFS cycle whose last (384 h) file is published."""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    for back in range(0, 48, 6):
        t = now - dt.timedelta(hours=back)
        t = t.replace(hour=t.hour // 6 * 6, minute=0, second=0, microsecond=0)
        base = f"{GFS}/gfs.{t:%Y%m%d}/{t:%H}/atmos/gfs.t{t:%H}z.pgrb2.0p50"
        if get(f"{base}.f384.idx"):
            return t, base
    raise RuntimeError("no complete GFS run found in the last 2 days")


def gfs_step(base, fh):
    import eccodes
    idx = get(f"{base}.f{fh:03d}.idx")
    if not idx:
        raise RuntimeError(f"missing GFS f{fh:03d}")
    lines = idx.decode().strip().split("\n")
    offs = [int(l.split(":")[1]) for l in lines]
    out = {}
    for name, key in FIELDS:
        for n, l in enumerate(lines):
            if f":{key}:" in l and not (name == "rain" and fh == 0):
                end = offs[n + 1] - 1 if n + 1 < len(offs) else None
                msg = get(f"{base}.f{fh:03d}", (offs[n], end))
                h = eccodes.codes_new_from_message(msg)
                ni, nj = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
                a = eccodes.codes_get_values(h).reshape(nj, ni).astype(np.float32)
                eccodes.codes_release(h)
                out[name] = np.roll(a, ni // 2, axis=1)  # 0..359.5E -> 180W..179.5E; rows already 90N..90S
                break
    if "rain" not in out:
        out["rain"] = np.zeros_like(out["u"])
    if "gust" not in out:
        out["gust"] = np.hypot(out["u"], out["v"])
    out["temp"] = out["temp"] - 273.15
    out["rain"] = out["rain"] * 3600  # kg/m2/s -> mm/h
    out["pres"] = out["pres"] / 100
    return out


def build_gfs(meta):
    t0 = time.time()
    run, base = gfs_run()
    print(f"GFS run {run:%Y-%m-%d %HZ}", flush=True)
    with cf.ThreadPoolExecutor(12) as ex:
        steps = list(ex.map(lambda fh: gfs_step(base, fh), GFS_STEPS))
    for k, s in enumerate(steps):
        png(OUT / "w" / f"{k}.png", q8(s["u"], -40, 40), q8(s["v"], -40, 40), q8(s["gust"], 0, 60))
        png(OUT / "x" / f"{k}.png", q8(s["temp"], -50, 50), q8(np.sqrt(np.clip(s["rain"], 0, 50) / 50), 0, 1), q8(s["cloud"], 0, 100))
    # point tiles at 1 degree: grid rows 90..-90, cols -180..179 (wrap col 360 = col 0)
    nst = len(steps)
    cube = {}
    for name in TILE_ENC:
        lo, hi = TILE_ENC[name]
        a = np.stack([s[name][::2, ::2] for s in steps])  # (nst, 181, 360)
        if name == "rain":
            a = np.sqrt(np.clip(a, 0, 50) / 50)
        c = np.clip(np.round((a - lo) / (hi - lo) * 254), 0, 254).astype(np.uint8)
        cube[name] = np.concatenate([c, c[:, :, :1]], axis=2)  # (nst, 181, 361)
    (OUT / "p").mkdir(parents=True, exist_ok=True)
    for f in (OUT / "p").glob("*.bin*"):
        f.unlink()
    for la in range(-90, 90, 10):
        for lo in range(-180, 180, 10):
            r0 = 90 - (la + 10)  # row of the tile's north edge
            rows = slice(r0, r0 + 11) if r0 + 11 <= 181 else slice(r0, 181)
            block = np.stack([cube[n][:, rows, lo + 180: lo + 191] for n in TILE_ENC])  # (7, nst, rows, 11)
            if block.shape[2] < 11:
                block = np.concatenate([block, np.repeat(block[:, :, -1:], 11 - block.shape[2], axis=2)], axis=2)
            (OUT / "p" / f"{la}_{lo}.bin.gz").write_bytes(gzip.compress(block.tobytes(), 9, mtime=0))
    meta["gfs"] = {"run": run.strftime("%Y-%m-%dT%H:00Z"), "hours": GFS_STEPS, "grid": {"nx": 720, "ny": 361, "res": 0.5, "lat0": 90, "lon0": -180},
                   "png": {"u": [-40, 40], "v": [-40, 40], "gust": [0, 60], "temp": [-50, 50], "rain_sqrt_max": 50, "cloud": [0, 100]},
                   "tile": {"deg": 10, "res": 1, "n": 11, "vars": list(TILE_ENC), "enc": TILE_ENC, "rain": "sqrt(mm_h/50)"}}
    print(f"GFS: {nst} steps out to {GFS_STEPS[-1]} h ({time.time() - t0:.0f}s)", flush=True)


# ---------------------------------------------------------------- RTOFS
def rtofs_run():
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    for back in range(0, 4):
        d = (now - dt.timedelta(days=back)).strftime("%Y%m%d")
        if get(f"{RTOFS}/rtofs.{d}/rtofs_glo.t00z.f{RTOFS_STEPS[-1]}.archs.b"):
            return d
    raise RuntimeError("no complete RTOFS run found")


def read_b(text):
    names = []
    for l in text.splitlines():
        m = re.match(r"^(\S[^=]*?)\s*=\s+\d+\s+[\d.]+\s+(\d+)", l)
        if m:
            names.append((m.group(1).strip(), int(m.group(2))))
    return names


def build_rtofs(meta):
    import netCDF4
    t0 = time.time()
    day = rtofs_run()
    print(f"RTOFS run {day} 00Z", flush=True)
    tmp = Path(tempfile.mkdtemp(prefix="rtofs_"))
    try:
        if not download(f"{RTOFS}/rtofs.{day}/rtofs_glo_2ds_f024_diag.nc", tmp / "grid.nc"):
            raise RuntimeError("RTOFS grid download failed")
        with netCDF4.Dataset(tmp / "grid.nc") as d:
            la = np.array(d["Latitude"][:], np.float64); lo = np.array(d["Longitude"][:], np.float64)
        (tmp / "grid.nc").unlink()
        ny, nx = la.shape
        lo = ((lo + 180) % 360) - 180
        # grid rotation: direction of increasing i in east/north components
        cl = np.cos(np.radians(la))
        dlo = ((np.roll(lo, -1, axis=1) - np.roll(lo, 1, axis=1) + 180) % 360) - 180
        dla = np.roll(la, -1, axis=1) - np.roll(la, 1, axis=1)
        ang = np.arctan2(dla, dlo * cl); ang[:, 0] = ang[:, 1]; ang[:, -1] = ang[:, -2]
        ca, sa = np.cos(ang).astype(np.float32), np.sin(ang).astype(np.float32)
        R = 0.25
        jj = np.clip(((90 - la) / R).astype(int), 0, 719); ii = np.clip(((lo + 180) / R).astype(int), 0, 1439)
        cell = (jj * 1440 + ii).ravel()
        del la, lo, dlo, dla, ang, cl, jj, ii
        n = nx * ny; npad = (n + 4095) // 4096 * 4096
        U, V, T, hours = [], [], [], []
        for fh in RTOFS_STEPS:
            name = f"rtofs_glo.t00z.f{fh:02d}.archs"
            btxt = get(f"{RTOFS}/rtofs.{day}/{name}.b")
            if not btxt or not download(f"{RTOFS}/rtofs.{day}/{name}.a.tgz", tmp / "a.tgz"):
                print(f"  skip f{fh}", flush=True); continue
            fields = read_b(btxt.decode(errors="replace"))
            pos = {f"{nm}{k}": i for i, (nm, k) in enumerate(fields)}
            with tarfile.open(tmp / "a.tgz") as tf:
                tf.extractall(tmp, filter="data")
            (tmp / "a.tgz").unlink()
            afile = next(tmp.glob("*.archs.a"))
            mm = np.memmap(afile, dtype=">f4", mode="r")
            F = lambda key: np.array(mm[pos[key] * npad: pos[key] * npad + n], np.float32).reshape(ny, nx)
            u = F("u_btrop0") + F("u-vel.1"); v = F("v_btrop0") + F("v-vel.1")
            sst = F("temp1") if "temp1" in pos else None  # top layer temperature = sea surface temperature, deg C
            del mm; afile.unlink()
            bad = ~(np.isfinite(u) & np.isfinite(v)) | (np.abs(u) > 100) | (np.abs(v) > 100)
            ue = (u * ca - v * sa); vn = (u * sa + v * ca)
            ue[bad] = 0; vn[bad] = 0
            w = (~bad).ravel().astype(np.float64)
            cnt = np.bincount(cell, w, 1440 * 720)
            su = np.bincount(cell, ue.ravel() * w, 1440 * 720); sv = np.bincount(cell, vn.ravel() * w, 1440 * 720)
            with np.errstate(invalid="ignore", divide="ignore"):
                U.append(np.where(cnt > 0, su / cnt, np.nan).reshape(720, 1440).astype(np.float32))
                V.append(np.where(cnt > 0, sv / cnt, np.nan).reshape(720, 1440).astype(np.float32))
            if sst is not None:
                ok = np.isfinite(sst) & (sst > -5) & (sst < 40) & ~bad
                wt = ok.ravel().astype(np.float64); nt = np.bincount(cell, wt, 1440 * 720)
                st = np.bincount(cell, np.where(ok, sst, 0).ravel() * wt, 1440 * 720)
                with np.errstate(invalid="ignore", divide="ignore"):
                    T.append(np.where(nt > 0, st / nt, np.nan).reshape(720, 1440).astype(np.float32))
            hours.append(fh)
            print(f"  f{fh:03d} max {float(np.nanmax(np.hypot(U[-1], V[-1]))):.2f} m/s", flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not hours:
        raise RuntimeError("no RTOFS steps decoded")
    water = np.isfinite(U[0])
    for k, (u, v) in enumerate(zip(U, V)):
        png(OUT / "c" / f"{k}.png", q8(u, -2.5, 2.5), q8(v, -2.5, 2.5), np.where(water, 255, 0))
    # sea surface temperature, same steps: R = -3..35 C, B = 255 on water
    if len(T) == len(hours):
        (OUT / "s").mkdir(parents=True, exist_ok=True)
        for k, t in enumerate(T):
            png(OUT / "s" / f"{k}.png", q8(t, -3, 35), np.zeros_like(t), np.where(np.isfinite(t), 255, 0))
        meta["sst"] = {"png": [-3, 35]}
        print(f"  water temperature {float(np.nanmin(T[0])):.1f} to {float(np.nanmax(T[0])):.1f} C", flush=True)
    else:
        meta.pop("sst", None)
    # current tiles, 41x41 at 0.25 degree; row 0 = north edge
    (OUT / "q").mkdir(parents=True, exist_ok=True)
    for f in (OUT / "q").glob("*.bin*"):
        f.unlink()
    cu = np.stack([np.where(water, np.clip(np.round((u + 2.5) / 5 * 254), 0, 254), 255) for u in U]).astype(np.uint8)
    cv = np.stack([np.where(water, np.clip(np.round((v + 2.5) / 5 * 254), 0, 254), 255) for v in V]).astype(np.uint8)
    cu = np.concatenate([cu, cu[:, :, :1], ], axis=2); cv = np.concatenate([cv, cv[:, :, :1]], axis=2)
    cu = np.concatenate([cu, cu[:, -1:, :]], axis=1); cv = np.concatenate([cv, cv[:, -1:, :]], axis=1)  # (k, 721, 1441)
    kept = 0
    for la in range(-80, 90, 10):
        for lo in range(-180, 180, 10):
            r0 = int((90 - (la + 10)) / 0.25); c0 = int((lo + 180) / 0.25)
            a, b = cu[:, r0:r0 + 41, c0:c0 + 41], cv[:, r0:r0 + 41, c0:c0 + 41]
            if (a[0] < 255).any():
                (OUT / "q" / f"{la}_{lo}.bin.gz").write_bytes(gzip.compress(np.stack([a, b]).tobytes(), 9, mtime=0)); kept += 1
    meta["rtofs"] = {"run": f"{day[:4]}-{day[4:6]}-{day[6:]}T00:00Z", "hours": hours, "grid": {"nx": 1440, "ny": 720, "res": 0.25, "lat0": 90, "lon0": -180},
                     "png": [-2.5, 2.5], "tile": {"deg": 10, "res": 0.25, "n": 41, "enc": [-2.5, 2.5]}}
    print(f"RTOFS: {len(hours)} steps to {hours[-1]} h, {kept} current tiles ({time.time() - t0:.0f}s)", flush=True)


def main(parts):
    OUT.mkdir(parents=True, exist_ok=True)
    mp = OUT / "meta.json"
    meta = json.loads(mp.read_text()) if mp.exists() else {}
    if "gfs" in parts:
        build_gfs(meta)
    if "rtofs" in parts:
        build_rtofs(meta)
    meta["built"] = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%MZ")
    mp.write_text(json.dumps(meta))


if __name__ == "__main__":
    main(sys.argv[1:] or ["gfs", "rtofs"])
