"""A board that names a `replay:` asset publishes each entry's best recorded run."""
import io
import json

import pytest

from builder import build, replays
from test_build import CLASSROOM, CONFIG_YAML, ORG, SLUG, FakeApi, add_student, lap, run_build

BOARD_DIR = "replays/lab-3-wall-following"


def recording(seconds, **over):
    doc = {"v": 1, "map": "levine_blocked", "hz": 20, "n": 4, "x": [0, 5, 5, 5], "y": [0, 0, 0, 0],
           "yaw": [0, 0, 0, 0], "s": [400, 0, 0, 0], "laps": [[0, 3]], "lap_ms": [round(seconds * 1000)], "best": 0}
    doc.update(over)
    return doc


def attach(api, repo, attempt, doc, size=None, name="replay.json"):
    raw = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
    url = f"https://api.example/replay/{repo}/{attempt}"
    api.assets[url] = raw
    api.releases_by_repo[repo][attempt]["assets"].append(
        {"name": name, "url": url, "size": len(raw) if size is None else size})
    return url


@pytest.fixture
def world(tmp_path):
    api = FakeApi()
    api.files[(f"{ORG}/classroom50", f"{CLASSROOM}/autograders/{SLUG}/config.yaml")] = \
        CONFIG_YAML.replace("  podium: 5\n", "  podium: 5\n  replay: replay.json\n")
    data_dir = tmp_path / "docs" / "data"
    data_dir.mkdir(parents=True)
    maps = tmp_path / "docs" / "assets" / "maps"
    maps.mkdir(parents=True)
    (maps / "maps.json").write_text(json.dumps({"levine_blocked": {}}))
    staff = add_student(api, "cedrichld", [lap(13.47)], ["2026-09-07T03:46:01Z"])
    attach(api, staff, 0, recording(13.47))
    return api, data_dir, build.Log(io.StringIO())


def downloads(api):
    """Every asset URL the build fetches from here on."""
    seen, real = [], api.download_asset
    api.download_asset = lambda url: (seen.append(url), real(url))[1]
    return seen


def board(data_dir):
    return json.loads((data_dir / f"{SLUG}.json").read_text())


def published(data_dir, ref):
    return json.loads((data_dir / ref.partition("?v=")[0]).read_text())


def test_best_run_and_reference_are_published(world):
    api, data_dir, log = world
    alice = add_student(api, "alice", [lap(13.0), lap(12.0)], ["2026-09-10T10:00:00Z", "2026-09-11T10:00:00Z"])
    attach(api, alice, 0, recording(13.0))
    attach(api, alice, 1, recording(12.0, owner="alice", note="<img onerror=alert(1)>"))
    assert run_build(api, data_dir, log) == 0
    doc = board(data_dir)
    row = doc["rows"][0]
    assert row["metric"] == 12.0 and row["replay"].startswith(f"{BOARD_DIR}/") and "?v=" in row["replay"]
    assert published(data_dir, row["replay"]) == recording(12.0)          # the best lap, extra keys gone
    assert doc["reference"]["replay"].startswith(f"{BOARD_DIR}/reference.json?v=")
    assert published(data_dir, doc["reference"]["replay"]) == recording(13.47)
    everything = "".join(p.read_text() for p in data_dir.rglob("*.json")) + " ".join(str(p) for p in data_dir.rglob("*"))
    for leak in ("alice", "cedrichld", "submit/", "onerror"):
        assert leak not in everything.lower(), f"{leak} leaked"
    assert len(list((data_dir / BOARD_DIR).glob("*.json"))) == 2           # one file per entry, not per attempt


def test_a_slower_later_run_keeps_the_best_recording(world):
    api, data_dir, log = world
    alice = add_student(api, "alice", [lap(12.0)], ["2026-09-10T10:00:00Z"])
    attach(api, alice, 0, recording(12.0))
    assert run_build(api, data_dir, log) == 0
    first = board(data_dir)["rows"][0]["replay"]
    # a second, slower attempt arrives
    name = f"{CLASSROOM}-{SLUG}-alice"
    sha = "abcdef1"
    tag = "submit/2026-09-12T00-00-00Z-" + sha
    api.tags_by_repo[alice].append({"tag_name": tag, "sha": sha + "0" * 33})
    api.runs_by_repo[alice].append({"head_sha": sha + "0" * 33, "created_at": "2026-09-12T10:00:00Z", "name": "Autograde"})
    url = f"https://api.example/{name}/late"
    score, mx, detail = lap(14.0)
    api.assets[url] = json.dumps({"score": score, "max-score": mx, "tests": [
        {"test-name": "C1 drives one lap", "passed": True, "score": 40, "max-score": 40, "detail": detail}]}).encode()
    api.releases_by_repo[alice].append({"tag_name": tag, "created_at": "2026-09-12T10:00:00Z",
                                        "assets": [{"name": "result.json", "url": url}]})
    attach(api, alice, 1, recording(14.0))
    assert run_build(api, data_dir, log) == 0
    row = board(data_dir)["rows"][0]
    assert row["metric"] == 12.0 and row["replay"] == first
    assert published(data_dir, row["replay"]) == recording(12.0)


