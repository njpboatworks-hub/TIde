"""Where boat sewage may and may not be discharged in US waters, for the Cruising tab (site/discharge.json).

Federal rule (EPA, Vessel Sewage FAQs, epa.gov/vessels-marinas-and-ports/vessel-sewage-faqs):
  "Untreated sewage discharges are prohibited within three miles from shore. In order to discharge within three miles,
   sewage must be treated using a U.S. Coast Guard-approved Type I or Type II MSD. Alternatively, sewage may be stored
   onboard in a holding tank (Type III MSD)." Treated and untreated discharges are both prohibited in No Discharge Zones.

Data:
  * No Discharge Zones: EPA "Sewage No Discharge Zones" feature service (services.arcgis.com, owner EPA OWOW).
  * The 3 nautical mile line: BOEM Submerged Lands Act boundary (gis.boem.gov), Atlantic, Pacific and Alaska regions.
    NOAA notes the Three Nautical Mile Line is generally the same as the states' Submerged Lands Act boundaries, with
    exceptions. The Gulf of Mexico is left out: off Texas and the west coast of Florida that boundary is 9 nautical
    miles, not 3, so it would show the wrong distance there.
Usage: python -m scripts.discharge_zones
"""
from __future__ import annotations

import json
import time
import urllib.parse
from datetime import datetime, timezone

from backend.config import ROOT
from scripts.boating_places import get_json

NDZ = "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/Sewage_No_Discharge_Zone_NDZ_locations_EPA_HQ_OWOW/FeatureServer/0"
SLA = {"Atlantic": "https://gis.boem.gov/arcgis/rest/services/BOEM_BSEE/ATL_Layers/MapServer/2",
       "Pacific": "https://gis.boem.gov/arcgis/rest/services/BOEM_BSEE/POC_Layers/MapServer/4",
       "Alaska": "https://gis.boem.gov/arcgis/rest/services/BOEM_BSEE/AK_Layers/MapServer/3",
       "Gulf (Louisiana, Mississippi, Alabama)": "https://gis.boem.gov/arcgis/rest/services/BOEM_BSEE/GOA_Layers/MapServer/4"}
# In the Gulf the Submerged Lands Act boundary is 3 nautical miles only off Louisiana, Mississippi and Alabama; off Texas
# and the Gulf coast of Florida it is 9, so only the part between Sabine Pass and Perdido Pass is used.
GULF_3NM_LON = (-93.84, -87.52)


def simplify(rings, tol=0.0015, min_area=2e-6):
    """Rings -> fewer points (~150 m tolerance); drops slivers smaller than ~0.02 km2. The server ignores
    maxAllowableOffset for these polygons, so it is done here."""
    from shapely.geometry import Polygon
    out = []
    for r in rings:
        if len(r) < 4:
            continue
        pg = Polygon(r)
        if not pg.is_valid:
            pg = pg.buffer(0)
        if pg.area < min_area:
            continue
        sp = pg.simplify(tol, preserve_topology=True)
        for g in getattr(sp, "geoms", [sp]):
            c = list(g.exterior.coords)
            if len(c) >= 4:
                out.append([[round(x, 4), round(y, 4)] for x, y in c])
    return out


def query(url, fields, offset_deg):
    out, off = [], 0
    while True:
        q = urllib.parse.urlencode({"where": "1=1", "outFields": fields, "outSR": 4326, "f": "json", "returnGeometry": "true",
                                    "maxAllowableOffset": offset_deg, "resultOffset": off, "resultRecordCount": 1000, "geometryPrecision": 4})
        d = get_json(f"{url}/query?{q}")
        if not d:
            return out if out else None
        fs = d.get("features", []); out += fs
        if len(fs) < 1000 and not d.get("exceededTransferLimit"):
            return out
        off += len(fs)


def main():
    t0 = time.time()
    out = {"built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "ndz": [], "line3": [], "regions": []}
    fs = query(NDZ, "NAME,State,Type_of_Designation,Description_of_Waterbody,URL_of_Federal_Register_Notice,Date_of_EPA_Determination", 0.0008)
    for f in fs or []:
        a, g = f["attributes"], f.get("geometry") or {}
        rings = simplify(g.get("rings", []))
        if not rings:
            continue
        d = a.get("Date_of_EPA_Determination")
        out["ndz"].append({"name": (a.get("NAME") or "").strip(), "state": (a.get("State") or "").strip(),
                           "type": (a.get("Type_of_Designation") or "").strip(), "desc": (a.get("Description_of_Waterbody") or "").strip()[:400],
                           "fr": (a.get("URL_of_Federal_Register_Notice") or "").strip(),
                           "date": datetime.fromtimestamp(d / 1000, timezone.utc).strftime("%Y-%m-%d") if d else "", "rings": rings})
    print(f"No Discharge Zones: {len(out['ndz'])}", flush=True)
    for region, url in SLA.items():
        fs = query(url, "OBJECTID", 0.002)
        if fs is None:
            print(f"3 nm line {region}: not available today", flush=True); continue
        n = 0
        for f in fs:
            for p in (f.get("geometry") or {}).get("paths", []):
                if region.startswith("Gulf"):
                    p = [q for q in p if GULF_3NM_LON[0] <= q[0] <= GULF_3NM_LON[1]]
                if len(p) >= 2:
                    out["line3"].append([[round(x, 4), round(y, 4)] for x, y in p]); n += 1
        out["regions"].append(region)
        print(f"3 nm line {region}: {n} pieces", flush=True)
    if not out["ndz"] and not out["line3"]:
        raise SystemExit("nothing downloaded; keeping the previous site/discharge.json")
    p = ROOT / "site" / "discharge.json"
    p.write_text(json.dumps(out, separators=(",", ":")))
    print(f"wrote {p} ({p.stat().st_size // 1024} KB, {time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
