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

import gzip
import json
import re
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from backend.config import ROOT

UA = {"User-Agent": "wind-analytics-prototype"}
ENC = "https://encdirect.noaa.gov/arcgis/rest/services/encdirect"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.private.coffee/api/interpreter"]

TYPES = {
    "anchorage": "Anchorage", "mooring_field": "Mooring field", "mooring": "Mooring buoy", "marina": "Marina",
    "pumpout": "Pump-out", "fuel": "Fuel", "ramp": "Launch ramp", "boatyard": "Boatyard", "water": "Water",
    "showers": "Showers", "laundry": "Laundry", "toilets": "Toilets", "provisions": "Provisions",
    "chandler": "Chandlery", "club": "Yacht club", "eat": "Restaurant", "dinghy": "Dinghy landing", "other": "Other service",
    "reef": "Artificial reef", "wreck": "Wreck", "fishing": "Fishing spot",
    # things to see from the water or a short walk from the dinghy (OpenStreetMap)
    "s_light": "Lighthouse", "s_fort": "Fort or castle", "s_falls": "Waterfall", "s_view": "Viewpoint", "s_museum": "Maritime museum",
    "s_wreck": "Historic wreck", "s_ship": "Museum ship", "s_arch": "Natural arch", "s_cave": "Sea cave", "s_spring": "Hot spring",
    "s_blowhole": "Blowhole", "s_ruins": "Ruins",
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


OSM_V = 4  # bump when the query changes: every cached square is then fetched again (oldest first)
OSM_Q = """[out:json][timeout:240][bbox:{s},{w},{n},{e}];
(
  nwr["leisure"="marina"];
  nwr["leisure"="slipway"];
  nwr["man_made"="pier"];
  nwr["seamark:type"~"^(mooring|anchorage|harbour)$"];
  nwr["natural"="beach"];
  nwr["waterway"="dock"];
  nwr["man_made"="quay"];
)->.water;
(
  nwr["leisure"="marina"];
  nwr["seamark:type"="anchorage"];
  nwr["seamark:type"="mooring"];
  nwr["mooring"]["mooring"!="no"];
  nwr["amenity"="sanitary_dump_station"];
  nwr["waterway"="sanitary_dump_station"];
  nwr["seamark:small_craft_facility:category"];
  nwr["waterway"="fuel"];
  nwr["amenity"="fuel"]["boat"="yes"];
  nwr["leisure"="slipway"];
  nwr["club"~"^(sailing|yacht|boat|rowing)$"];
  nwr["sport"="sailing"]["leisure"~"^(sports_centre|club)$"];
  nwr["craft"="boatbuilder"];
  nwr["shop"="boat"];
  nwr["leisure"="fishing"];
  nwr["man_made"="pier"]["fishing"="yes"];
  nwr["name"~"(yacht|sailing|boat|corinthian|rowing|canoe|power squadron) club|yacht squadron|sailing (association|center|centre|school|foundation)|community sailing|boating club",i](around.water:1500);
  nwr["amenity"~"^(restaurant|bar|pub|cafe|fast_food|ice_cream)$"]["name"](around.water:400);
  nwr["man_made"="lighthouse"];
  nwr["historic"~"^(lighthouse|wreck|ship)$"];
  nwr["natural"="blowhole"];
  nwr["tourism"="museum"]["name"~"maritime|nautical|naval|navy|whaling|lighthouse|ship|boat|sea|marine|fisher|harbou?r|yacht|sailing|coast guard|lifesaving|maritim|museo del mar|mus.e de la mer",i];
  nwr["historic"~"^(fort|castle)$"](around.water:1500);
  nwr["natural"="arch"](around.water:1500);
  nwr["natural"="cave_entrance"](around.water:400);
  nwr["natural"="hot_spring"](around.water:2000);
  nwr["waterway"="waterfall"](around.water:2000);
  nwr["tourism"="viewpoint"]["name"](around.water:800);
  nwr["historic"="ruins"]["name"](around.water:600);
);
out tags center;"""


def osm_boxes():
    """Map squares to ask OpenStreetMap about (a whole-country query times out). 2 x 2.5 degree squares that hold at
    least one place from the NOAA charts (so coasts, the Great Lakes and the big rivers), plus Hawaii and Puerto Rico.
    Falls back to a coarse grid over the whole country when there is no NOAA list yet."""
    src = ROOT / "site" / "boating.json"
    B = set()
    if src.exists():
        for q in json.loads(src.read_text()).get("p", []):
            if q[6].startswith("NOAA") and q[1] >= 15 and q[0] not in ("wreck", "reef"):
                la, lo = math.floor(q[1] / 2) * 2, math.floor(q[2] / 2.5) * 2.5
                B.add((la, lo, la + 2, lo + 2.5))
    if not B:
        B = {(la, lo, min(la + 4, 50), lo + 5) for la in range(24, 50, 4) for lo in range(-125, -66, 5)}
    B |= {(18.5, -161.0, 22.5, -154.0), (17.5, -68.0, 18.7, -64.4)}
    # cruising grounds outside US charts: the Bahamas and Turks and Caicos, the Virgin Islands down the Lesser Antilles, Bermuda
    B |= {(la, lo, la + 2, lo + 2.5) for la in (20, 22, 24, 26) for lo in (-80.0, -77.5, -75.0, -72.5)}
    B |= {(la, lo, la + 2, lo + 2.5) for la in (12, 14, 16, 18) for lo in (-65.0, -62.5, -60.0)}
    B.add((32, -65.0, 34, -62.5))
    out = sorted(B)
    world = ROOT / "data" / "osm_world_squares.json"  # coastal squares of the world's main cruising grounds (built from Natural Earth land)
    if world.exists():
        order = ["Greater Antilles and Caribbean", "Gulf of Mexico, Yucatan, Central America", "Mediterranean", "Europe Atlantic and North Sea",
                 "Canada Atlantic", "BC and SE Alaska", "Mexico Pacific and Baja", "Australia", "New Zealand", "Baltic", "Brazil and Argentina coast"]
        W = json.loads(world.read_text()); have = set(out)
        for name in order + [k for k in W if k not in order]:
            for b in W.get(name, []):
                b = tuple(b)
                if b not in have: have.add(b); out.append(b)
    return out


CLUB_RE = re.compile(r"\b(yacht|sailing|boat|corinthian|rowing|canoe|boating)\s+club\b|\byacht\s+squadron\b|\bpower\s+squadron\b|\bsailing\s+(association|center|centre|school|foundation)\b|\bcommunity\s+sailing\b", re.I)
EAT = {"restaurant", "bar", "pub", "cafe", "fast_food", "ice_cream"}


SIGHT_MUSEUM_RE = re.compile(r"\b(maritime|nautical|naval|navy|whaling|lighthouse|ships?|boats?|sea|seaport|marine|fisher(y|ies|man|men)?|harbou?r|yacht\w*|sailing|coast guard|lifesaving|maritim\w*|museo del mar|mus.e de la mer)\b", re.I)


def sight_type(t):
    """Things worth seeing: lighthouses, forts, waterfalls... and the niche finds (wrecks, arches, sea caves, hot springs)."""
    h, n = t.get("historic", ""), t.get("natural", "")
    if t.get("man_made") == "lighthouse" or h == "lighthouse":
        return "s_light"
    if h == "wreck":
        return "s_wreck"
    if h == "ship":
        return "s_ship"
    if n == "blowhole":
        return "s_blowhole"
    if t.get("tourism") == "museum" and SIGHT_MUSEUM_RE.search(t.get("name", "")):
        return "s_museum"
    if h in ("fort", "castle"):
        return "s_fort"
    if n == "arch":
        return "s_arch"
    if n == "cave_entrance":
        return "s_cave"
    if n == "hot_spring":
        return "s_spring"
    if t.get("waterway") == "waterfall":
        return "s_falls"
    if t.get("tourism") == "viewpoint" and t.get("name"):
        return "s_view"
    if h == "ruins" and t.get("name"):
        return "s_ruins"
    return None


def osm_type(t):
    sm = t.get("seamark:type", ""); scf = t.get("seamark:small_craft_facility:category", "")
    st = sight_type(t)
    if st:
        return st
    if t.get("amenity") in EAT:
        return "eat" if t.get("name") else None
    if CLUB_RE.search(t.get("name", "")) and not t.get("highway") and t.get("amenity") not in ("parking", "toilets"):
        return "club"
    if t.get("amenity") == "sanitary_dump_station" or t.get("waterway") == "sanitary_dump_station" or "pump" in scf:
        if t.get("boat") in ("yes", "designated") or "seamark:type" in t or t.get("waterway") or t.get("leisure") == "marina":
            return "pumpout"
        return "pumpout?"  # could be an RV dump station on land: kept only if it is at a marina (see main)
    if scf:
        for word, ty in (("fuel", "fuel"), ("slipway", "ramp"), ("boat_hoist", "boatyard"), ("boatyard", "boatyard"), ("chandler", "chandler"),
                         ("water_tap", "water"), ("shower", "showers"), ("laundrette", "laundry"), ("toilets", "toilets"), ("visitor_berth", "marina"),
                         ("visitors_mooring", "mooring"), ("nautical_club", "club"), ("provisions", "provisions")):
            if word in scf:
                return ty
    if t.get("club") in ("sailing", "yacht", "boat", "rowing") or t.get("sport") == "sailing":
        return "club"
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
    if t.get("amenity") == "fuel" and t.get("boat") == "yes":
        return "fuel"
    if t.get("craft") == "boatbuilder":
        return "boatyard"
    if t.get("shop") == "boat":
        return "chandler"
    if t.get("leisure") == "fishing" or (t.get("man_made") == "pier" and t.get("fishing") == "yes"):
        return "fishing"
    return None


OSM_CACHE = ROOT / "data" / "osm"


def square_file(box, cache=None):
    return (cache or OSM_CACHE) / f"{box[0]:g}_{box[1]:g}.json.gz"


def square_age(box, cache=None):
    f = square_file(box, cache)
    if not f.exists():
        return float("inf")
    try:
        d = json.loads(gzip.decompress(f.read_bytes()))
        if d.get("failed"):  # timed out before: try again only once it is the oldest in the queue
            return time.time() - datetime.strptime(d["fetched"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp()
        if d.get("v", 1) != OSM_V:
            return 1e12 - 1  # made by an older query: refetch, but after squares that were never fetched
        return time.time() - datetime.strptime(d["fetched"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return float("inf")


def osm_fetch(part=0, parts=1, budget_s=2400, out_dir=None):
    """Ask OpenStreetMap about this part's squares (every parts-th one), squares never fetched or fetched longest ago
    first, until the time budget runs out. Each answered square is written to out_dir (default data/osm) as
    <south>_<west>.json.gz, so a later run only needs the squares that are missing or old."""
    out_dir = out_dir or OSM_CACHE
    out_dir.mkdir(parents=True, exist_ok=True)
    allb = osm_boxes()
    rank = {b: i for i, b in enumerate(allb)}
    boxes = sorted(allb[part::parts], key=lambda b: (-square_age(b), rank[b]))  # missing first, then in priority order, then oldest
    elements, ok, t0 = {}, 0, time.time()
    for k, (s_, w, n, e) in enumerate(boxes):
        if time.time() - t0 > budget_s:
            print(f"  time budget used: stopped after {k} of {len(boxes)} squares", flush=True)
            break
        q = OSM_Q.replace("{s}", str(s_)).replace("{w}", str(w)).replace("{n}", str(n)).replace("{e}", str(e))
        d = None
        for a in range(2):  # two servers at most, short waits, so one slow square cannot eat the budget
            url = OVERPASS[(k + part + a) % len(OVERPASS)]
            d = get_json(url, data=urllib.parse.urlencode({"data": q}).encode(), tries=1, timeout=200)
            if d:
                break
            time.sleep(5)
        if not d and not square_file((s_, w, n, e)).exists():  # never had data: note the failure so this square waits its turn next time
            square_file((s_, w, n, e), out_dir).write_bytes(gzip.compress(json.dumps(
                {"fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "v": OSM_V, "failed": True, "box": [s_, w, n, e], "elements": {}}).encode(), 9, mtime=0))
        if d:
            ok += 1
            els = {f"{el.get('type')}{el.get('id')}": el for el in d.get("elements", [])}
            elements.update(els)
            square_file((s_, w, n, e), out_dir).write_bytes(gzip.compress(json.dumps(
                {"fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "v": OSM_V, "box": [s_, w, n, e], "elements": els}, separators=(",", ":")).encode(), 9, mtime=0))
        time.sleep(1)
    print(f"  OpenStreetMap part {part + 1}/{parts}: {ok} of {len(boxes)} squares answered, {len(elements)} objects ({time.time() - t0:.0f}s)", flush=True)
    return elements


def from_osm(elements=None):
    if elements is None:
        elements = osm_fetch()
    if not elements:
        print("  OpenStreetMap: nothing to add", flush=True)
        return []
    P = []
    for e in elements.values():
        t = e.get("tags", {}); c = e.get("center") or ({"lat": e.get("lat"), "lon": e.get("lon")} if "lat" in e else None)
        ty = osm_type(t)
        if not ty or not c:
            continue
        fee = t.get("fee", "").lower()
        cost = "free" if fee == "no" else "paid" if fee in ("yes",) or t.get("charge") else ""
        extra = {k: t[k] for k in ("phone", "contact:phone", "website", "contact:website", "charge", "opening_hours", "capacity",
                                   "mooring", "access", "operator", "depth", "seamark:anchorage:category", "vhf", "seamark:radio_station:channel",
                                   "amenity", "cuisine", "outdoor_seating", "wikipedia", "start_date", "wreck:date_sunk", "height",
                                   "historic:period", "access:dinghy") if t.get(k)}
        P.append([ty, round(c["lat"], 5), round(c["lon"], 5), t.get("name", "") or t.get("seamark:name", ""), t.get("description", "")[:300], cost, "OpenStreetMap", extra])
    print(f"  OpenStreetMap: {len(P)} places", flush=True)
    return P


def from_enc_fishing():
    """Charted fish havens (artificial reefs) and wrecks: the spots anglers look for. Wrecks only from the coastal
    and approach charts (the harbour charts add thousands of small hulks in marinas)."""
    P = []
    for scale in ("enc_coastal", "enc_approach", "enc_harbour"):
        L = enc_layers(scale)
        for name, lid in L.items():
            if name.startswith("Obstruction") and not name.endswith("_line"):
                fs = enc_features(scale, lid, "OBJNAM,CATOBS,VALSOU,INFORM")
                kept = 0
                for f in fs:
                    a = f.get("attributes", {})
                    if "fish" not in clean(a.get("CATOBS")).lower():
                        continue
                    c = center(f.get("geometry") or {})
                    if not c:
                        continue
                    d = a.get("VALSOU"); info = "; ".join(x for x in (f"least depth {d:g} m" if isinstance(d, (int, float)) and d > 0 else "", clean(a.get("INFORM"))) if x)
                    P.append(["reef", round(c[0], 5), round(c[1], 5), clean(a.get("OBJNAM")), info[:300], "", "NOAA chart", {}]); kept += 1
                print(f"  {scale} {name}: kept {kept} fish havens", flush=True)
            elif name.startswith("Wreck") and scale != "enc_harbour":
                fs = enc_features(scale, lid, "OBJNAM,CATWRK,VALSOU,INFORM")
                for f in fs:
                    a = f.get("attributes", {}); c = center(f.get("geometry") or {})
                    if not c:
                        continue
                    d = a.get("VALSOU"); cat = clean(a.get("CATWRK")).lower()
                    info = "; ".join(x for x in (cat, f"least depth {d:g} m" if isinstance(d, (int, float)) and d > 0 else "", clean(a.get("INFORM"))) if x)
                    P.append(["wreck", round(c[0], 5), round(c[1], 5), clean(a.get("OBJNAM")), info[:300], "", "NOAA chart", {}])
                print(f"  {scale} {name}: {len(fs)} wrecks", flush=True)
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
                    if math.hypot((q[1] - p[1]) * 111, (q[2] - p[2]) * 111 * math.cos(math.radians(p[1]))) < 0.15 \
                            and (p[0] != "eat" or (p[3] or "").lower() == (q[3] or "").lower()):  # neighbouring restaurants are different places
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
        if p[3] and p[0] != "eat":  # restaurants: two branches of a chain are two places
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


def load_osm_cache(cache=None):
    """All saved squares -> {id: element}."""
    cache = cache or OSM_CACHE
    osm, n = {}, 0
    for f in sorted(cache.glob("*.json.gz")):
        try:
            d = json.loads(gzip.decompress(f.read_bytes()))
            if d.get("failed"): continue
            osm.update(d["elements"]); n += 1
        except Exception as e:
            print(f"  skipped {f.name}: {e}", flush=True)
    print(f"  OpenStreetMap: {len(osm)} objects from {n} of {len(osm_boxes())} squares", flush=True)
    return osm


def main(osm_dir=None):
    t0 = time.time()
    osm, carried = None, []
    if osm_dir:  # newly fetched squares (from the parallel jobs) replace the saved copies, then everything is merged
        import shutil
        OSM_CACHE.mkdir(parents=True, exist_ok=True)
        for f in Path(osm_dir).rglob("*.json.gz"):
            dst = OSM_CACHE / f.name
            try:  # a failed download must not replace a good saved copy (its places would vanish until the next success)
                if json.loads(gzip.decompress(f.read_bytes())).get("failed") and dst.exists() and not json.loads(gzip.decompress(dst.read_bytes())).get("failed"):
                    continue
            except Exception:
                pass
            shutil.copy(f, dst)
        osm = load_osm_cache()
    # OpenStreetMap places from the previous build in squares the cache does not cover yet are kept, so coverage only grows
    carried = []
    prev = ROOT / "site" / "boating.json"
    if osm is not None and prev.exists():
        have = {(f.name.split("_")[0], f.name.split("_")[1].replace(".json.gz", "")) for f in OSM_CACHE.glob("*.json.gz")}
        for q in json.loads(prev.read_text()).get("p", []):
            if q[6] == "OpenStreetMap":
                key = (f"{math.floor(q[1] / 2) * 2:g}", f"{math.floor(q[2] / 2.5) * 2.5:g}")
                if key not in have:
                    q[7] = q[7] or {}; carried.append(q)
        print(f"  kept {len(carried)} OpenStreetMap places from the last build for squares not fetched yet", flush=True)
    P = from_enc() + from_enc_fishing() + from_osm(osm) + (carried if osm_dir else [])
    cur = ROOT / "data" / "curated_places.json"
    if cur.exists():  # hand-checked places (for example Bahamas mooring fields) always included
        cp = json.loads(cur.read_text())["places"]
        P += [[t, la, lo, n, note, "", "Cruising guide list", {}] for t, la, lo, n, note in cp]
        print(f"  {len(cp)} hand-checked places", flush=True)
    # dump stations without a boat tag: keep the ones within ~400 m of a marina (marina pump-outs), drop the rest (RV sites)
    marinas = [(p[1], p[2]) for p in P if p[0] == "marina"]
    mgrid = {}
    for la, lo in marinas:
        mgrid.setdefault((round(la * 50), round(lo * 50)), []).append((la, lo))
    keep = []
    for p in P:
        if p[0] == "pumpout?":
            k = (round(p[1] * 50), round(p[2] * 50)); near = False
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    for la, lo in mgrid.get((k[0] + dy, k[1] + dx), []):
                        if math.hypot((la - p[1]) * 111, (lo - p[2]) * 111 * math.cos(math.radians(la))) < 0.4:
                            near = True
            if not near:
                continue
            p[0] = "pumpout"
        keep.append(p)
    P = keep
    # yacht, sailing and boat clubs are often mapped (or charted) as marinas: go by the name
    for p in P:
        if p[0] in ("marina", "other") and CLUB_RE.search(p[3] or ""):
            p[0] = "club"
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
    import sys
    a = sys.argv[1:]
    if a[:1] == ["osm-part"]:  # python -m scripts.boating_places osm-part K N OUT_DIR [budget_seconds]
        k, n, out = int(a[1]), int(a[2]), Path(a[3])
        osm_fetch(k, n, int(a[4]) if len(a) > 4 else 2400, out)
    elif a[:1] == ["--osm-dir"]:
        main(a[1])
    else:
        main()
