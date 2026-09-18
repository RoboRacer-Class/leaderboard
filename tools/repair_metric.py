#!/usr/bin/env python3
"""Correct one attempt's ranked metric by hand, and say why.

    python3 tools/repair_metric.py lab-3-wall-following "Polar Meteor 19" 15.02 \\
        --why "lap time recovered from this run's own recording; the grader reported 0.0" \\
        [--lap 0.378 15.401]

For the rare case where the grader reported a wrong number for a run that was
otherwise graded correctly (2026-09-18: lab 3's referee closed a run between the
sim's lap_count and lap_time messages and reported a 0.00 s lap). The attempt
the board shows for that entry gets the new value and a `repaired` note, the
rows are re-ranked by the builder's own rules, and nothing else is touched. The
rebuild keeps a repaired attempt as it is: a release is read once.

`--lap START END` (seconds into the entry's recording) also marks the timed lap
in that recording, which a run graded with a zero lap time lacks, so the player
lines the car up on its start line like everyone else's.

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
    ap.add_argument("entry", help="the alias or team as the board shows it")
    ap.add_argument("value", type=float)
    ap.add_argument("--why", required=True)
    ap.add_argument("--lap", nargs=2, type=float, metavar=("START", "END"))
    ap.add_argument("--classroom50", default=str(ROOT.parent / "classroom50"))
    args = ap.parse_args()

    data_dir = ROOT / "docs/data"
    labs = build.load_labs(build.ConfigSource(None, build.DEFAULT_ORG, Path(args.classroom50)), build.DEFAULT_CLASSROOM)
    lab = next((l for l in labs if l["board"] == args.board), None)
    if lab is None:
        return print(f"{args.board}: no such board") or 1
    path = data_dir / f"{lab['board']}.json"
    state, metric = build.load_state(path), lab["metric"]
    player = state["players"].get(args.entry)
    if player is None:
        return print(f"{args.entry}: not on this board") or 1
    subs = [s for s in rules.counted_submissions(player) if s.get("graded") and s.get("full") and s.get("metrics")]
    # the attempt to correct: the glitched one if there is one, else the one the board ranks
    sub = next((s for s in subs if rules.glitched(s, metric)), None)
    if sub is None:
        due = rules.parse_time(lab["due"]) if lab.get("due") else None
        best = rules.best_of(rules.counted_submissions(player)[:lab["cap"] or 10**9], metric, due)
        sub = best[1] if best else None
    if sub is None:
        return print(f"{args.entry}: no graded full-score attempt to correct") or 1
    was = sub["metrics"][metric.key]
    sub["metrics"][metric.key] = args.value
    sub["repaired"] = f"{metric.key} was {was}: {args.why}"

    if args.lap and sub.get("replay"):
        rec_path = data_dir / sub["replay"].partition("?v=")[0]
        doc = json.loads(rec_path.read_text())
        first, last = round(args.lap[0] * doc["hz"]), min(round(args.lap[1] * doc["hz"]), doc["n"] - 1)
        doc.update(laps=[[first, last]], lap_ms=[round(args.value * 1000)], best=0)
        checked = replays.clean(json.dumps(doc).encode(), replays.known_maps(data_dir), lap_seconds=args.value)
        if checked is None:
            return print("the recording with that lap marked does not validate; nothing written") or 1
        sub["replay"] = replays.save(data_dir, lab["board"], args.entry, checked)

    due = rules.parse_time(lab["due"]) if lab.get("due") else None
    rows, unranked = rules.rank_players(state["players"], metric, lab["cap"] or 10**9, due)
    reference = rules.best_reference(state["reference_submissions"], metric)
    build.merge_backfill(data_dir, lab, state, rows, reference)
    now = rules.iso(dt.datetime.now(dt.timezone.utc))
    build.write_json(path, build.lab_document(lab, state, rows, unranked, reference, now))
    row = next(r for r in rows if r["alias"] == args.entry)
    print(f"{args.entry}: {metric.key} {was} -> {args.value}, now P{row['rank']} of {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
