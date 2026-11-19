"""World towns and cities for the search box (all places of 15,000+ people).

Source: GeoNames cities15000 (download.geonames.org/export/dump, CC BY 4.0), with country and state/province names
from countryInfo.txt and admin1CodesASCII.txt. Writes site/world.json as [name, "State, Country", lat, lon, population].
Usage: python -m scripts.world_places <folder holding cities15000.zip, countryInfo.txt, admin1CodesASCII.txt>
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

from backend.config import ROOT


def main(folder):
    f = Path(folder)
    countries = {}
    for l in (f / "countryInfo.txt").read_text(encoding="utf-8").splitlines():
        if l and not l.startswith("#"):
            p = l.split("\t"); countries[p[0]] = p[4]
    admin = {}
    for l in (f / "admin1CodesASCII.txt").read_text(encoding="utf-8").splitlines():
        p = l.split("\t")
        if len(p) > 1:
            admin[p[0]] = p[1]
    out = []
    with zipfile.ZipFile(f / "cities15000.zip") as z:
        for l in io.TextIOWrapper(z.open("cities15000.txt"), encoding="utf-8"):
            p = l.rstrip("\n").split("\t")
            cc, a1 = p[8], p[10]
            where = ", ".join(x for x in (admin.get(f"{cc}.{a1}") if cc in ("US", "CA", "AU") else None, countries.get(cc, cc)) if x)
            out.append([p[1], where, round(float(p[4]), 4), round(float(p[5]), 4), int(p[14] or 0)])
    out.sort(key=lambda r: -r[4])
    (ROOT / "site" / "world.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"{len(out)} places, {(ROOT / 'site' / 'world.json').stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1])
