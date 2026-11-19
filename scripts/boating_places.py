"""Places for cruisers and liveaboards: anchorages, mooring fields and buoys, marinas, pump-outs, fuel, launch ramps and
other small-craft services, written to site/boating.json for the Cruising tab.

Sources (public records, no invented data):
  * NOAA Electronic Navigational Charts through NOAA ENC Direct (encdirect.noaa.gov): charted anchorage areas,
    mooring facilities (mooring buoys and fields), harbour facilities (marinas) and small-craft facilities
    (pump-out stations, fuel, visitor moorings, boatyards, launch sites, water, showers and so on). US waters.
  * OpenStreetMap through the Overpass API (overpass-api.de), when reachable: marinas, anchorages, moorings, pump-out
    and dump stations, fuel docks, slipways. OSM fee/charge/phone/website/opening-hours tags are kept, because the
    charts carry no prices. OSM data (c) OpenStreetMap contributors, ODbL.
Nothing here says a place is free or paid unless the source says so (OSM fee=no / fee=yes / charge=...).

Output: {"built": iso, "types": {...}, "p": [[type, lat, lon, name, info, cost, src, extra], ...]}
  cost: "free" | "paid" | "" (unknown); extra: dict of useful OSM tags (phone, website, charge, hours, depth, ...)
Usage: python -m scripts.boating_places
"""
from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from backend.config import ROOT

UA = {"User-Agent": "wind-analytics-prototype"}
ENC = "https://encdirect.noaa.gov/arcgis/rest/services/encdirect"
OVERPASS = "https://overpass-api.de/api/interpreter"

TYPES = {
    "anchorage": "Anchorage", "mooring_field": "Mooring field", "mooring": "Mooring buoy", "marina": "Marina",
    "pumpout": "Pump-out", "fuel": "Fuel", "ramp": "Launch ramp", "boatyard": "Boatyard", "water": "Water",
    "showers": "Showers", "laundry": "Laundry", "toilets": "Toilets", "provisions": "Provisions",
    "chandler": "Chandlery", "club": "Yacht club", "dinghy": "Dinghy landing", "other": "Other service",
}
# S-57 CATSCF (category of small craft facility) -> our type
CATSCF = {
    "visitor's berth": "marina", "visitors berth": "marina", "nautical club": "club", "boat hoist": "boatyard",
    "boatyard": "boatyard", "chandler": "chandler", "provisions": "provisions", "water tap": "water",
    "fuel station": "fuel", "showers": "showers", "laundrette": "laundry", "public toilets": "toilets",
    "sewerage pump-out station": "pumpout", "landing/launching place for boats": "ramp", "visitors mooring": "mooring",
    "visitor's mooring": "mooring", "scrubbing berth": "boatyard", "sailmaker": "chandler",
}


# S-57 attribute codes (IHO S-57 object catalogue), used where the service returns numbers instead of words
CATACH_CODES = {"1": "unrestricted anchorage", "2": "deep water anchorage", "3": "tanker anchorage", "4": "explosives anchorage",
                "5": "quarantine anchorage", "6": "sea-plane anchorage", "7": "small craft anchorage", "8": "small craft mooring area",
                "9": "anchorage for periods up to 24 hours"}
CATMOR_CODES = {"1": "dolphin", "2": "deviation dolphin", "3": "bollard", "4": "tie-up wall", "5": "post or pile", "6": "chain/wire/cable", "7": "mooring buoy"}
CATSCF_CODES = {"1": "visitor's berth", "2": "nautical club", "3": "boat hoist", "4": "sailmaker", "5": "boatyard", "6": "public inn", "7": "restaurant",
                "8": "chandler", "9": "provisions", "10": "doctor", "11": "pharmacy", "12": "water tap", "13": "fuel station", "14": "electricity",
                "15": "bottle gas", "16": "showers", "17": "launderette", "18": "public toilets", "19": "post box", "20": "public telephone",
                "21": "refuse bin", "22": "car park", "23": "parking for boats and trailers", "24": "caravan site", "25": "camping site",
                "26": "sewerage pump-out station", "27": "emergency telephone", "28": "landing/launching place for boats", "29": "visitors mooring",
                "30": "scrubbing berth", "31": "picnic area", "32": "mechanics workshop", "33": "guard and/or security service"}
CATHAF_CODES = {"5": "yacht harbour/marina"}


def words(v, codes):
    """'7' or '7,8' or 'small craft anchorage' -> list of lower-case words."""
    return [codes.get(x.strip(), x.strip()).lower() for x in clean(v).split(",") if x.strip()]


def get_json(url, data=None, tries=5, timeout=120):
    for a in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            if isinstance(d, dict) and d.get("error"):
                raise RuntimeError(d["error"].get("message"))
            return d
        except Exception as e:  # the ENC service fails now and then; wait and retry
            if a == tries - 1:
                print(f"  gave up: {url[:110]} ({e})", flush=True)
                return None
            time.sleep(4 * (a + 1))


def enc_layers(scale):
    d = get_json(f"{ENC}/{scale}/MapServer/layers?f=json")
    return {l["name"].split(".")[-1]: l["id"] for l in (d or {}).get("layers", [])}


