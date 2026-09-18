"""Nothing about a term is written into the page or the builder: labels, track images
and the hand-over from one term to the next all come from the course config."""
import io
import json

import pytest

from builder import build, maps
from test_build import CLASSROOM, CONFIG_YAML, ORG, SLUG, FakeApi, add_student, lap, run_build
from test_build_replays import attach, recording

CONFIG_KEY = (f"{ORG}/classroom50", f"{CLASSROOM}/autograders/{SLUG}/config.yaml")


def log():
    return build.Log(io.StringIO())


def test_the_tab_label_comes_from_the_grader_config(tmp_path):
    api = FakeApi()
    api.files[CONFIG_KEY] = CONFIG_YAML.replace("  podium: 5\n", "  podium: 5\n  tab: 'Lab 3: Wall Follow'\n")
    add_student(api, "cedrichld", [lap(13.47)], ["2026-09-07T03:46:01Z"])
    assert run_build(api, tmp_path, log()) == 0
    entry = json.loads((tmp_path / "index.json").read_text())["labs"][0]
    assert entry["tab_title"] == "Lab 3: Wall Follow"
    api.files[CONFIG_KEY] = CONFIG_YAML                                   # no label: the page falls back to the lab's name
    assert run_build(api, tmp_path, log()) == 0
    assert json.loads((tmp_path / "index.json").read_text())["labs"][0]["tab_title"] == ""


def track_png():
    from PIL import Image
    im = Image.new("L", (80, 60), 205)                                     # unknown space
    for x in range(10, 70):
        for y in range(10, 50):
            im.putpixel((x, y), 254)                                       # a free room...
    for x in range(10, 70):
        im.putpixel((x, 10), 0)                                            # ...with a wall
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def test_a_new_track_gets_its_image_without_anyone_running_a_tool(tmp_path):
    pytest.importorskip("PIL")
    api = FakeApi()
    api.files[CONFIG_KEY] = CONFIG_YAML.replace("  podium: 5\n", "  podium: 5\n  replay: replay.json\n")
    base = f"{CLASSROOM}/autograders/{SLUG}/maps"
    api.files[(f"{ORG}/classroom50", f"{base}/monza.yaml")] = "image: monza.png\nresolution: 0.05\norigin: [-2.0, -1.5, 0.0]\n"
    api.blobs = {(f"{ORG}/classroom50", f"{base}/monza.png"): track_png()}
    api.file_bytes = lambda repo, path, ref="main": api.blobs[(repo, path)]
    data_dir = tmp_path / "docs" / "data"
    data_dir.mkdir(parents=True)
    add_student(api, "cedrichld", [lap(13.47)], ["2026-09-07T03:46:01Z"])
    alice = add_student(api, "alice", [lap(12.0)], ["2026-09-10T10:00:00Z"])
    attach(api, alice, 0, recording(12.0, map="monza"))
    bob = add_student(api, "bob", [lap(12.5)], ["2026-09-10T11:00:00Z"])
    attach(api, bob, 0, recording(12.5, map="../../secrets"))              # a name that is not a name
    carol = add_student(api, "carol", [lap(12.7)], ["2026-09-10T12:00:00Z"])
    attach(api, carol, 0, recording(12.7, map="nowhere"))                  # no such map in the grader
    assert run_build(api, data_dir, log()) == 0
    rows = {r["metric"]: r for r in json.loads((data_dir / f"{SLUG}.json").read_text())["rows"]}
    assert rows[12.0].get("replay") and "replay" not in rows[12.5] and "replay" not in rows[12.7]
    index = json.loads((tmp_path / "docs/assets/maps/maps.json").read_text())
    assert set(index) == {"monza"} and index["monza"]["res"] == 0.05
    assert (tmp_path / "docs/assets/maps/monza.png").read_bytes()[:4] == b"\x89PNG"
    assert maps.valid_name("Spielberg") and not maps.valid_name("../x") and not maps.valid_name("a/b") and not maps.valid_name("")


def test_a_new_classroom_archives_the_old_term_by_itself(tmp_path):
    api = FakeApi()
    add_student(api, "cedrichld", [lap(13.47)], ["2026-09-07T03:46:01Z"])
    add_student(api, "alice", [lap(14.2)], ["2026-09-10T10:00:00Z"])
    assert run_build(api, tmp_path, log()) == 0
    (tmp_path / "replays" / SLUG).mkdir(parents=True)
    (tmp_path / "replays" / SLUG / "someone.json").write_text("{}")

    # next term: a new classroom, the same lab slug, new students
    term = "ese-6150-s27"
    nxt = FakeApi()
    for (repo, path), text in list(nxt.files.items()):
        nxt.files[(repo, path.replace(CLASSROOM, term, 1))] = text
    nxt.teams = {k.replace(CLASSROOM, term): v for k, v in nxt.teams.items()}
    name = f"{term}-{SLUG}-zoe"
    nxt.repos.append(name)
    assert build.build(nxt, ORG, term, "salt", "tok", tmp_path, "https://x/leaderboard", None, False, log()) == 0
    now = json.loads((tmp_path / f"{SLUG}.json").read_text())
    # last term's drivers are not on this term's board; zoe accepted the lab and has not submitted yet
    assert now["rows"] == [] and len(now["players"]) == 1 and len(now["unranked"]) == 1
    assert all(not p["submissions"] for p in now["players"].values())
    old = tmp_path / "archive" / CLASSROOM
    assert [r["metric"] for r in json.loads((old / f"{SLUG}.json").read_text())["rows"]] == [14.2]
    assert (old / "index.json").is_file() and (old / "replays" / SLUG / "someone.json").is_file()
    terms = json.loads((tmp_path / "archive" / "index.json").read_text())["terms"]
    assert [t["label"] for t in terms] == [CLASSROOM]
    assert json.loads((tmp_path / "index.json").read_text())["classroom"] == term