def test_a_refused_recording_costs_only_the_button(world):
    api, data_dir, log = world
    alice = add_student(api, "alice", [lap(12.0)], ["2026-09-10T10:00:00Z"])
    attach(api, alice, 0, recording(9.0))                                  # not the lap the board ranks
    bob = add_student(api, "bob", [lap(12.5)], ["2026-09-10T11:00:00Z"])
    huge = attach(api, bob, 0, recording(12.5), size=replays.MAX_BYTES + 1)
    carol = add_student(api, "carol", [lap(12.7)], ["2026-09-10T12:00:00Z"])
    attach(api, carol, 0, b"{broken")
    fetched = downloads(api)
    assert run_build(api, data_dir, log) == 0
    assert huge not in fetched                                             # refused on its listed size, never downloaded
    rows = board(data_dir)["rows"]
    assert [r["metric"] for r in rows] == [12.0, 12.5, 12.7]
    assert all("replay" not in r for r in rows)


def test_boards_without_the_key_publish_nothing(world):
    api, data_dir, log = world
    api.files[(f"{ORG}/classroom50", f"{CLASSROOM}/autograders/{SLUG}/config.yaml")] = CONFIG_YAML
    alice = add_student(api, "alice", [lap(12.0)], ["2026-09-10T10:00:00Z"])
    url = attach(api, alice, 0, recording(12.0))
    fetched = downloads(api)
    assert run_build(api, data_dir, log) == 0
    assert url not in fetched and fetched                                  # results were read, the recording was not
    doc = board(data_dir)
    assert "replay" not in doc["rows"][0] and "replay" not in doc["reference"]
    assert not (data_dir / "replays").exists()


def test_a_run_graded_before_the_board_read_recordings_is_caught_up_once(world):
    """The grader started attaching recordings before this builder shipped, and a
    release is otherwise read only once: the best attempt gets one more look."""
    api, data_dir, log = world
    with_key = api.files[(f"{ORG}/classroom50", f"{CLASSROOM}/autograders/{SLUG}/config.yaml")]
    api.files[(f"{ORG}/classroom50", f"{CLASSROOM}/autograders/{SLUG}/config.yaml")] = CONFIG_YAML   # the old builder
    alice = add_student(api, "alice", [lap(13.0), lap(12.0)], ["2026-09-10T10:00:00Z", "2026-09-11T10:00:00Z"])
    attach(api, alice, 0, recording(13.0))
    attach(api, alice, 1, recording(12.0))
    bob = add_student(api, "bob", [lap(12.5)], ["2026-09-10T11:00:00Z"])            # graded with no recording at all
    assert run_build(api, data_dir, log) == 0
    assert all("replay" not in r for r in board(data_dir)["rows"])

    api.files[(f"{ORG}/classroom50", f"{CLASSROOM}/autograders/{SLUG}/config.yaml")] = with_key
    listed, real = [], api.releases
    api.releases = lambda repo: (listed.append(repo), real(repo))[1]
    assert run_build(api, data_dir, log) == 0
    rows = {r["metric"]: r for r in board(data_dir)["rows"]}
    assert published(data_dir, rows[12.0]["replay"]) == recording(12.0)              # the best attempt, not the first
    assert "replay" not in rows[12.5]
    assert published(data_dir, board(data_dir)["reference"]["replay"]) == recording(13.47)
    assert sorted(listed) == sorted([alice, bob, f"{ORG}/{CLASSROOM}-{SLUG}-cedrichld"])

    listed.clear()
    assert run_build(api, data_dir, log) == 0
    assert listed == []                                                             # one look each, never again
    assert published(data_dir, board(data_dir)["rows"][0]["replay"]) == recording(12.0)


def backfill(data_dir, entries):
    """What the staff backfill tool leaves behind: recordings plus a sidecar naming the attempt each belongs to."""
    index = {}
    for key, (attempt, doc) in entries.items():
        index[key] = {"attempt": attempt, "replay": replays.save(data_dir, SLUG, key, {**doc, "rerun": 1})}
    (data_dir / BOARD_DIR / "backfill.json").write_text(json.dumps(index))
    return index


