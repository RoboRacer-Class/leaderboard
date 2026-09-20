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
import math
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
RETIME_MAX = 1.25              # a re-run a quarter slower or faster than the graded run is another run


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
    if lap_seconds is not None:
        # a ranked run's recording must name the lap the board ranks, timed to match: the
        # player races on that lap, so anything else finishes out of the board's order
        seconds = ranked_seconds(out)
        if seconds is None or abs(seconds - lap_seconds) > LAP_SLACK_S:
            return None
    if doc.get("end") in ENDS:
        out["end"] = doc["end"]
    return out


def ranked_seconds(doc: dict):
    """The time of the lap a recording names as ranked, or None when it names none."""
    laps, lap_ms, best = doc.get("laps"), doc.get("lap_ms"), doc.get("best")
    if not (isinstance(laps, list) and isinstance(lap_ms, list) and _int(best)
            and 0 <= best < len(laps) and len(lap_ms) == len(laps)
            and _int(lap_ms[best]) and lap_ms[best] > 0):
        return None
    return lap_ms[best] / 1000


def _totals(deltas: list) -> list:
    out, total = [], 0
    for d in deltas:
        total += d
        out.append(total)
    return out


def _deltas(values: list) -> list:
    return [values[0]] + [b - a for a, b in zip(values, values[1:])] if values else []


def retime(doc: dict, lap_seconds: float) -> dict | None:
    """The same recording on a clock scaled so that its ranked lap lasts `lap_seconds`.

    A run staff re-ran locally (the backfill) is not the graded run: its lap
    differs by a few tenths, and a player racing it on its own clock finishes
    the car out of the board's order and shows a time the board does not have.
    Scaling the clock uniformly keeps the drive as it was and puts its finish
    on the board's time; speeds scale with it. `doc` is a cleaned recording.
    None when it names no ranked lap, or the scale is beyond RETIME_MAX either
    way: that re-run is not this run."""
    seconds = ranked_seconds(doc)
    if seconds is None:
        return None
    k = lap_seconds / seconds
    if not 1 / RETIME_MAX <= k <= RETIME_MAX:
        return None
    hz, n = doc["hz"], doc["n"]
    m = int(math.floor((n - 1) * k + 1e-9)) + 1          # samples on the new clock
    out = {"v": FORMAT, "map": doc["map"], "hz": hz, "n": m}
    for name in COLUMNS:
        values, scale = _totals(doc[name]), 1 / k if name == "s" else 1.0
        column = []
        for j in range(m):
            tau = j / k                                     # this sample's position on the old clock
            i = min(int(math.floor(tau)), n - 2)
            column.append(round((values[i] + (tau - i) * (values[i + 1] - values[i])) * scale))
        out[name] = _deltas(column)
    out["laps"] = [[min(m - 1, round(a * k)), min(m - 1, round(b * k))] for a, b in doc["laps"]]
    out["lap_ms"] = [round(ms * k) for ms in doc["lap_ms"]]
    out["lap_ms"][doc["best"]] = round(lap_seconds * 1000)
    out["best"] = doc["best"]
    if doc.get("end") in ENDS:
        out["end"] = doc["end"]
    return out


def map_of(raw: bytes):
    """The map stem a recording names, read without trusting anything else in it."""
    if len(raw) > MAX_BYTES:
        return None
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    return doc.get("map") if isinstance(doc, dict) and isinstance(doc.get("map"), str) else None


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
