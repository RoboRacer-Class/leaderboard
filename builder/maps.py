"""Track images for the replay player, made from the grader's own ROS maps.

A map is cropped to its drivable area and only the walls are kept (opaque on
transparent, so the player tints them to the theme). `maps.json` carries what the
player needs to place a pose: metres per pixel and the world position of the
crop's bottom-left corner, and, when the grader laps the map on a centerline,
its start/finish `line`, where the player lines the cars up. The rebuild makes a
missing track by itself the first time a recording names it (`ensure`), so a new
lab needs no manual step; `tools/make_maps.py` does the same from a local folder.
Needs Pillow and PyYAML.
"""
import io
import json
import math
import posixpath
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


def line_of(csv_text: str):
    """The start/finish line of a gym centerline CSV (`x_m, y_m, ...` rows, first point = the
    finish line: the sim's lap counter and the referee's `timing: datum` both count laps
    there): that point and the track's direction through it, a unit vector (the mean of the
    ways in and out); the line runs across the track. None without two distinct points."""
    pts = []
    for row in csv_text.splitlines():
        row = row.strip()
        if row and not row.startswith("#"):
            pts.append(tuple(float(v) for v in row.split(",")[:2]))
    pts = [p for p in pts if all(map(math.isfinite, p))]
    if len(pts) < 2:
        return None
    (x, y), (xo, yo), (xi, yi) = pts[0], pts[1], pts[-1]

    def unit(dx, dy):
        n = math.hypot(dx, dy)
        return (dx / n, dy / n) if n > 1e-9 else (0.0, 0.0)
    out, back = unit(xo - x, yo - y), unit(x - xi, y - yi)
    dx, dy = unit(out[0] + back[0], out[1] + back[1])
    if dx == dy == 0.0:
        return None
    return {"x": round(x, 4), "y": round(y, 4), "dx": round(dx, 5), "dy": round(dy, 5)}


def _scenarios(node):
    """Every block of a grader config that runs a map on a centerline."""
    if isinstance(node, dict):
        if node.get("map") and node.get("centerline"):
            yield node
        for value in node.values():
            yield from _scenarios(value)
    elif isinstance(node, list):
        for value in node:
            yield from _scenarios(value)


def line_for(read, stem: str, yaml_text: str, maps_dir: str = "maps"):
    """The start/finish line of `stem` as its grader laps it, or None. `read(path)` returns a
    file of the grader's folder. The centerline is the one a scenario of its config.yaml
    names for the map (the grader stages it next to the map as the gym's
    `<map>_centerline.csv`), else the map yaml's own `centerline:`, else that file name."""
    wanted = []
    try:
        config = yaml.safe_load(read("config.yaml"))
        wanted += [b["centerline"] for b in _scenarios(config) if Path(str(b["map"])).stem == stem]
    except Exception:  # noqa: BLE001 - no grader config: the map folder's own conventions
        pass
    try:
        key = (yaml.safe_load(yaml_text) or {}).get("centerline")
    except Exception:  # noqa: BLE001
        key = None
    if isinstance(key, str) and key:
        wanted.append(f"{maps_dir}/{key}")
    wanted.append(f"{maps_dir}/{stem}_centerline.csv")
    for path in wanted:
        path = posixpath.normpath(str(path))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. /-]{0,160}", path) or ".." in path:
            continue
        try:
            line = line_of(read(path))
        except Exception:  # noqa: BLE001 - a line never fails a track image
            continue
        if line:
            return line
    return None


def add(data_dir: Path, stem: str, yaml_text: str, image_bytes: bytes, line: dict | None = None) -> dict:
    entry, png = convert(yaml_text, image_bytes, stem)
    if line:
        entry["line"] = line
    out = folder(data_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / entry["file"]).write_bytes(png)
    index_path = out / "maps.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    index[stem] = entry
    index_path.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n")
    return entry


def set_line(data_dir: Path, stem: str, line: dict) -> bool:
    """Give a track the page already has its start line, image untouched."""
    index_path = folder(data_dir) / "maps.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    if stem not in index or not line:
        return False
    index[stem]["line"] = line
    index_path.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n")
    return True


def ensure(config, classroom: str, slug: str, stem: str, data_dir: Path, log) -> bool:
    """Make the track image for `stem` from `<classroom>/autograders/<slug>/maps/`
    when the page does not have it yet. True when the page can draw that map."""
    if not valid_name(stem):
        return False
    index_path = folder(data_dir) / "maps.json"
    if index_path.exists() and stem in json.loads(index_path.read_text()):
        return True
    grader = f"{classroom}/autograders/{slug}"
    base = f"{grader}/maps"
    try:
        yaml_text = config.text(f"{base}/{stem}.yaml")
        image = str((yaml.safe_load(yaml_text) or {}).get("image") or "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,80}", image) or ".." in image:
            return False
        raw = config.bytes(f"{base}/{image}")
        if len(raw) > MAX_SOURCE_BYTES:
            return False
        add(data_dir, stem, yaml_text, raw, line_for(lambda p: config.text(f"{grader}/{p}"), stem, yaml_text))
    except Exception as err:  # noqa: BLE001 - a track image never fails a build
        log(f"  track image for {stem}: not made ({type(err).__name__})")
        return False
    log(f"  track image for {stem}: made from the grader's map")
    return True
