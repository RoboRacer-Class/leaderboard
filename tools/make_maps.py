#!/usr/bin/env python3
"""Web-sized track images for the replay player.

    python3 tools/make_maps.py <dir with map .yaml/.png> [more dirs...]

Crops each ROS map to its drivable area, keeps only the walls (opaque on
transparent, so the player tints them to the theme) and writes
docs/assets/maps/<map>.png plus maps.json with the geometry the player needs
to place a pose: metres per pixel and the world position of the crop's
bottom-left corner. Needs Pillow and PyYAML; run by hand when a map changes.
"""
import json
import pathlib
import sys

import yaml
from PIL import Image

OUT = pathlib.Path(__file__).resolve().parents[1] / "docs/assets/maps"
MARGIN_M = 1.5


def convert(yaml_path: pathlib.Path) -> dict:
    meta = yaml.safe_load(yaml_path.read_text())
    res, (ox, oy) = float(meta["resolution"]), meta["origin"][:2]
    grey = Image.open(yaml_path.parent / meta["image"]).convert("L")
    free = grey.point(lambda p: 255 if p > 250 else 0)
    left, top, right, bottom = free.getbbox()
    pad = round(MARGIN_M / res)
    box = (max(0, left - pad), max(0, top - pad),
           min(grey.width, right + pad), min(grey.height, bottom + pad))
    walls = grey.crop(box).point(lambda p: 255 if p < 90 else 0)
    rgba = Image.new("RGBA", walls.size, (255, 255, 255, 0))
    rgba.putalpha(walls)
    OUT.mkdir(parents=True, exist_ok=True)
    rgba.save(OUT / f"{yaml_path.stem}.png", optimize=True)
    # image rows run top-down, the world's y runs up: the crop's bottom edge
    # is (image height - box bottom) pixels above the map origin
    return {"file": f"{yaml_path.stem}.png", "res": res, "w": walls.width, "h": walls.height,
            "x0": round(ox + box[0] * res, 4), "y0": round(oy + (grey.height - box[3]) * res, 4)}


if __name__ == "__main__":
    index_path = OUT / "maps.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    for folder in sys.argv[1:]:
        for y in sorted(pathlib.Path(folder).glob("*.yaml")):
            index[y.stem] = convert(y)
            print(y.stem, index[y.stem], (OUT / f"{y.stem}.png").stat().st_size, "bytes")
    index_path.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n")
