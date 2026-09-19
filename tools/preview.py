#!/usr/bin/env python3
"""See the page locally as the next rebuild would publish it.

    python3 tools/preview.py [--port 8780]

Copies docs/ to a temporary folder, links the staff-made recordings the rebuild
would link (the same `merge_backfill`), and serves it. Run it from whichever
checkout you want to look at: this one, or a worktree holding unreleased work.
The grader configs come from the classroom50 checkout next to this one.
"""
import argparse
import functools
import http.server
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from builder import build  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--classroom50", default=str(ROOT.parent / "classroom50"))
    args = ap.parse_args()
    site = Path(tempfile.mkdtemp(prefix="board-preview-")) / "docs"
    shutil.copytree(ROOT / "docs", site)
    labs = build.load_labs(build.ConfigSource(None, build.DEFAULT_ORG, Path(args.classroom50)), build.DEFAULT_CLASSROOM)
    for lab in labs:
        path = site / "data" / f"{lab['board']}.json"
        if not path.is_file():
            continue
        doc = json.loads(path.read_text())
        build.merge_backfill(site / "data", lab, doc, doc["rows"] + doc.get("late", []), doc.get("reference"))
        path.write_text(json.dumps(doc))
        print(f"http://127.0.0.1:{args.port}/?lab={lab['board']}")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
    handler.log_message = lambda *a: None
    try:
        http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shutil.rmtree(site.parent, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