def enc_features(scale, lid, fields):
    out, off = [], 0
    while True:
        q = urllib.parse.urlencode({"where": "1=1", "outFields": fields, "outSR": 4326, "f": "json", "resultOffset": off,
                                    "resultRecordCount": 1000, "maxAllowableOffset": 0.0005, "returnGeometry": "true"})
        d = get_json(f"{ENC}/{scale}/MapServer/{lid}/query?{q}")
        if not d:
            break
        fs = d.get("features", [])
        out += fs
        if len(fs) < 1000 and not d.get("exceededTransferLimit"):
            break
        off += len(fs)
    return out


def center(g):
    if "x" in g:
        return g["y"], g["x"]
    pts = [p for ring in g.get("rings") or g.get("paths") or [] for p in ring]
    if not pts:
        return None
    if g.get("rings"):  # polygon: area-weighted centroid of the outer ring, falls back to the mean
        r = g["rings"][0]; a = cx = cy = 0.0
        for (x0, y0), (x1, y1) in zip(r, r[1:] + r[:1]):
            c = x0 * y1 - x1 * y0; a += c; cx += (x0 + x1) * c; cy += (y0 + y1) * c
        if abs(a) > 1e-12:
            return cy / (3 * a), cx / (3 * a)
    return sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)


def clean(s):
    s = (s or "").strip()
    return "" if s in ("", " ", "None", "null") else s


def from_enc():
    P = []
    for scale in ("enc_harbour", "enc_approach", "enc_coastal"):
        L = enc_layers(scale)
        if not L:
            print(f"  {scale}: layer list unavailable", flush=True); continue
        pre = {"enc_harbour": "", "enc_approach": "", "enc_coastal": ""}[scale]
        for name, lid in L.items():
            n = name.lower()
            if "anchor_berth" in n:
                continue  # numbered berths inside big-ship anchorages, not places for small craft
            if "anchorage_area" in n:
                kind = "anchorage"; fields = "OBJNAM,CATACH,INFORM"
            elif "small_craft_facility" in n:
                kind = "scf"; fields = "OBJNAM,CATSCF,INFORM"
            elif "mooring_warping_facility" in n and not n.endswith("_line"):
                kind = "mor"; fields = "OBJNAM,CATMOR,BOYSHP,INFORM"
            elif "harbour_facility" in n:
                kind = "haf"; fields = "OBJNAM,CATHAF,INFORM"
            else:
                continue
            fs = enc_features(scale, lid, fields)
            kept = 0
            for f in fs:
                a, g = f.get("attributes", {}), f.get("geometry") or {}
                c = center(g)
                if not c:
                    continue
                nm, info = clean(a.get("OBJNAM")), clean(a.get("INFORM"))
                if kind == "anchorage":
                    cats = words(a.get("CATACH"), CATACH_CODES); cat = ", ".join(cats)
                    # skip big-ship, explosives, quarantine and seaplane anchorages; keep general / small craft ones
                    if any(w in cat for w in ("deep water", "tanker", "explosive", "quarantine", "sea-plane", "seaplane", "dangerous")):
                        continue
                    t = "mooring_field" if "mooring area" in cat else "anchorage"
                    info = "; ".join(x for x in (cat.replace("unristricted", "unrestricted"), info) if x)
                elif kind == "scf":
                    cats = words(a.get("CATSCF"), CATSCF_CODES)
                    ts = [CATSCF.get(c, "other") for c in cats] or ["other"]
                    t = "pumpout" if "pumpout" in ts else ts[0]
                    info = "; ".join(x for x in (", ".join(cats), info) if x)
                elif kind == "mor":
                    cat = ", ".join(words(a.get("CATMOR"), CATMOR_CODES))
                    # mooring buoys: category 7, or a buoy shape, or "buoy" in the charted name. Dolphins, bollards, piles
                    # and chains are not places to stay.
                    if "buoy" not in cat and a.get("BOYSHP") in (None, "", " ") and "buoy" not in nm.lower():
                        continue
                    t = "mooring_field" if g.get("rings") else "mooring"
                    info = "; ".join(x for x in (info, "charted mooring buoy: may be commercial, private or government; ask before using it") if x)
                else:  # harbour facility: keep marinas / yacht harbours
                    cat = ", ".join(words(a.get("CATHAF"), CATHAF_CODES))
                    if "marina" not in cat and "yacht" not in cat:
                        continue
                    t = "marina"; info = info or cat
                P.append([t, round(c[0], 5), round(c[1], 5), nm, info[:300], "", "NOAA chart", {}])
                kept += 1
            print(f"  {scale} {name}: {len(fs)} features, kept {kept}", flush=True)
    return P


OSM_Q = """[out:json][timeout:600];
area["ISO3166-1"="US"][admin_level=2]->.us;
(
  nwr["leisure"="marina"](area.us);
  nwr["seamark:type"="anchorage"](area.us);
  nwr["seamark:type"="mooring"]["seamark:mooring:category"="buoy"](area.us);
  nwr["mooring"](area.us);
  nwr["amenity"="sanitary_dump_station"]["boat"!="no"](area.us);
  nwr["seamark:small_craft_facility:category"~"pump"](area.us);
  nwr["waterway"="fuel"](area.us);
  nwr["leisure"="slipway"](area.us);
);
out tags center;"""


