"""Track images for the replay player, made from the grader's own ROS maps.

A map is cropped to its drivable area and only the walls are kept (opaque on
transparent, so the player tints them to the theme). `maps.json` carries what the
player needs to place a pose: metres per pixel and the world position of the
crop's bottom-left corner. The rebuild makes a missing track by itself the first
time a recording names it (`ensure`), so a new lab needs no manual step;
`tools/make_maps.py` does the same from a local folder. Needs Pillow and PyYAML.
"""
import io
import json
import re
from pathlib import Path

import yaml

MARGIN_M = 1.5
MAX_SOURCE_BYTES = 16_000_000


def valid_name(stem) -> bool:
    """A map stem as it appears in a recording: a plain file stem, never a path."""
    return isinstance(stem, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", stem) is not None


def folder(data_dir: Path) -> Path:
    return Path(data_dir).parent / "assets" / "maps"


def convert(yaml_text: str, image_bytes: bytes, stem: str):
    """(entry for maps.json, PNG bytes) from a ROS map's yaml and image."""
    from PIL import Image
    meta = yaml.safe_load(yaml_text)
    res, (ox, oy) = float(meta["resolution"]), meta["origin"][:2]
    grey = Image.open(io.BytesIO(image_bytes)).convert("L")
    left, top, right, bottom = grey.point(lambda p: 255 if p > 250 else 0).getbbox()
    pad = round(MARGIN_M / res)
    box = (max(0, left - pad), max(0, top - pad), min(grey.width, right + pad), min(grey.height, bottom + pad))
    walls = grey.crop(box).point(lambda p: 255 if p < 90 else 0)
    rgba = Image.new("RGBA", walls.size, (255, 255, 255, 0))
    rgba.putalpha(walls)
    out = io.BytesIO()
    rgba.save(out, "PNG", optimize=True)
    # image rows run top-down, the world's y runs up: the crop's bottom edge
    # is (image height - box bottom) pixels above the map origin
    entry = {"file": f"{stem}.png", "res": res, "w": walls.width, "h": walls.height,
             "x0": round(ox + box[0] * res, 4), "y0": round(oy + (grey.height - box[3]) * res, 4)}
    return entry, out.getvalue()


def add(data_dir: Path, stem: str, yaml_text: str, image_bytes: bytes) -> dict:
    entry, png = convert(yaml_text, image_bytes, stem)
    out = folder(data_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / entry["file"]).write_bytes(png)
    index_path = out / "maps.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    index[stem] = entry
    index_path.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n")
    return entry


def ensure(config, classroom: str, slug: str, stem: str, data_dir: Path, log) -> bool:
    """Make the track image for `stem` from `<classroom>/autograders/<slug>/maps/`
    when the page does not have it yet. True when the page can draw that map."""
    if not valid_name(stem):
        return False
    index_path = folder(data_dir) / "maps.json"
    if index_path.exists() and stem in json.loads(index_path.read_text()):
        return True
    base = f"{classroom}/autograders/{slug}/maps"
    try:
        yaml_text = config.text(f"{base}/{stem}.yaml")
        image = str((yaml.safe_load(yaml_text) or {}).get("image") or "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,80}", image) or ".." in image:
            return False
        raw = config.bytes(f"{base}/{image}")
        if len(raw) > MAX_SOURCE_BYTES:
            return False
        add(data_dir, stem, yaml_text, raw)
    except Exception as err:  # noqa: BLE001 - a track image never fails a build
        log(f"  track image for {stem}: not made ({type(err).__name__})")
        return False
    log(f"  track image for {stem}: made from the grader's map")
    return True
