"""A lap time of zero is a grader glitch (lab 3, 2026-09-18: the referee closed the run
between the sim's lap_count and lap_time messages), never a lap: it must not rank, and
the attempt must stay readable so a regrade can reach the board."""
import io
import json

from builder import build
from test_build import CLASSROOM, ORG, SLUG, FakeApi, add_student, lap, run_build


def board(data_dir):
    return json.loads((data_dir / f"{SLUG}.json").read_text())


def test_a_zero_lap_never_ranks_and_a_regrade_reaches_the_board(tmp_path):
    api, log = FakeApi(), build.Log(io.StringIO())
    add_student(api, "cedrichld", [lap(13.47)], ["2026-09-07T03:46:01Z"])
    add_student(api, "alice", [lap(14.2)], ["2026-09-10T10:00:00Z"])
    bob = add_student(api, "bob", [lap(0.0)], ["2026-09-10T11:00:00Z"])
    assert run_build(api, tmp_path, log) == 0
    doc = board(tmp_path)
    assert [r["metric"] for r in doc["rows"]] == [14.2]                     # not P1 with 0.00 s
    assert [u["used"] for u in doc["unranked"]] == [1]                       # still counted, still listed

    # staff re-run the grading with the fixed grader: same tag, new result
    url = api.releases_by_repo[bob][0]["assets"][0]["url"]
    score, mx, detail = lap(15.02)
    api.assets[url] = json.dumps({"score": score, "max-score": mx, "tests": [
        {"test-name": "C1 drives one lap", "passed": True, "score": 40, "max-score": 40, "detail": detail}]}).encode()
    assert run_build(api, tmp_path, log) == 0
    assert [r["metric"] for r in board(tmp_path)["rows"]] == [14.2, 15.02]

    # a healthy attempt is read once, as before
    listed, real = [], api.releases
    api.releases = lambda repo: (listed.append(repo), real(repo))[1]
    assert run_build(api, tmp_path, log) == 0
    assert listed == []


def test_a_zero_reference_is_ignored(tmp_path):
    api, log = FakeApi(), build.Log(io.StringIO())
    add_student(api, "cedrichld", [lap(0.0), lap(13.47)], ["2026-09-07T03:46:01Z", "2026-09-07T04:00:00Z"])
    add_student(api, "alice", [lap(14.2)], ["2026-09-10T10:00:00Z"])
    assert run_build(api, tmp_path, log) == 0
    assert board(tmp_path)["reference"]["metric"] == 13.47