def test_backfilled_recordings_follow_the_attempt_they_were_made_for(world):
    from builder import rules
    api, data_dir, log = world
    api.files[(f"{ORG}/classroom50", f"{CLASSROOM}/autograders/{SLUG}/config.yaml")] = CONFIG_YAML   # graded long before replays
    alice = add_student(api, "alice", [lap(13.0), lap(12.0)], ["2026-09-10T10:00:00Z", "2026-09-11T10:00:00Z"])
    add_student(api, "bob", [lap(12.5)], ["2026-09-10T11:00:00Z"])
    assert run_build(api, data_dir, log) == 0
    doc = board(data_dir)
    alias = next(r["alias"] for r in doc["rows"] if r["metric"] == 12.0)
    best = rules.attempt_id("submit/2026-09-11T00-00-00Z-0000001")
    staff = rules.attempt_id("submit/2026-09-10T00-00-00Z-0000000")
    index = backfill(data_dir, {alias: (best, recording(12.04)), "reference": (staff, recording(13.5)),
                                "Nobody 1": ("feedfacefeedface", recording(9.0))})
    assert run_build(api, data_dir, log) == 0
    doc = board(data_dir)
    rows = {r["metric"]: r for r in doc["rows"]}
    assert rows[12.0]["replay"] == index[alias]["replay"] and published(data_dir, rows[12.0]["replay"])["rerun"] == 1
    assert "replay" not in rows[12.5]
    assert doc["reference"]["replay"] == index["reference"]["replay"]

    # a better run arrives with no recording of its own: the old re-run is not passed off as it
    name, sha = f"{CLASSROOM}-{SLUG}-alice", "abcdef2"
    tag = "submit/2026-09-13T00-00-00Z-" + sha
    api.tags_by_repo[alice].append({"tag_name": tag, "sha": sha + "0" * 33})
    api.runs_by_repo[alice].append({"head_sha": sha + "0" * 33, "created_at": "2026-09-13T10:00:00Z", "name": "Autograde"})
    url = f"https://api.example/{name}/better"
    score, mx, detail = lap(11.0)
    api.assets[url] = json.dumps({"score": score, "max-score": mx, "tests": [
        {"test-name": "C1 drives one lap", "passed": True, "score": 40, "max-score": 40, "detail": detail}]}).encode()
    api.releases_by_repo[alice].append({"tag_name": tag, "created_at": "2026-09-13T10:00:00Z",
                                        "assets": [{"name": "result.json", "url": url}]})
    assert run_build(api, data_dir, log) == 0
    rows = {r["metric"]: r for r in board(data_dir)["rows"]}
    assert 11.0 in rows and "replay" not in rows[11.0]


def test_a_malformed_sidecar_is_ignored(world):
    api, data_dir, log = world
    add_student(api, "alice", [lap(12.0)], ["2026-09-10T10:00:00Z"])
    (data_dir / BOARD_DIR).mkdir(parents=True, exist_ok=True)
    for text in ("{broken", "[1, 2]", json.dumps({"x": "y"}), json.dumps({"x": {"attempt": 5, "replay": "../../index.html"}})):
        (data_dir / BOARD_DIR / "backfill.json").write_text(text)
        assert run_build(api, data_dir, log) == 0
        assert "replay" not in board(data_dir)["rows"][0]


def test_a_backfilled_recording_off_the_row_time_is_not_linked(world):
    """A re-run is not the graded run: one whose ranked lap disagrees with the row's
    time would finish the car out of the board's order, so it is left unlinked."""
    from builder import rules
    api, data_dir, log = world
    api.files[(f"{ORG}/classroom50", f"{CLASSROOM}/autograders/{SLUG}/config.yaml")] = CONFIG_YAML
    add_student(api, "alice", [lap(12.0)], ["2026-09-10T10:00:00Z"])
    add_student(api, "bob", [lap(12.5)], ["2026-09-10T11:00:00Z"])
    assert run_build(api, data_dir, log) == 0
    doc = board(data_dir)
    alice_alias = next(r["alias"] for r in doc["rows"] if r["metric"] == 12.0)
    bob_alias = next(r["alias"] for r in doc["rows"] if r["metric"] == 12.5)
    attempt = rules.attempt_id("submit/2026-09-10T00-00-00Z-0000000")
    backfill(data_dir, {alice_alias: (attempt, recording(12.3)), bob_alias: (attempt, recording(12.5)),
                        "reference": (attempt, recording(13.6))})
    assert run_build(api, data_dir, log) == 0
    doc = board(data_dir)
    rows = {r["metric"]: r for r in doc["rows"]}
    assert "replay" not in rows[12.0] and "replay" in rows[12.5]
    assert "replay" not in doc["reference"]
