#!/usr/bin/env python3
"""Web-sized track images for the replay player.

    python3 tools/make_maps.py [--line-only] <dir with map .yaml/.png> [more dirs...]

Crops each ROS map to its drivable area, keeps only the walls (opaque on
transparent, so the player tints them to the theme) and writes
docs/assets/maps/<map>.png plus maps.json with the geometry the player needs
to place a pose, and the map's start/finish line when the grader laps it on a
centerline (a grader's `maps/` folder: its config.yaml sits one level up). The
rebuild makes a missing track by itself (builder/maps.py, the same code); run
this by hand only to refresh a map that changed. --line-only gives the tracks
maps.json already has their start line and leaves every image as it is.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from builder import maps  # noqa: E402

DATA = pathlib.Path(__file__).resolve().parents[1] / "docs/data"

if __name__ == "__main__":
    import yaml
    args = sys.argv[1:]
    line_only = "--line-only" in args
    for folder in (pathlib.Path(a) for a in args if a != "--line-only"):
        for y in sorted(folder.glob("*.yaml")):
            line = maps.line_for(lambda p: (folder.parent / p).read_text(), y.stem, y.read_text(), folder.name)
            if line_only:
                print(y.stem, line if maps.set_line(DATA, y.stem, line) else "unchanged (no such track or no line)")
                continue
            image = folder / yaml.safe_load(y.read_text())["image"]
            print(y.stem, maps.add(DATA, y.stem, y.read_text(), image.read_bytes(), line))
