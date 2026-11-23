"""Bottom type (what the seabed is made of) from the NOAA Electronic Navigational Charts, for picking an anchorage:
sand, mud and clay hold an anchor well; rock and coral do not. Charted seabed samples (S-57 SBDARE, attribute NATSUR)
from the harbour, approach and coastal chart scales.

Output, one file per 1 x 1 degree square so the page only loads the area on screen:
  site/seabed/index.json             list of square keys "lat_lon"
  site/seabed/{lat}_{lon}.json       [[lat, lon, materials], ...]   materials: bit mask of MAT below
Usage: python -m scripts.seabed
"""
from __future__ import annotations

import json
import math
import time

from backend.config import ROOT
from scripts.boating_places import enc_features, enc_layers

MAT = ["mud", "sand", "clay", "silt", "shells", "gravel", "pebbles", "cobbles", "stones", "rock", "boulder", "coral",
       "lava", "weed", "ooze"]
OUT = ROOT / "site" / "seabed"


def code(natsur):
    m = 0
    for w in str(natsur or "").lower().replace(";", ",").split(","):
        w = w.strip().rstrip("s") if w.strip() not in ("shells", "pebbles", "cobbles", "stones") else w.strip()
        w = {"boulders": "boulder", "boulder": "boulder", "shell": "shells", "pebble": "pebbles", "cobble": "cobbles",
             "stone": "stones"}.get(w, w)
        if w in MAT:
            m |= 1 << MAT.index(w)
    return m


def main():
    t0, seen, tiles = time.time(), set(), {}
    for scale in ("enc_harbour", "enc_approach", "enc_coastal"):
        L = enc_layers(scale)
        lid = L.get("Seabed_Area_point")
        if lid is None:
            continue
        fs = enc_features(scale, lid, "NATSUR")
        n = 0
        for f in fs:
            g = f.get("geometry") or {}
            if "x" not in g:
                continue
            c = code(f.get("attributes", {}).get("NATSUR"))
            if not c:
                continue
            lat, lon = round(g["y"], 5), round(g["x"], 5)
            k = (round(lat * 1000), round(lon * 1000))  # one sample per ~100 m (the same sample repeats across scales)
            if k in seen:
                continue
            seen.add(k)
            tiles.setdefault(f"{math.floor(lat)}_{math.floor(lon)}", []).append([lat, lon, c])
            n += 1
        print(f"  {scale}: {len(fs)} samples, {n} kept", flush=True)
    if sum(len(v) for v in tiles.values()) < 10000:
        raise SystemExit("too few seabed samples downloaded; keeping the previous files")
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*.json"):
        f.unlink()
    for k, v in tiles.items():
        (OUT / f"{k}.json").write_text(json.dumps(v, separators=(",", ":")))
    (OUT / "index.json").write_text(json.dumps({"mat": MAT, "tiles": sorted(tiles)}))
    size = sum(f.stat().st_size for f in OUT.glob("*.json")) // 1024
    print(f"wrote {len(tiles)} squares, {sum(len(v) for v in tiles.values())} samples, {size} KB ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
