"""Things a boat must clear or respect, from NOAA Electronic Navigational Charts (encdirect.noaa.gov), for the
Cruising tab and its routing: bridges and overhead cables / pipelines with their charted vertical clearance, Coast
Guard stations, and restricted areas (no-wake zones, swimming areas, military areas, sanctuaries...).

Clearances are in metres as charted (NOAA charts give them above mean high water). Many bridges carry no clearance in
the chart data; those are kept with clearance null so the page can say "not charted" rather than guess.

Output site/nav.json:
  {"built": iso,
   "bridges": [[lat, lon, name, kind, clear_m, clear_closed_m, clear_open_m, width_m, [[lon, lat], ...]], ...],
   "cables":  [[lat, lon, kind, clear_m, [[lon, lat], ...]], ...],          kind: "cable" | "pipeline"
   "cg":      [[lat, lon, name, info], ...],
   "areas":   [[kind, name, info, [[[lon, lat], ...], ...]], ...]}
Usage: python -m scripts.nav_hazards
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone

from backend.config import ROOT
from scripts.boating_places import enc_features, enc_layers, clean, center

SCALES = ("enc_harbour", "enc_approach", "enc_coastal")
AREA_KINDS = {"no wake area", "swimming area", "military area", "nature reserve", "ecological reserve", "bird sanctuary",
              "seal sanctuary", "research area", "offshore safety zone", "degaussing range", "minefield", "dredging area",
              "navigational aid safety zone", "historic wreck area"}


def num(v):
    try:
        v = float(v)
        return round(v, 1) if math.isfinite(v) and v > 0 else None
    except (TypeError, ValueError):
        return None


def paths(g, step=1):
    out = []
    for ring in g.get("paths") or g.get("rings") or []:
        pts = [[round(x, 5), round(y, 5)] for x, y in ring[::step]]
        if ring and pts[-1] != [round(ring[-1][0], 5), round(ring[-1][1], 5)]:
            pts.append([round(ring[-1][0], 5), round(ring[-1][1], 5)])
        if len(pts) >= 2:
            out.append(pts)
    return out


def thin(ring, tol=0.0003):
    """Simplify an area outline (~30 m) so the file stays small; areas are drawn as a guide, not used for routing."""
    try:
        from shapely.geometry import LineString
        ls = LineString(ring).simplify(tol, preserve_topology=False)
        pts = [[round(x, 4), round(y, 4)] for x, y in ls.coords]
    except Exception:
        pts = [[round(x, 4), round(y, 4)] for x, y in ring]
    return pts if len(pts) >= 3 else None


def main():
    t0 = time.time()
    bridges, cables, cg, areas = {}, [], {}, []
    for scale in SCALES:
        L = enc_layers(scale)
        for name, lid in L.items():
            if name.startswith("Bridge_"):
                for f in enc_features(scale, lid, "OBJNAM,CATBRG,VERCLR,VERCCL,VERCOP,HORCLR"):
                    a, g = f.get("attributes", {}), f.get("geometry") or {}
                    c = center(g); ps = paths(g)
                    if not c or not ps:
                        continue
                    line = max(ps, key=len)
                    rec = [round(c[0], 5), round(c[1], 5), clean(a.get("OBJNAM")), clean(a.get("CATBRG")).lower(),
                           num(a.get("VERCLR")), num(a.get("VERCCL")), num(a.get("VERCOP")), num(a.get("HORCLR")), line[:40]]
                    key = (rec[2].lower() or f"{rec[0]:.3f},{rec[1]:.3f}", round(rec[0], 2), round(rec[1], 2))
                    old = bridges.get(key)
                    has = lambda r: sum(x is not None for x in r[4:8])
                    if not old or has(rec) > has(old):  # same bridge at several chart scales: keep the one with clearances
                        bridges[key] = rec
            elif name.startswith(("Cable_Overhead", "Pipeline_Overhead")):
                kind = "cable" if name.startswith("Cable") else "pipeline"
                for f in enc_features(scale, lid, "VERCLR"):
                    g = f.get("geometry") or {}; c = center(g); ps = paths(g)
                    if c and ps:
                        cables.append([round(c[0], 5), round(c[1], 5), kind, num(f.get("attributes", {}).get("VERCLR")), max(ps, key=len)[:40]])
            elif name.startswith("Coastguard_Station"):
                for f in enc_features(scale, lid, "OBJNAM,INFORM"):
                    a = f.get("attributes", {}); c = center(f.get("geometry") or {})
                    if c:
                        cg[(round(c[0], 3), round(c[1], 3))] = [round(c[0], 5), round(c[1], 5), clean(a.get("OBJNAM")) or "Coast Guard station", clean(a.get("INFORM"))[:200]]
            elif name.startswith("Restricted_Area"):
                for f in enc_features(scale, lid, "OBJNAM,CATREA,INFORM"):
                    a, g = f.get("attributes", {}), f.get("geometry") or {}
                    kinds = [k.strip().lower() for k in clean(a.get("CATREA")).split(",") if k.strip()]
                    kind = next((k for k in kinds if k in AREA_KINDS), None)
                    rings = [r for r in (thin(r) for r in paths(g)) if r]
                    if not kind or not rings:
                        continue
                    areas.append([kind, clean(a.get("OBJNAM")), clean(a.get("INFORM"))[:240], rings])
            else:
                continue
            print(f"  {scale} {name}", flush=True)
    # cables appear at several chart scales too: keep one per ~200 m
    seen, cab = set(), []
    for c in cables:
        k = (c[2], round(c[0] * 500), round(c[1] * 500))
        if k not in seen:
            seen.add(k); cab.append(c)
    seen, ar = set(), []
    for a in areas:
        k = (a[0], a[1], round(a[3][0][0][0], 3), round(a[3][0][0][1], 3))
        if k not in seen:
            seen.add(k); ar.append(a)
    out = {"built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "bridges": list(bridges.values()), "cables": cab, "cg": list(cg.values()), "areas": ar}
    p = ROOT / "site" / "nav.json"
    p.write_text(json.dumps(out, separators=(",", ":")))
    withclr = sum(1 for b in out["bridges"] if any(x is not None for x in b[4:7]))
    print(f"wrote {p} ({p.stat().st_size // 1024} KB, {time.time() - t0:.0f}s): {len(out['bridges'])} bridges ({withclr} with clearance), "
          f"{len(cab)} overhead cables/pipelines, {len(out['cg'])} Coast Guard stations, {len(ar)} restricted areas")


if __name__ == "__main__":
    main()
