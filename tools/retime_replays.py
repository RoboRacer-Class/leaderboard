#!/usr/bin/env python3
"""Put every recording a board links on the board's own clock.

    python3 tools/retime_replays.py lab-3-wall-following [--dry-run]

A recording staff made by re-running an entry (the backfill) is not the graded
run: its lap differs by a few tenths, and the player, which races every car on
its recording's ranked lap, finished it out of the board's order and showed the
re-run's time over the board's (2026-09-19: P3 of lab 3 came fifth, at 10.83 s
against the 10.65 s it is ranked by). This scales each linked recording whose
ranked lap disagrees with its row so that the lap lasts exactly the row's time
(`replays.retime`), saves it under a new version, points `backfill.json` (or
the attempt, for a run the rebuild captured) at it, and rewrites the board
through the builder's own ranking. A recording already on the board's clock is
left alone. The backfill tool retimes as it records, so this is for files made
before it did, and for any file `backfill_fits` refuses to link.

Pull first, commit `docs/data` and push right after: the rebuild commits to the
same files every few minutes.
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from builder import build, replays, rules  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    ap.add_argument("--classroom50", default=str(ROOT.parent / "classroom50"))
    args = ap.parse_args()

    data_dir = ROOT / "docs/data"
    labs = build.load_labs(build.ConfigSource(None, build.DEFAULT_ORG, Path(args.classroom50)), build.DEFAULT_CLASSROOM)
    lab = next((l for l in labs if l["board"] == args.board), None)
    if lab is None:
        return print(f"{args.board}: no such board") or 1
    metric = lab["metric"]
    if metric.unit != "s":
        return print(f"{args.board}: not ranked by a time; nothing to put on a clock") or 0
    path = data_dir / f"{lab['board']}.json"
    state = build.load_state(path)
    cap = lab["cap"] or 10**9
    due = rules.parse_time(lab["due"]) if lab.get("due") else None
    rows, unranked = rules.rank_players(state["players"], metric, cap, due)
    late, unranked = rules.late_rows(state["players"], metric, cap, due, unranked)
    reference = rules.best_reference(state["reference_submissions"], metric)
    index = replays.load_backfill(data_dir, lab["board"])
    sidecar = data_dir / "replays" / replays.file_name(lab["board"]) / "backfill.json"
    maps = replays.known_maps(data_dir)

    # every entry with its ranked time, the attempt the board shows and the staff-made recording, if any
    entries = []
    for row in rows + late:
        sub = rules.counted_submissions(state["players"][row["alias"]])[row["attempt"] - 1]
        entries.append((f"P{row['rank']}" if row.get("rank") else "late", row["alias"], row["metric"], sub, index.get(row["alias"])))
    if reference is not None:
        best = build.best_attempt(lab, "reference", state["reference_submissions"])
        entries.append(("REF", "reference", reference["metric"], best, index.get("reference")))

    retimed, sidecar_changed = 0, False
    for pos, key, value, sub, made in entries:
        if sub is None:
            continue
        if sub.get("replay"):                                # captured by the rebuild from the release
            holder, ref = sub, sub["replay"]
        elif made is not None and made["attempt"] == sub["id"]:
            holder, ref = made, made["replay"]
        else:
            continue
        try:
            doc = json.loads((data_dir / ref.partition("?v=")[0]).read_text())
        except (OSError, ValueError):
            print(f"  {pos} {key}: recording unreadable; stays unlinked")
            continue
        seconds = replays.ranked_seconds(doc)
        if seconds is not None and round(seconds * 1000) == round(value * 1000):
            continue                                         # on the board's clock already
        timed = replays.retime(doc, value) if seconds is not None else None
        checked = replays.clean(json.dumps(timed).encode(), maps, lap_seconds=value) if timed else None
        was = "no ranked lap" if seconds is None else f"{seconds:.2f} s"
        if checked is None:
            print(f"  {pos} {key}: {was} against {value} s on the board; cannot be put on the board's clock, stays unlinked")
            continue
        if doc.get("rerun") == 1:
            checked["rerun"] = 1
        print(f"  {pos} {key}: {was} -> {value} s (x{value / seconds:.3f})")
        retimed += 1
        if args.dry_run:
            continue
        holder["replay"] = replays.save(data_dir, lab["board"], key, checked)
        sidecar_changed = sidecar_changed or holder is made

    if args.dry_run or not retimed:
        print(f"{lab['board']}: {retimed} recording(s) {'to retime' if args.dry_run else 'retimed'}")
        return 0
    if sidecar_changed:
        sidecar.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n")
    rows, unranked = rules.rank_players(state["players"], metric, cap, due)
    late, unranked = rules.late_rows(state["players"], metric, cap, due, unranked)
    reference = rules.best_reference(state["reference_submissions"], metric)
    build.merge_backfill(data_dir, lab, state, rows + late, reference)
    now = rules.iso(dt.datetime.now(dt.timezone.utc))
    build.write_json(path, build.lab_document(lab, state, rows, unranked, reference, now, late))
    print(f"{lab['board']}: {retimed} recording(s) retimed; board rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
