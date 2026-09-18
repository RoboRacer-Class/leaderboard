"""Recorded runs for the page's replay player.

The grader attaches a recording of the run to the submission's release (grader
bundle replay_recorder.py, format v1). That release lives in a repository its
student can write to, so the file is untrusted: `clean` republishes only
whitelisted integers within sane bounds, or nothing. A refused recording
costs that row its eye button and nothing else.

One file per board entry, named after the public board key and overwritten
when a better run arrives, so no attempt id or commit hash is ever published.
"""
import hashlib
import json
import re
from pathlib import Path

FORMAT = 1
MAX_BYTES = 400_000
MAX_SECONDS = 600
MAX_LAPS = 50
ENDS = ("finished", "collision", "timeout", "stalled")
COLUMNS = {
    # column: (largest first value, largest step between samples at 20 Hz)
    "x": (20_000, 250),        # cm: within 200 m of the origin, under 50 m/s
    "y": (20_000, 250),
    "yaw": (10**7, 6_000),     # centidegrees, unwrapped: under 1200 deg/s (full lock
                               # at 7 m/s is ~540 deg/s, so a real spin must still pass)
    "s": (5_000, 2_500),       # cm/s: a wall stops the car within one sample
}
LAP_SLACK_S = 0.06             # the board's time is rounded to 0.01 s


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def clean(raw: bytes, maps: set, lap_seconds: float | None = None) -> dict | None:
    """The publishable copy of a recording, or None when it is not one.

    `lap_seconds` is the time the board ranks this entry by: when the
    recording names its ranked lap, the two must agree."""
    if len(raw) > MAX_BYTES:
        return None
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(doc, dict) or doc.get("v") != FORMAT:
        return None
    hz, track = doc.get("hz"), doc.get("map")
    if not _int(hz) or not 5 <= hz <= 50 or not isinstance(track, str) or track not in maps:
        return None
    n = len(doc["x"]) if isinstance(doc.get("x"), list) else 0
    if not 2 <= n <= MAX_SECONDS * hz:
        return None
    out = {"v": FORMAT, "map": track, "hz": hz, "n": n}
    for name, (first_max, step_max) in COLUMNS.items():
        column = doc.get(name)
        if not isinstance(column, list) or len(column) != n or not all(_int(v) for v in column):
            return None
        step_max = step_max * 20 // hz
        if abs(column[0]) > first_max or any(abs(v) > step_max for v in column[1:]):
            return None
        out[name] = list(column)

    laps = doc.get("laps")
    laps = laps if isinstance(laps, list) and len(laps) <= MAX_LAPS else []
    ok = all(isinstance(lap, list) and len(lap) == 2 and all(_int(i) for i in lap)
             and 0 <= lap[0] < lap[1] <= n - 1 for lap in laps)
    out["laps"] = [list(lap) for lap in laps] if ok else []
    lap_ms = doc.get("lap_ms")
    ok = (isinstance(lap_ms, list) and len(lap_ms) == len(out["laps"])
          and all(_int(ms) and 0 < ms < MAX_SECONDS * 1000 for ms in lap_ms))
    out["lap_ms"] = list(lap_ms) if ok else []
    best = doc.get("best")
    if _int(best) and 0 <= best < len(out["laps"]):
        out["best"] = best
        if lap_seconds is not None and out["lap_ms"] \
                and abs(out["lap_ms"][best] / 1000 - lap_seconds) > LAP_SLACK_S:
            return None
    if doc.get("end") in ENDS:
        out["end"] = doc["end"]
    return out


def file_name(key: str) -> str:
    """A file stem from the public board key: an alias, `Team 7`, `reference`."""
    return re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-") or "entry"


def save(data_dir: Path, board: str, key: str, doc: dict) -> str:
    """Write the entry's recording; returns the row's `replay` reference,
    relative to the data folder. The version changes with the content, so a
    better run is never hidden behind a cached copy of the old one."""
    text = json.dumps(doc, separators=(",", ":"))
    rel = f"replays/{file_name(board)}/{file_name(key)}.json"
    path = Path(data_dir) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return f"{rel}?v={hashlib.sha1(text.encode()).hexdigest()[:8]}"


def known_maps(data_dir: Path) -> set:
    """Track names the page can draw (docs/assets/maps/maps.json, next to docs/data)."""
    try:
        return set(json.loads((Path(data_dir).parent / "assets/maps/maps.json").read_text()))
    except (OSError, ValueError):
        return set()


def load_backfill(data_dir: Path, board: str) -> dict:
    """Recordings staff made after the fact by re-running an entry's best attempt
    locally: {board key: {"attempt": attempt id, "replay": reference}}, written
    next to the recordings by the staff backfill tool. Anything else is ignored."""
    folder = f"replays/{file_name(board)}"
    try:
        raw = json.loads((Path(data_dir) / folder / "backfill.json").read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    ref = re.compile(re.escape(folder) + r"/[a-z0-9-]+\.json\?v=[0-9a-f]{8}")
    return {key: entry for key, entry in raw.items()
            if isinstance(entry, dict) and isinstance(entry.get("attempt"), str)
            and isinstance(entry.get("replay"), str) and ref.fullmatch(entry["replay"])}
