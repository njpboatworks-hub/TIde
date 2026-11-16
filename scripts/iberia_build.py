"""Data for the Spain & Portugal region (mainland coasts).

Stages (run all, or name some):  python -m scripts.iberia_build [stations] [effects] [live] [tides] [places] [export]
  stations  coastal airports (IEM ES__ASOS / PT__ASOS), 2019-2024 hourly wind -> typical wind by month and local hour
  effects   how each station bends the regional breeze, by regional wind direction (May-Oct, 9:00-18:00 local)
  live      latest airport readings (IEM) + 7-day forecast from MET Norway (api.met.no, CC BY 4.0)
  tides     ~35 days of tide-gauge readings (IOC Sea Level Station Monitoring Facility) -> harmonic analysis (UTide)
            -> predicted highs and lows, heights in metres above the lowest predicted tide of the year
  places    GeoNames (CC BY 4.0) towns, bays, harbours and marinas near the coast
  export    site/iberia/{coast,live,tides}.json, merges shoreline tiles into site/tiles and places into site/places.json
"""
from __future__ import annotations

import base64
import io
import json
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from backend.config import DATA_DIR, ROOT
from scripts.coast_asos import hourly as asos_hourly
from scripts.region_iberia import BB, in_region, tz_for

D = DATA_DIR / "iberia"
OUT = ROOT / "site" / "iberia"
SCRATCH_SHORE = None  # set by export(): folder with ib_gshhs / ib_osm outputs
UA = {"User-Agent": "wind-analytics-prototype/1.0"}


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


# ---------------------------------------------------------------- stations
def stations():
    g = raster()
    cands = []
    for net in ("ES__ASOS", "PT__ASOS"):
        d = get(f"https://mesonet.agron.iastate.edu/geojson/network/{net}.geojson") or {}
        for f in d.get("features", []):
            lon, lat = f["geometry"]["coordinates"]
            p = f["properties"]
            if in_region(lat, lon) and near_water(g, lat, lon, 8):
                cands.append({"id": "asos_" + p["sid"], "sid": p["sid"], "name": p.get("sname", p["sid"]).title() + " (airport)",
                              "lat": round(lat, 4), "lon": round(lon, 4), "kind": "airport", "tz": tz_for(lat, lon)})
    print(f"{len(cands)} coastal airports in Spain and Portugal", flush=True)

    def clim(st):
        h = asos_hourly(st)
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
        if sum(x is not None for row in Dd for x in row) < 144:
            return None
        return {k: st[k] for k in ("id", "name", "lat", "lon", "kind", "tz")} | {"dir": Dd, "spd": S, "steady": T}

    out = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        for i, r in enumerate(ex.map(clim, cands)):
            if r:
                out.append(r)
    (D / "stations.json").write_text(json.dumps({"years": [2019, 2024], "stations": out}, separators=(",", ":")))
    print(f"wrote {len(out)} stations with typical wind", flush=True)


# ---------------------------------------------------------------- effects
def effects(SIGMA_KM=90.0, CUTOFF_KM=260.0, MIN_REG_KT=6.0, MIN_STN_KT=3.0, NSECT=16):
    sts = json.loads((D / "stations.json").read_text())["stations"]
    idx = pd.date_range("2019-01-01", "2024-12-31 23:00", freq="1h", tz="UTC")
    U = np.full((len(sts), len(idx)), np.nan, np.float32); V = U.copy(); S = U.copy(); keep = np.zeros((len(sts), len(idx)), bool)
    for i, st in enumerate(sts):
        h = asos_hourly({"id": st["id"], "sid": st["id"][5:]})
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
        O, R, C = [], [], []
        for k in range(NSECT):
            m = sect == k
            if m.sum() < 30:
                O.append(None); R.append(None); C.append(None); continue
            a = np.deg2rad(off[m])
            O.append(round(float(np.degrees(np.arctan2(np.sin(a).mean(), np.cos(a).mean()))), 1))
            R.append(round(float(np.median(ratio[m])), 2)); C.append(round(float(np.hypot(np.sin(a).mean(), np.cos(a).mean())), 2))
        out.append({"id": st["id"], "off": O, "ratio": R, "cons": C})
    (D / "effects.json").write_text(json.dumps({"sectors": NSECT, "season": "May-Oct, 9:00-18:00 local", "stations": out}, separators=(",", ":")))
    print(f"wrote direction effects for {len(out)} stations", flush=True)