def osm_type(t):
    sm = t.get("seamark:type", "")
    if t.get("amenity") == "sanitary_dump_station" or "pump" in t.get("seamark:small_craft_facility:category", ""):
        if t.get("boat") in ("yes", "designated") or "seamark:type" in t or t.get("waterway"):
            return "pumpout"
        return None  # RV dump stations on land
    if t.get("leisure") == "marina":
        return "marina"
    if sm == "anchorage":
        return "anchorage"
    if sm == "mooring" or t.get("mooring"):
        return "mooring"
    if t.get("waterway") == "fuel":
        return "fuel"
    if t.get("leisure") == "slipway":
        return "ramp"
    return None


def from_osm():
    d = get_json(OVERPASS, data=urllib.parse.urlencode({"data": OSM_Q}).encode(), tries=3, timeout=700)
    if not d:
        print("  OpenStreetMap: not reachable, skipped", flush=True)
        return []
    P = []
    for e in d.get("elements", []):
        t = e.get("tags", {}); c = e.get("center") or ({"lat": e.get("lat"), "lon": e.get("lon")} if "lat" in e else None)
        ty = osm_type(t)
        if not ty or not c:
            continue
        fee = t.get("fee", "").lower()
        cost = "free" if fee == "no" else "paid" if fee in ("yes",) or t.get("charge") else ""
        extra = {k: t[k] for k in ("phone", "contact:phone", "website", "contact:website", "charge", "opening_hours", "capacity",
                                   "mooring", "access", "operator", "depth", "seamark:anchorage:category", "vhf", "seamark:radio_station:channel") if t.get(k)}
        P.append([ty, round(c["lat"], 5), round(c["lon"], 5), t.get("name", "") or t.get("seamark:name", ""), t.get("description", "")[:300], cost, "OpenStreetMap", extra])
    print(f"  OpenStreetMap: {len(P)} places", flush=True)
    return P


def dedupe(P):
    """Drop repeats (the same chart object at several chart scales, or charted and OSM copies of one place): same
    type within ~150 m. NOAA chart records win their information; OSM adds cost and contact details."""
    out, grid = [], {}
    order = sorted(P, key=lambda p: (p[6] != "NOAA chart", not p[3]))
    for p in order:
        key = (p[0], round(p[1] * 100), round(p[2] * 100))
        hit = None
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for q in grid.get((p[0], key[1] + dy, key[2] + dx), []):
                    if math.hypot((q[1] - p[1]) * 111, (q[2] - p[2]) * 111 * math.cos(math.radians(p[1]))) < 0.15:
                        hit = q
        if hit:
            if not hit[3] and p[3]:
                hit[3] = p[3]
            if not hit[5] and p[5]:
                hit[5] = p[5]
            hit[7].update({k: v for k, v in p[7].items() if k not in hit[7]})
            if p[6] not in hit[6]:
                hit[6] = hit[6] + " + " + p[6]
            continue
        grid.setdefault(key, []).append(p); out.append(p)
    return out


def tidy(P):
    """Merge pieces of one named area (charts split big anchorages into several polygons and repeat them at several
    scales): same type and same name within 5 km -> one place at their mean position. Shorten the charts' standard
    regulation sentence."""
    import re
    groups, out = {}, []
    for p in P:
        if p[3]:
            k = (p[0], p[3].lower())
            g = next((g for g in groups.get(k, []) if math.hypot((g[0][1] - p[1]) * 111, (g[0][2] - p[2]) * 111 * math.cos(math.radians(p[1]))) < 5), None)
            if g:
                g.append(p); continue
            groups.setdefault(k, []).append([p]); out.append(groups[k][-1])
        else:
            out.append([p])
    res = []
    for g in out:
        p = list(g[0])
        if len(g) > 1:
            p[1] = round(sum(q[1] for q in g) / len(g), 5); p[2] = round(sum(q[2] for q in g) / len(g), 5)
            p[4] = max((q[4] for q in g), key=len)
        p[4] = re.sub(r":?\s*Navigation regulations are published in Chapter (\d+) of the U\.?S\.? Coast Pilot[^.]*\.(\s*Refer to regulation section numbers\.)?",
                      r" (rules: US Coast Pilot, chapter \1)", p[4]).replace("unristricted", "unrestricted")
        p[4] = re.sub(r"\s*Refer to regulation section numbers\.?", "", p[4]).strip()
        res.append(p)
    return res


def main():
    t0 = time.time()
    P = from_enc() + from_osm()
    P = tidy(dedupe(P))
    for p in P:
        if not p[7]:
            p[7] = 0  # smaller file
    counts = {}
    for p in P:
        counts[p[0]] = counts.get(p[0], 0) + 1
    out = {"built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "types": TYPES, "p": P}
    (ROOT / "site" / "boating.json").write_text(json.dumps(out, separators=(",", ":")))
    print(f"{len(P)} places ({time.time() - t0:.0f}s): " + ", ".join(f"{TYPES[k]} {v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
