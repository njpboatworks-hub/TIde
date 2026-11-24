"""Package the hosted page as a standalone website for your own domain.

Usage: python -m scripts.build_deploy
Writes deploy/sailiq-wind-site/ (upload this folder) and deploy/sailiq-wind-site.zip
"""
from __future__ import annotations

import shutil
from pathlib import Path

from backend.config import ROOT

SITE, OUT = ROOT / "site", ROOT / "deploy" / "sailiq-wind-site"
FILES = ["index.html", "config.js", "data.json", "coast.json", "live.json", "tides.json", "places.json", "logo-mark.png", "coach-kit.jpg", "favicon.ico", "favicon-nockd-32.png", "icon-nockd-192.png", "apple-touch-icon-nockd.png", "icon-nockd-512.png", "terrain.png", "terrain.json", "world.json", "surge.json", "states.json", "privacy.html", "check.html", "discharge.json", "nav.json", "marinezones.json"]

CONFIG = """// SailIQ Wind settings for your own website.
window.SAILIQ_CONFIG = {
  externalMaps: true,           // satellite imagery: USGS The National Map (US) and EOX Sentinel-2 cloudless (elsewhere), free, no key
  defaultBasemap: "satellite",  // satellite, drawn, or with a Mapbox token: mb-satellite, mb-streets, mb-outdoors
  // Optional: paste a Mapbox public token (starts with "pk.") to add Mapbox satellite, streets and outdoors maps.
  mapboxToken: "",
};
"""

# Header rules (the _headers format works on Cloudflare Pages and Netlify): let the Nockd iPhone app (which runs from inside the phone, not from this website) read the
# public map data files. Nothing private is served from this folder.
HEADERS = """/*
  Access-Control-Allow-Origin: *
"""

HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="description" content="Nockd: wind and weather forecasts, tides, currents, racing tools and a cruising map for boaters across US coasts, the Great Lakes, the Bahamas and the Caribbean.">
<link rel="icon" href="favicon.ico?v=nockd1" sizes="any">
<link rel="icon" type="image/png" sizes="32x32" href="favicon-nockd-32.png?v=nockd1">
<link rel="icon" type="image/png" sizes="192x192" href="icon-nockd-192.png?v=nockd1">
<link rel="apple-touch-icon" href="apple-touch-icon-nockd.png?v=nockd1">
<meta name="apple-mobile-web-app-title" content="Nockd">
<meta property="og:title" content="Nockd">
<meta property="og:image" content="icon-nockd-512.png">
<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>
</head><body>
"""


TILE = 5  # degrees: the Cruising tab loads only the squares on screen


def split_places():
    """site/boating.json (every place, worldwide) -> boating/index.json + boating/{lat}_{lon}.json 5-degree squares."""
    import json, math
    src = SITE / "boating.json"
    if not src.exists():
        print("note: site/boating.json not built yet"); return
    d = json.loads(src.read_text()); tiles = {}
    for p in d["p"]:
        k = f"{math.floor(p[1] / TILE) * TILE}_{math.floor(p[2] / TILE) * TILE}"
        tiles.setdefault(k, []).append(p)
    out = OUT / "boating"; out.mkdir(parents=True, exist_ok=True)
    for k, v in tiles.items():
        (out / f"{k}.json").write_text(json.dumps(v, separators=(",", ":")))
    (out / "index.json").write_text(json.dumps({"built": d.get("built"), "types": d["types"], "size": TILE, "tiles": {k: len(v) for k, v in tiles.items()}}, separators=(",", ":")))
    print(f"places: {len(d['p'])} in {len(tiles)} squares")


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    page = (SITE / "index.html").read_text().replace("<title>Nockd</title>", "<title>Nockd: wind, water, racing and cruising</title>", 1)
    (OUT / "index.html").write_text(HEAD + page + "\n</body></html>\n")  # claude.ai adds this wrapper itself
    for f in FILES[2:]:
        if f in ("boating.json", "discharge.json", "nav.json", "marinezones.json") and not (SITE / f).exists():  # built weekly by scripts.boating_places; the page says when it is missing
            print("note: site/boating.json not built yet (run python -m scripts.boating_places)"); continue
        shutil.copy(SITE / f, OUT / f)
    (OUT / "config.js").write_text(CONFIG)
    (OUT / "_headers").write_text(HEADERS)
    shutil.copytree(SITE / "tiles", OUT / "tiles")
    for sub in ("iberia", "west", "lakes", "hawaii", "depth", "global", "world", "seabed"):   # other regions and the water-depth tiles
        if (SITE / sub).exists():
            shutil.copytree(SITE / sub, OUT / sub)
    split_places()
    shutil.copy(ROOT / "deploy" / "DEPLOY.md", OUT / "DEPLOY.md")
    zip_path = shutil.make_archive(str(OUT), "zip", OUT.parent, OUT.name)
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"wrote {OUT} ({size // 1024 // 1024} MB) and {zip_path}")


if __name__ == "__main__":
    main()