# ---------------------------------------------------------------- live + forecast
def live(HOURS=168):
    now = datetime.now(timezone.utc)
    g = raster()
    obs = []
    for net in ("ES__ASOS", "PT__ASOS"):
        d = get(f"https://mesonet.agron.iastate.edu/api/1/currents.json?network={net}") or {}
        for r in d.get("data", []):
            lat, lon = r.get("lat"), r.get("lon")
            if lat is None or not in_region(lat, lon) or not near_water(g, lat, lon, 8) or r.get("drct") is None or r.get("sknt") is None:
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
    # forecast points: stations + open water every 0.25 degrees within ~40 km of the coast
    from scipy.ndimage import binary_dilation
    band = binary_dilation(g["cells"], iterations=int(40 / (g["res"] * 111.32))) & ~g["cells"]
    sts = json.loads((D / "stations.json").read_text())["stations"]
    pts = {(round(s["lat"], 3), round(s["lon"], 3)) for s in sts}
    for la in np.arange(BB[1] + 0.1, BB[3], 0.25):
        for lo in np.arange(BB[0] + 0.1, BB[2], 0.25):
            i, j = int((lo - g["lon0"]) / g["res"]), int((la - g["lat0"]) / g["res"])
            if 0 <= i < g["nx"] and 0 <= j < g["ny"] and band[j, i] and in_region(la, lo):
                pts.add((round(float(la), 3), round(float(lo), 3)))
    start = now.replace(minute=0, second=0, microsecond=0)

    def fc(p):
        d = get(f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={p[0]:.3f}&lon={p[1]:.3f}")
        ts = ((d or {}).get("properties") or {}).get("timeseries") or []
        if len(ts) < 24:
            return None
        t = np.array([(datetime.fromisoformat(x["time"].replace("Z", "+00:00")) - start).total_seconds() / 3600 for x in ts])
        det = [x["data"]["instant"]["details"] for x in ts]
        sp = np.array([x.get("wind_speed", np.nan) for x in det]) * 1.943844
        dr = np.deg2rad([x.get("wind_from_direction", np.nan) for x in det])
        u, v = np.sin(dr) * sp, np.cos(dr) * sp
        hrs = np.arange(HOURS)
        ok = (hrs >= t.min()) & (hrs <= t.max())
        ui, vi = np.interp(hrs, t, u), np.interp(hrs, t, v)  # vector interpolation (MET steps go 1 h, then 6 h)
        dd = [int(round((np.degrees(np.arctan2(a, b)) + 360) % 360)) if k else None for a, b, k in zip(ui, vi, ok)]
        ss = [round(float(np.hypot(a, b)), 1) if k else None for a, b, k in zip(ui, vi, ok)]
        return {"lat": p[0], "lon": p[1], "d": dd, "s": ss}
    with ThreadPoolExecutor(max_workers=8) as ex:
        series = [f for f in ex.map(fc, sorted(pts)) if f]
    print(f"{len(series)} forecast series from MET Norway ({len(pts)} points)", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "live.json").write_text(json.dumps({"generated": now.isoformat(timespec="minutes"), "obs": obs,
                                               "fc": {"start": start.isoformat(), "hours": HOURS, "points": series},
                                               "sources": "Iowa Environmental Mesonet (airports), MET Norway (forecast, CC BY 4.0)"},
                                              separators=(",", ":")))


# ---------------------------------------------------------------- tides
NAMES = {"acoruna": "A Coruña", "a coruña": "A Coruña", "la coruña": "A Coruña", "villagarcia": "Vilagarcía de Arousa", "marin": "Marín",
         "huelva": "Huelva", "bonanza": "Bonanza (Guadalquivir mouth)", "sevilla": "Seville (river port)", "gijon": "Gijón",
         "malaga": "Málaga", "almeria": "Almería", "murcia": "Escombreras (Cartagena)", "cadiz": "Cádiz", "ferrol": "Ferrol",
         "port of barcelona": "Barcelona (port)", "alicante": "Alicante", "motril": "Motril", "bilbao": "Bilbao"}


def clean_name(n: str) -> str:
    import re
    base = re.sub(r"\d+$", "", n.strip()).strip()
    return NAMES.get(base.lower(), base)


def tides(DAYS_HIST=35, DAYS_AHEAD=30):
    import utide
    lst = get("https://www.ioc-sealevelmonitoring.org/service.php?query=stationlist&showall=all") or []
    seen, gauges = set(), []
    for s in lst:
        try:
            lat, lon = float(s["Lat"]), float(s["Lon"])
        except Exception:
            continue
        if not in_region(lat, lon) and not (36.1 < lat < 36.2 and -5.4 < lon < -5.3):  # keep Gibraltar
            continue
        key = (round(lat, 2), round(lon, 2))
        if s["Code"] in seen:
            continue
        seen.add(s["Code"])
        gauges.append({"code": s["Code"], "name": s.get("Location") or s["Code"], "lat": lat, "lon": lon, "key": key})
    print(f"{len(gauges)} tide gauges in the region", flush=True)
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    begin = end - timedelta(days=DAYS_HIST)

    cache_dir = D / "gauges"; cache_dir.mkdir(exist_ok=True)

    def history(gauge):
        cf = cache_dir / f"{gauge['code']}_{end:%Y%m%d}.csv"
        if cf.exists():
            h = pd.read_csv(cf, index_col=0, parse_dates=True).iloc[:, 0]
            return h if h.notna().sum() > 24 * 20 else None
        parts = []
        t = begin
        while t < end:
            t2 = min(end, t + timedelta(days=7))
            d = get(f"https://www.ioc-sealevelmonitoring.org/service.php?query=data&code={gauge['code']}&timestart={t:%Y-%m-%dT%H:%M}&timestop={t2:%Y-%m-%dT%H:%M}&format=json", timeout=300)
            if isinstance(d, list) and d:
                df = pd.DataFrame(d)
                if "sensor" in df:  # keep the most common sensor only
                    df = df[df["sensor"] == df["sensor"].mode().iloc[0]]
                parts.append(pd.Series(df["slevel"].astype(float).values, index=pd.to_datetime(df["stime"], utc=True)))
            t = t2
        if not parts:
            return None
        s = pd.concat(parts).sort_index()
        s = s[~s.index.duplicated()]
        med = s.rolling("3h", center=True).median()
        s = s[(s - med).abs() < 0.5]  # drop spikes
        h = s.resample("1h").mean()
        h.to_csv(cf)
        return h if h.notna().sum() > 24 * 20 else None

    t0 = end - timedelta(days=1)
    results = []

    def analyse(gauge):
        h = history(gauge)
        if h is None:
            return None
        h = h.dropna()
        # Quality check: fit on all but the last 7 days, predict those 7 days, compare with what the gauge measured.
        cut = h.index.max() - pd.Timedelta(days=7)
        tr, te = h[h.index <= cut], h[h.index > cut]
        skill = err_cm = None
        if len(tr) > 24 * 20 and len(te) > 24 * 4:
            c0 = utide.solve(tr.index.tz_convert(None).values, tr.values - tr.values.mean(), lat=gauge["lat"], method="ols", conf_int="none", trend=False, verbose=False)
            k0 = [n for n, f in zip(c0.name, c0.aux.frq) if f > 1 / 30]
            e = te.values - (utide.reconstruct(te.index.tz_convert(None).values, c0, constit=k0, verbose=False).h + tr.values.mean())
            e = e - e.mean()
            skill = float(1 - np.var(e) / np.var(te.values - te.values.mean())); err_cm = float(np.sqrt(np.mean(e ** 2)) * 100)
        tt = h.index.tz_convert(None).values  # UTide needs real dates (datetime64), not plain day numbers
        coef = utide.solve(tt, h.values - h.values.mean(), lat=gauge["lat"], method="ols", conf_int="none", trend=False, verbose=False)
        # Five weeks cannot separate slow (fortnightly, monthly, seasonal) terms from weather-driven sea level,
        # so keep only diurnal and faster constituents (frequency above ~0.8 cycles per day).
        keep = [n for n, f in zip(coef.name, coef.aux.frq) if f > 1 / 30]
        # datum: lowest predicted level over the coming year (close to chart datum)
        year = pd.date_range(t0, t0 + timedelta(days=366), freq="30min")
        ty = year.tz_convert(None).values
        yfit = utide.reconstruct(ty, coef, constit=keep, verbose=False).h
        low = float(np.nanmin(yfit))
        win = pd.date_range(t0, t0 + timedelta(days=DAYS_AHEAD + 1), freq="6min")
        tw = win.tz_convert(None).values
        y = utide.reconstruct(tw, coef, constit=keep, verbose=False).h - low
        ev = []
        for k in range(1, len(y) - 1):
            if (y[k] > y[k - 1] and y[k] >= y[k + 1]) or (y[k] < y[k - 1] and y[k] <= y[k + 1]):
                kind = "H" if y[k] > y[k - 1] else "L"
                mins = int((win[k] - t0).total_seconds() // 60)
                if ev and ev[-1][2] == kind:  # keep alternating highs and lows
                    continue
                if ev and (mins - ev[-1][0] < 150 or abs(y[k] * 100 - ev[-1][1]) < 1):  # tiny wiggle, not a real tide turn
                    ev.pop()
                    continue
                ev.append([int((win[k] - t0).total_seconds() // 60), int(round(y[k] * 100)), kind])
        amp = (np.nanmax(yfit) - low)
        if amp < 0.05 or len(ev) < 6:
            return None
        quality = None if skill is None else "good" if skill >= 0.9 else "fair" if skill >= 0.75 else "weak"
        return {"id": gauge["code"], "name": clean_name(gauge["name"]), "lat": round(gauge["lat"], 5), "lon": round(gauge["lon"], 5), "ev": ev,
                "range_m": round(float(amp), 2), "days": int(h.index.normalize().nunique()),
                "skill": None if skill is None else round(skill, 3), "err_cm": None if err_cm is None else round(err_cm, 1), "quality": quality}

    with ThreadPoolExecutor(max_workers=4) as ex:
        for r in ex.map(analyse, gauges):
            if r:
                results.append(r)
    # one gauge per harbour: within 3 km keep the one that predicted best
    results.sort(key=lambda r: -(r["skill"] or 0))
    tide_list = []
    for r in results:
        if all(np.hypot((r["lat"] - q["lat"]) * 111.32, (r["lon"] - q["lon"]) * 111.32 * np.cos(np.deg2rad(r["lat"]))) > 3 for q in tide_list):
            tide_list.append(r)
    for name in {t["name"] for t in tide_list}:  # same name twice (e.g. two Ferrol gauges): say which is which
        same = sorted([t for t in tide_list if t["name"] == name], key=lambda t: t["lon"])
        if len(same) == 2:
            same[0]["name"] += " (outer)"; same[1]["name"] += " (inner)"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tides.json").write_text(json.dumps({"start": t0.isoformat(), "timebase": "utc", "units": "m", "datum": "lowest predicted tide",
                                                "quality_note": "skill = share of the tide explained when predicting a week the fit never saw",
                                                "source": "Calculated from IOC sea-level station data (harmonic analysis); not official predictions",
                                                "currents": [], "tides": tide_list}, separators=(",", ":")))
    print(f"wrote tide predictions for {len(tide_list)} harbours", flush=True)


# ---------------------------------------------------------------- places
def places():
    g = raster()
    from scipy.ndimage import binary_dilation
    near20 = binary_dilation(~g["cells"], iterations=int(20 / (g["res"] * 111.32)))

    def coastal(lat, lon):
        i, j = int((lon - g["lon0"]) / g["res"]), int((lat - g["lat0"]) / g["res"])
        return 0 <= i < g["nx"] and 0 <= j < g["ny"] and bool(near20[j, i])
    water_codes = {"BAY", "COVE", "HBR", "INLT", "LGN", "ESTY", "STRT", "CHN"}
    site_codes = {"PRT", "MRNA", "LTHSE", "CAPE", "PT"}
    out = []
    for cc, label in (("ES", "Spain"), ("PT", "Portugal")):
        z = zipfile.ZipFile(io.BytesIO(get(f"https://download.geonames.org/export/dump/{cc}.zip", as_json=False, timeout=600)))
        with z.open(f"{cc}.txt") as f:
            for line in io.TextIOWrapper(f, encoding="utf-8"):
                c = line.rstrip("\n").split("\t")
                if len(c) < 15:
                    continue
                name, lat, lon, fcl, fcode, pop = c[1], float(c[4]), float(c[5]), c[6], c[7], int(c[14] or 0)
                if not in_region(lat, lon) or not coastal(lat, lon):
                    continue
                if fcl == "P" and (pop >= 1000 or fcode in ("PPLA", "PPLA2", "PPLC")):
                    out.append([name, label, round(lat, 4), round(lon, 4), "town", pop])
                elif fcode in water_codes or fcode in site_codes:
                    out.append([name, label, round(lat, 4), round(lon, 4), "place", 0])
    out.sort(key=lambda x: -x[5])
    (D / "places.json").write_text(json.dumps([x[:5] for x in out], ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {len(out)} places", flush=True)


# ---------------------------------------------------------------- export
def export():
    import shutil
    OUT.mkdir(parents=True, exist_ok=True)
    land = json.loads((D / "coast_land.json").read_text())
    ras = json.loads((D / "coast_raster.json").read_text())
    st = json.loads((D / "stations.json").read_text())
    eff = json.loads((D / "effects.json").read_text()) if (D / "effects.json").exists() else None
    coast = {"years": st["years"], "stations": st["stations"], "land": land, "raster": ras}
    if eff:
        coast["effects"] = eff
    (OUT / "coast.json").write_text(json.dumps(coast, separators=(",", ":")))
    # shoreline tiles: OpenStreetMap near the coast, GSHHS elsewhere, merged into the shared site/tiles index
    tiles_dir = ROOT / "site" / "tiles"
    idx = json.loads((tiles_dir / "index.json").read_text())
    have = set(idx["tiles"]) | set(idx["land"])
    for src in ("osm", "gshhs"):
        d = D / f"tiles_{src}"
        sub = json.loads((d / "index.json").read_text())
        for k in sub["tiles"]:
            if k not in have:
                shutil.copy(d / f"{k}.json", tiles_dir / f"{k}.json"); idx["tiles"].append(k); have.add(k)
        for k in sub["land"]:
            if k not in have:
                idx["land"].append(k); have.add(k)
    (tiles_dir / "index.json").write_text(json.dumps(idx))
    # search: add Spanish/Portuguese places and stations to site/places.json (tagged with the region)
    pl = json.loads((ROOT / "site" / "places.json").read_text())
    pl = [p for p in pl if not (len(p) > 5 and p[5] == "iberia")]
    for p in json.loads((D / "places.json").read_text()):
        pl.append(p + ["iberia"])
    for s in st["stations"]:
        pl.append([s["name"], "wind station", s["lat"], s["lon"], "wind", "iberia"])
    tdf = OUT / "tides.json"
    if tdf.exists():
        for t in json.loads(tdf.read_text())["tides"]:
            pl.append([t["name"], "tide gauge (calculated)", t["lat"], t["lon"], "tide", "iberia"])
    (ROOT / "site" / "places.json").write_text(json.dumps(pl, ensure_ascii=False, separators=(",", ":")))
    print("exported site/iberia/*.json, merged tiles and places", flush=True)


if __name__ == "__main__":
    D.mkdir(parents=True, exist_ok=True)
    stages = sys.argv[1:] or ["stations", "effects", "live", "tides", "places", "export"]
    for st in stages:
        t = time.time()
        globals()[st]()
        print(f"[{st}] done in {time.time() - t:.0f}s", flush=True)