def test_new_term_command_archives_under_a_label_and_is_safe_to_repeat(tmp_path):
    api = FakeApi()
    add_student(api, "cedrichld", [lap(13.47)], ["2026-09-07T03:46:01Z"])
    assert run_build(api, tmp_path, log()) == 0
    assert build.archive_term(tmp_path, "Fall 2026", log()) == "fall-2026"
    assert (tmp_path / "archive/fall-2026" / f"{SLUG}.json").is_file() and not (tmp_path / f"{SLUG}.json").exists()
    assert build.archive_term(tmp_path, "Fall 2026", log()) is None        # nothing left to archive
    assert run_build(api, tmp_path, log()) == 0                             # the same classroom again: starts clean...
    assert build.archive_term(tmp_path, "Fall 2026", log()) == "fall-2026-2"   # ...and a label is never overwritten
    labels = [t["label"] for t in json.loads((tmp_path / "archive/index.json").read_text())["terms"]]
    assert labels == ["fall-2026", "fall-2026-2"]


def add_later_student(api, username, year, seconds):
    """Like add_student, in another year (tag names carry their time)."""
    name = f"{CLASSROOM}-{SLUG}-{username}"
    api.repos.append(name)
    repo, sha = f"{ORG}/{name}", "abc1234"
    tag = f"submit/{year}-09-12T00-00-00Z-{sha}"
    score, mx, detail = lap(seconds)
    url = f"https://api.example/{name}/0"
    api.assets[url] = json.dumps({"score": score, "max-score": mx, "tests": [
        {"test-name": "C1 drives one lap", "passed": True, "score": 40, "max-score": 40, "detail": detail}]}).encode()
    api.tags_by_repo[repo] = [{"tag_name": tag, "sha": sha + "0" * 33}]
    api.runs_by_repo[repo] = [{"head_sha": sha + "0" * 33, "created_at": f"{year}-09-12T10:00:00Z", "name": "Autograde"}]
    api.releases_by_repo[repo] = [{"tag_name": tag, "created_at": f"{year}-09-12T10:05:00Z", "assets": [{"name": "result.json", "url": url}]}]
    api.pulls[repo] = 1
    return repo


def test_moving_the_dates_to_next_year_starts_a_new_board_by_itself(tmp_path):
    """Same classroom, same lab, last year's repositories still in the org: the only thing
    anyone does for the new term is set the assignment's dates."""
    api = FakeApi()
    add_student(api, "cedrichld", [lap(13.47)], ["2026-09-07T03:46:01Z"])                 # staff: tested days before opening
    add_student(api, "alice", [lap(14.2)], ["2026-09-10T10:00:00Z"])
    add_student(api, "bob", [lap(13.2)], ["2026-09-11T10:00:00Z"])
    assert run_build(api, tmp_path, log()) == 0
    assert len(json.loads((tmp_path / f"{SLUG}.json").read_text())["rows"]) == 2

    from test_build import ASSIGNMENTS
    moved = json.loads(json.dumps(ASSIGNMENTS))
    for a in moved:
        a["due"] = a["due"].replace("2026", "2027")
        if a.get("available_from"):
            a["available_from"] = a["available_from"].replace("2026", "2027")
    api.files[(f"{ORG}/classroom50", f"{CLASSROOM}/assignments.json")] = json.dumps(moved)
    add_later_student(api, "zoe", 2027, 12.9)
    assert run_build(api, tmp_path, log()) == 0
    doc = json.loads((tmp_path / f"{SLUG}.json").read_text())
    assert [r["metric"] for r in doc["rows"]] == [12.9] and len(doc["players"]) == 1      # only this term's driver
    assert doc["reference"]["metric"] == 13.47                                            # the staff time carries over
    terms = json.loads((tmp_path / "archive/index.json").read_text())["terms"]
    assert len(terms) == 1 and terms[0]["label"].endswith("2026")
    old = json.loads((tmp_path / "archive" / terms[0]["label"] / f"{SLUG}.json").read_text())
    assert sorted(r["metric"] for r in old["rows"]) == [13.2, 14.2]
    assert run_build(api, tmp_path, log()) == 0                                           # and it does not archive twice
    assert len(json.loads((tmp_path / "archive/index.json").read_text())["terms"]) == 1
    assert [r["metric"] for r in json.loads((tmp_path / f"{SLUG}.json").read_text())["rows"]] == [12.9]
