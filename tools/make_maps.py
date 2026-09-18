#!/usr/bin/env python3
"""Web-sized track images for the replay player.

    python3 tools/make_maps.py <dir with map .yaml/.png> [more dirs...]

Crops each ROS map to its drivable area, keeps only the walls (opaque on
transparent, so the player tints them to the theme) and writes
docs/assets/maps/<map>.png plus maps.json with the geometry the player needs
to place a pose. The rebuild makes a missing track by itself (builder/maps.py,
the same code); run this by hand only to refresh a map that changed.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from builder import maps  # noqa: E402

DATA = pathlib.Path(__file__).resolve().parents[1] / "docs/data"

if __name__ == "__main__":
    for folder in sys.argv[1:]:
        for y in sorted(pathlib.Path(folder).glob("*.yaml")):
            import yaml
            image = pathlib.Path(folder) / yaml.safe_load(y.read_text())["image"]
            print(y.stem, maps.add(DATA, y.stem, y.read_text(), image.read_bytes()))
