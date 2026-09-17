"""Team boards: a `mode: team` lab is keyed by team number, not by an alias."""
import io
import json

import pytest

from builder import build, rules, teams

from test_build import ORG, CLASSROOM, CONFIG_YAML, FakeApi, add_student, lap

SLUG = "lab-4-follow-the-gap"
ASSIGNMENTS = [{"slug": SLUG, "name": "Lab 4: Follow the Gap", "mode": "team",
                "team_formation": "teacher", "max_group_size": 4,
                "due": "2026-09-26T03:59:00Z"}]


def teams_json(groups):
    """groups: {counter: [members]} -> a teams.json body for SLUG."""
    h = "86471e6c42321c77"
    return json.dumps({"schema": "classroom50/teams/v1", "assignments": {SLUG: {"teams": [
        {"slug": f"classroom50-group-{h}-{n}", "id": 100 + n, "name": f"Team {n}", "members": m}
        for n, m in sorted(groups.items())]}}})


@pytest.fixture
def world(tmp_path):
    api = FakeApi()
    api.files[(f"{ORG}/classroom50", f"{CLASSROOM}/assignments.json")] = json.dumps(ASSIGNMENTS)
    api.files[(f"{ORG}/classroom50", f"{CLASSROOM}/autograders/{SLUG}/config.yaml")] = CONFIG_YAML
    api.files[(f"{ORG}/classroom50", f"{CLASSROOM}/teams.json")] = teams_json(
        {2: ["alice", "bob"], 10: ["carol"], 15: ["cedrichld"]})
    return api, tmp_path, build.Log(io.StringIO())


def add_team(api, n, results, times):
    return add_student(api, f"group-{n}", results, times, slug=SLUG)


def run(api, data_dir, log, dry_run=False):
    return build.build(api, ORG, CLASSROOM, "salt", "tok", data_dir, "https://x/leaderboard",
                       None, dry_run, log)


def test_rows_are_team_numbers_not_aliases(world):
    api, data_dir, log = world
    add_team(api, 2, [lap(12.0)], ["2026-09-20T10:00:00Z"])
    add_team(api, 10, [lap(11.0)], ["2026-09-20T11:00:00Z"])
    assert run(api, data_dir, log) == 0
    doc = json.loads((data_dir / f"{SLUG}.json").read_text())
    assert [r["alias"] for r in doc["rows"]] == ["Team 10", "Team 2"]
    assert doc["anonymous"] is False
    assert sorted(doc["players"]) == ["Team 10", "Team 2"]
    # the tail is stored in the clear: the board is not anonymous
    assert doc["players"]["Team 2"]["owner"] == "group-2"


def test_a_staff_team_is_the_reference_not_a_competitor(world):
    api, data_dir, log = world
    add_team(api, 2, [lap(12.0)], ["2026-09-20T10:00:00Z"])
    add_team(api, 15, [lap(9.5)], ["2026-09-19T10:00:00Z"])     # all-staff team
    assert run(api, data_dir, log) == 0
    doc = json.loads((data_dir / f"{SLUG}.json").read_text())
    assert [r["alias"] for r in doc["rows"]] == ["Team 2"]
    assert doc["reference"]["metric"] == 9.5
    assert "Team 15" not in doc["players"]


def test_an_unknown_team_is_ranked_rather_than_silently_dropped(world):
    """A team missing from the snapshot (freshly created, or a drifted file)
    must still appear — losing a reference row is cheap, losing a team is not."""
    api, data_dir, log = world
    add_team(api, 7, [lap(12.0)], ["2026-09-20T10:00:00Z"])
    assert run(api, data_dir, log) == 0
    doc = json.loads((data_dir / f"{SLUG}.json").read_text())
    assert [r["alias"] for r in doc["rows"]] == ["Team 7"]


def test_unreadable_teams_json_still_builds(world):
    api, data_dir, log = world
    del api.files[(f"{ORG}/classroom50", f"{CLASSROOM}/teams.json")]
    add_team(api, 2, [lap(12.0)], ["2026-09-20T10:00:00Z"])
    assert run(api, data_dir, log) == 0
    doc = json.loads((data_dir / f"{SLUG}.json").read_text())
    assert [r["alias"] for r in doc["rows"]] == ["Team 2"]


def test_team_boards_post_no_notes(world):
    api, data_dir, log = world
    add_team(api, 2, [lap(12.0)], ["2026-09-20T10:00:00Z"])
    api.pulls[f"{ORG}/{CLASSROOM}-{SLUG}-group-2"] = 1
    assert run(api, data_dir, log) == 0
    assert api.comments == {} and api.issues == {}
    doc = json.loads((data_dir / f"{SLUG}.json").read_text())
    assert "note" not in doc["players"]["Team 2"]


def test_teams_sort_numerically_not_lexically():
    players = {f"Team {n}": {"submissions": [], "locked": False} for n in (1, 2, 10, 14)}
    metric = rules.metric_from_config(
        {"test": "C1", "pattern": r"(?P<lap_s>[0-9.]+)", "metric": "lap_s"})
    _, unranked = rules.rank_players(players, metric, 10, None)
    assert [u["alias"] for u in unranked] == ["Team 1", "Team 2", "Team 10", "Team 14"]


def test_ops_commands_take_a_team_any_way_a_ta_writes_it(world):
    api, data_dir, log = world
    add_team(api, 7, [lap(12.0)], ["2026-09-20T10:00:00Z"])
    run(api, data_dir, log)
    doc = json.loads((data_dir / f"{SLUG}.json").read_text())
    for written in ("7", "group-7", "Team 7", "team 7"):
        assert build.board_key(doc, "salt", written) == ("Team 7", "group-7")
    with pytest.raises(ValueError):
        build.board_key(doc, "salt", "alice")


def test_refund_targets_the_team_repo(world):
    api, data_dir, log = world
    add_team(api, 7, [None, lap(12.0)], ["2026-09-20T10:00:00Z", "2026-09-20T11:00:00Z"])
    run(api, data_dir, log)
    repo = f"{ORG}/{CLASSROOM}-{SLUG}-group-7"
    tag = api.tags_by_repo[repo][0]["tag_name"]           # the ungraded attempt
    assert build.refund(api, ORG, CLASSROOM, "salt", SLUG, "7", tag, data_dir, unlock=False) == 0
    doc = json.loads((data_dir / f"{SLUG}.json").read_text())
    assert doc["players"]["Team 7"]["submissions"][0]["refunded"] is True
    assert tag in api.deleted_tags
