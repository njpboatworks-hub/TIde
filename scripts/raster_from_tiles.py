"""Rebuild each region's ~600 m land/water grid from the detailed shoreline tiles in site/tiles (OpenStreetMap, with
lagoons added), so narrow inlets and river mouths stay open exactly as drawn. Cells with no tile keep their old value.
Usage: python -m scripts.raster_from_tiles  (then re-export the regions)
"""
import base64, json
import numpy as np
from PIL import Image, ImageDraw
from backend.config import DATA_DIR, ROOT

T = ROOT / "site" / "tiles"


def main():
    idx = json.loads((T / "index.json").read_text())
    for d in (DATA_DIR, DATA_DIR / "west", DATA_DIR / "lakes", DATA_DIR / "hawaii", DATA_DIR / "iberia"):
        f = d / "coast_raster.json"; g = json.loads(f.read_text())
        old = np.unpackbits(np.frombuffer(base64.b64decode(g["bits"]), dtype=np.uint8))[: g["nx"] * g["ny"]].reshape(g["ny"], g["nx"]).astype(bool)
        lon0, lat0, res, nx, ny = g["lon0"], g["lat0"], g["res"], g["nx"], g["ny"]
        img = Image.new("L", (nx, ny), 0); cover = Image.new("1", (nx, ny), 0)
        dr, dc = ImageDraw.Draw(img), ImageDraw.Draw(cover)
        px = lambda cs: [((x - lon0) / res, (y - lat0) / res) for x, y in cs]
        n = 0
        for k in idx["tiles"] + idx["land"]:
            la, lo = map(int, k.split("_"))
            if lo + 1 <= lon0 or lo >= lon0 + nx * res or la + 1 <= lat0 or la >= lat0 + ny * res:
                continue
            dc.rectangle([(lo - lon0) / res, (la - lat0) / res, (lo + 1 - lon0) / res - 1, (la + 1 - lat0) / res - 1], fill=1)
            if k in idx["land"]:
                dr.rectangle([(lo - lon0) / res, (la - lat0) / res, (lo + 1 - lon0) / res - 1, (la + 1 - lat0) / res - 1], fill=255); n += 1; continue
            gj = json.loads((T / f"{k}.json").read_text())
            for poly in gj["coordinates"]:
                dr.polygon(px(poly[0]), fill=255)
                for h in poly[1:]:
                    dr.polygon(px(h), fill=0)
            n += 1
        land = np.array(img) >= 128; cov = np.array(cover, dtype=bool)
        from scipy.ndimage import binary_dilation
        near = binary_dilation(~old, iterations=5)  # only within ~3 km of water the old grid already had
        new = np.where(cov & near, land, old)
        g["bits"] = base64.b64encode(np.packbits(new.ravel()).tobytes()).decode()
        f.write_text(json.dumps(g, separators=(",", ":")))
        print(f"{d.name}: {n} tiles, {int((old != new).sum()):,} cells changed ({(old & ~new).sum():,} land->water)")


if __name__ == "__main__":
    main()
