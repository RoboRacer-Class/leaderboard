import io
import json
from pathlib import Path

import pytest

from builder import build, lock, rules
from builder.gh import GitHubError

ORG, CLASSROOM = "RoboRacer-Class", "ese-6150"
SLUG = "lab-3-wall-following"
CONFIG_YAML = """
lap:
  points: 40
leaderboard:
  title: Fastest clean lap
  test: C1
  pattern: 'lap time (?P<lap_s>[0-9.]+) s(?:, average (?P<avg_mps>[0-9.]+) m/s)?(?:, top (?P<top_mps>[0-9.]+) m/s)?'
  metric: lap_s
  label: Lap time
  unit: s
  direction: lower
  podium: 5
  extras:
    - {key: avg_mps, label: Avg speed, unit: m/s}
submissions:
  cap: 5
  exempt_users: [cedrichld]
"""
ASSIGNMENTS = [
    {"slug": "lab-2-automatic-emergency-braking", "name": "Lab 2: AEB", "due": "2026-09-12T03:59:00Z"},
    {"slug": SLUG, "name": "Lab 3: Wall Following", "due": "2026-09-17T03:59:00Z",
     "available_from": "2026-09-09T16:00:00Z"},
]


class FakeApi:
    def __init__(self):
        self.files = {
            (f"{ORG}/classroom50", f"{CLASSROOM}/assignments.json"): json.dumps(ASSIGNMENTS),
            (f"{ORG}/classroom50", f"{CLASSROOM}/autograders/{SLUG}/config.yaml"): CONFIG_YAML,
            (f"{ORG}/classroom50", f"{CLASSROOM}/autograders/lab-2-automatic-emergency-braking/config.yaml"): "scenarios: []\n",
        }
        self.repos, self.releases_by_repo, self.assets, self.runs_by_repo = [], {}, {}, {}
        self.tags_by_repo, self.deleted_tags = {}, []
        self.teams = {f"classroom50-{CLASSROOM}-teacher": ["rahulmangharam"],
                      f"classroom50-{CLASSROOM}-hta": ["CedricHLD"], f"classroom50-{CLASSROOM}-ta": []}
        self.pulls, self.comments, self.issues, self.perms = {}, {}, {}, {}
        self.next_id = 500
        self.calls = 0

    # config
    def file_text(self, repo, path, ref="main"):
        try:
            return self.files[(repo, path)]
        except KeyError:
            raise GitHubError(404, path, "not found") from None

    def org_repos(self, org):
        return list(self.repos)

    def team_members(self, org, slug):
        return self.teams.get(slug, [])

    # submissions
    def tag_refs(self, repo, prefix="submit/"):
        return [{"name": r["tag_name"], "sha": r["sha"]} for r in self.tags_by_repo.get(repo, [])]

    def delete_tag(self, repo, tag):
        self.tags_by_repo[repo] = [t for t in self.tags_by_repo.get(repo, []) if t["tag_name"] != tag]
        self.deleted_tags.append(tag)

    def releases(self, repo):
        return self.releases_by_repo.get(repo, [])

    def download_asset(self, url):
        return self.assets[url]

    def workflow_runs(self, repo):
        return self.runs_by_repo.get(repo, [])

    # notes
    def feedback_pr(self, repo, base="feedback"):
        return self.pulls.get(repo)

    def issue_comments(self, repo, number):
        return self.comments.get((repo, number), [])

    def create_comment(self, repo, number, body):
        self.next_id += 1
        self.comments.setdefault((repo, number), []).append({"id": self.next_id, "body": body, "user": {"login": "bot"}})
        return self.next_id

    def update_comment(self, repo, comment_id, body):
        for lst in self.comments.values():
            for c in lst:
                if c["id"] == comment_id:
                    c["body"] = body
                    return
        raise GitHubError(404, "comment", "gone")

    def create_issue(self, repo, title, body):
        self.next_id += 1
        self.issues[(repo, self.next_id)] = body
        return self.next_id

    def update_issue(self, repo, number, body):
        self.issues[(repo, number)] = body

    # locks
    def permission(self, repo, username):
        return self.perms.get((repo, username), "write")

    def set_permission(self, repo, username, permission):
        self.perms[(repo, username)] = "read" if permission == "pull" else "write"


def add_student(api, username, results, times, pr=1):
    """results: list of (score, max, detail) or None for an attempt that never
    graded (tag only); times: list of ISO push times."""
    name = f"{CLASSROOM}-{SLUG}-{username}"
    api.repos.append(name)
    repo = f"{ORG}/{name}"
    rels, runs, tags = [], [], []
    for i, (res, at) in enumerate(zip(results, times)):
        sha = f"{i:07x}"
        tag = f"submit/2026-09-1{i}T00-00-00Z-{sha}"
        tags.append({"tag_name": tag, "sha": sha + "0" * 33})
        runs.append({"head_sha": sha + "0" * 33, "created_at": at, "name": "Autograde"})
        if res is None:
            continue
        score, mx, detail = res
        url = f"https://api.example/{name}/{i}"
        api.assets[url] = json.dumps({"score": score, "max-score": mx, "tests": [
            {"test-name": "C1 drives one lap", "passed": score == mx, "score": 40, "max-score": 40, "detail": detail}]}).encode()
        rels.append({"tag_name": tag, "created_at": "2026-09-30T00:00:00Z",   # regraded later: must not matter
                     "assets": [{"name": "result.json", "url": url}]})
    api.releases_by_repo[repo] = rels
    api.runs_by_repo[repo] = runs
    api.tags_by_repo[repo] = tags
    if pr:
        api.pulls[repo] = pr
    return repo


def lap(seconds):
    return (90, 90, f"lap time {seconds} s, average 4.5 m/s, top 5.0 m/s")


@pytest.fixture
def world(tmp_path):
    api = FakeApi()
    add_student(api, "cedrichld", [lap(13.47)], ["2026-09-07T03:46:01Z"])           # staff reference
    add_student(api, "alice", [(53, 90, "touched a wall"), lap(14.2)], ["2026-09-10T10:00:00Z", "2026-09-11T10:00:00Z"])
    add_student(api, "bob", [lap(t) for t in (16, 15, 14, 13.9, 13.2)], [f"2026-09-1{i}T12:00:00Z" for i in range(5)])
    add_student(api, "carol", [lap(9.0)], ["2026-09-17T04:30:00Z"])                # late
    add_student(api, "dave", [lap(13.0)], ["2026-09-12T00:00:00Z"], pr=None)        # no Feedback PR -> issue
    add_student(api, "erin", [None, lap(13.5)], ["2026-09-12T01:00:00Z", "2026-09-12T02:00:00Z"])  # errored run, then a lap
    api.repos.append(f"{CLASSROOM}-lab-2-automatic-emergency-braking-alice")       # other lab, ignored
    stream = io.StringIO()
    log = build.Log(stream)
    return api, tmp_path, log, stream


def run_build(api, data_dir, log, dry_run=False):
    return build.build(api, ORG, CLASSROOM, "salt", "tok", data_dir, "https://x/leaderboard",
                       None, dry_run, log)


def test_build_ranks_locks_notes_and_hides_names(world):
    api, data_dir, log, stream = world
    assert run_build(api, data_dir, log) == 0
    doc = json.loads((data_dir / f"{SLUG}.json").read_text())
    index = json.loads((data_dir / "index.json").read_text())
    text = (data_dir / f"{SLUG}.json").read_text() + (data_dir / "index.json").read_text() + stream.getvalue()
    for name in ("alice", "bob", "carol", "dave", "erin", "cedrichld", "rahulmangharam"):
        assert name not in text.lower(), f"{name} leaked"
    assert "submit/" not in text, "tag names leaked"
    assert [lab["slug"] for lab in index["labs"]] == [SLUG]
    assert doc["reference"]["metric"] == 13.47 and doc["cap"] == 5 and doc["podium"] == 5
    ranked = [(r["rank"], r["metric"], r["attempt"], r["used"], r["locked"]) for r in doc["rows"]]
    # erin's errored run spent attempt #1; her lap is attempt #2
    assert ranked == [(1, 13.0, 1, 1, False), (2, 13.2, 5, 5, True), (3, 13.5, 2, 2, False), (4, 14.2, 2, 2, False)]
    assert [(u["used"]) for u in doc["unranked"]] == [1]          # carol: late, still spent one
    assert len(doc["players"]) == 5
    # bob hit the cap: repo is read-only, via the API fallback (no script in this world)
    assert api.perms[(f"{ORG}/{CLASSROOM}-{SLUG}-bob", "bob")] == "read"
    assert "locked via api (5/5)" in stream.getvalue()
    # notes: one PR comment each for alice/bob/carol, an issue for dave, nothing for staff
    bodies = [c["body"] for lst in api.comments.values() for c in lst]
    assert len(bodies) == 4 and all("Your alias on the public board is" in b for b in bodies)
    assert len(api.issues) == 1
    assert not any("cedrichld" in k[0] for k in api.comments) and not any("cedrichld" in k[0] for k in api.issues)
    bob_alias = next(r["alias"] for r in doc["rows"] if r["rank"] == 2)
    assert any(f"**{bob_alias}**" in b and "5 of 5" in b and "read-only" in b for b in bodies)


def test_second_build_is_idempotent_and_survives_deleted_releases(world):
    api, data_dir, log, stream = world
    run_build(api, data_dir, log)
    first = (data_dir / f"{SLUG}.json").read_text()
    comments_before = repr(sorted(api.comments.items()))
    # bob deletes his tags and releases to reset the count: the state remembers them
    api.releases_by_repo[f"{ORG}/{CLASSROOM}-{SLUG}-bob"] = []
    api.tags_by_repo[f"{ORG}/{CLASSROOM}-{SLUG}-bob"] = []
    run_build(api, data_dir, log)
    second = json.loads((data_dir / f"{SLUG}.json").read_text())
    assert json.loads(first)["rows"] == second["rows"]
    assert repr(sorted(api.comments.items())) == comments_before
    assert "new attempt" not in stream.getvalue().split("scanning", 2)[-1]


def test_dry_run_writes_data_but_touches_nothing(world):
    api, data_dir, log, stream = world
    run_build(api, data_dir, log, dry_run=True)
    assert (data_dir / f"{SLUG}.json").is_file()
    assert not api.comments and not api.issues and not api.perms
    assert "would lock (5/5)" in stream.getvalue() and "would update note" in stream.getvalue()


def test_refund_restores_attempt_and_unlocks(world):
    api, data_dir, log, stream = world
    run_build(api, data_dir, log)
    doc = json.loads((data_dir / f"{SLUG}.json").read_text())
    bob_alias = next(r["alias"] for r in doc["rows"] if r["rank"] == 2)
    tag = api.tags_by_repo[f"{ORG}/{CLASSROOM}-{SLUG}-bob"][0]["tag_name"]
    assert build.refund(api, ORG, CLASSROOM, "salt", SLUG, "bob", tag, data_dir, unlock=True) == 0
    assert api.deleted_tags == []            # a graded attempt keeps its tag and release
    assert api.perms[(f"{ORG}/{CLASSROOM}-{SLUG}-bob", "bob")] == "write"
    run_build(api, data_dir, log)
    doc = json.loads((data_dir / f"{SLUG}.json").read_text())
    bob = next(r for r in doc["rows"] if r["alias"] == bob_alias)
    assert bob["used"] == 4 and bob["locked"] is False and bob["metric"] == 13.2


def test_refund_of_an_errored_attempt_deletes_its_tag(world):
    api, data_dir, log, stream = world
    run_build(api, data_dir, log)
    tag = api.tags_by_repo[f"{ORG}/{CLASSROOM}-{SLUG}-erin"][0]["tag_name"]
    assert build.refund(api, ORG, CLASSROOM, "salt", SLUG, "erin", tag, data_dir, unlock=False) == 0
    assert api.deleted_tags == [tag]
    run_build(api, data_dir, log)
    doc = json.loads((data_dir / f"{SLUG}.json").read_text())
    erin = next(r for r in doc["rows"] if r["metric"] == 13.5)
    assert erin["used"] == 1 and erin["attempt"] == 1


def test_reveal_finds_alias(world, capsys):
    api, data_dir, log, stream = world
    run_build(api, data_dir, log)
    build.reveal("salt", SLUG, ["bob", "nobody"], data_dir)
    out = capsys.readouterr().out
    assert "bob\t" in out and "rank 2" in out and "nobody\t" in out and "no graded submission" in out


def test_refuses_to_run_without_staff(world):
    api, data_dir, log, stream = world
    api.teams = {}
    assert run_build(api, data_dir, log) == 1
    assert not (data_dir / f"{SLUG}.json").exists()


def test_submission_time_prefers_run_creation():
    sha = "9bb0e0a" + "f" * 33
    runs = [{"head_sha": sha, "created_at": "2026-09-07T03:46:01Z"},
            {"head_sha": sha, "created_at": "2026-09-09T00:00:00Z"}]
    assert build.submission_time(sha, runs, "2026-09-20T00:00:00Z") == "2026-09-07T03:46:01Z"
    assert build.submission_time(sha, [], "2026-09-20T00:00:00Z") == "2026-09-20T00:00:00Z"


def test_lock_uses_script_first_then_api():
    class Perm:
        def __init__(self): self.value = "write"; self.sets = []
        def permission(self, repo, user): return self.value
        def set_permission(self, repo, user, perm): self.sets.append(perm); self.value = "read"
    api = Perm()
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd); api.value = "read"      # the script did it
    ok, how = lock.lock_repo(api, ORG, CLASSROOM, SLUG, "bob", "tok", "#!/bin/bash\n", run=fake_run)
    assert ok and how == "script" and calls[0][2:] == ["lock", SLUG, "--user", "bob"] and api.sets == []
    api = Perm()
    ok, how = lock.lock_repo(api, ORG, CLASSROOM, SLUG, "bob", "tok", "#!/bin/bash\n", run=lambda *a, **k: None)
    assert ok and how == "api" and api.sets == ["pull"]


def test_log_redacts_names_case_insensitively():
    stream = io.StringIO()
    log = build.Log(stream)
    log.redact("AhmadAmine998", "ese-6150-lab-3-wall-following-ahmadamine998")
    log("repo ese-6150-lab-3-wall-following-AhmadAmine998 of ahmadamine998 failed")
    assert "ahmadamine" not in stream.getvalue().lower()


def test_unchanged_board_keeps_its_timestamp(world):
    api, data_dir, log, stream = world
    run_build(api, data_dir, log)
    before = (data_dir / f"{SLUG}.json").read_text()
    index_before = (data_dir / "index.json").read_text()
    real_now = build.now_utc
    build.now_utc = lambda: rules.parse_time("2030-01-01T00:00:00Z")   # a later run, nothing new
    try:
        run_build(api, data_dir, log)
    finally:
        build.now_utc = real_now
    assert (data_dir / f"{SLUG}.json").read_text() == before
    assert (data_dir / "index.json").read_text() == index_before


LAB4 = "lab-4-follow-the-gap"
LAB4_YAML = """
leaderboards:
  - key: lap
    short: FTG
    title: Fastest clean lap
    requirement: full marks on everything but the obstacle course
    require: {ignore: [D2, D3]}
    test: D1
    pattern: 'lap time (?P<lap_s>[0-9.]+) s'
    metric: lap_s
    label: Lap time
    unit: s
  - key: obstacles
    title: Fastest clean obstacle lap
    test: D3
    pattern: 'lap time (?P<lap_s>[0-9.]+) s'
    metric: lap_s
    label: Lap time
    unit: s
submissions:
  cap: 0
"""


def add_lab4_student(api, username, tests_by_attempt, times):
    name = f"{CLASSROOM}-{LAB4}-{username}"
    api.repos.append(name)
    repo = f"{ORG}/{name}"
    rels, runs, tags = [], [], []
    for i, (tests, at) in enumerate(zip(tests_by_attempt, times)):
        sha = f"{i:07x}"
        tag = f"submit/2026-09-2{i}T00-00-00Z-{sha}"
        tags.append({"tag_name": tag, "sha": sha + "0" * 33})
        runs.append({"head_sha": sha + "0" * 33, "created_at": at, "name": "Autograde"})
        url = f"https://api.example/{name}/{i}"
        score = sum(x["score"] for x in tests)
        mx = sum(x["max-score"] for x in tests)
        api.assets[url] = json.dumps({"score": score, "max-score": mx, "tests": tests}).encode()
        rels.append({"tag_name": tag, "created_at": at, "assets": [{"name": "result.json", "url": url}]})
    api.releases_by_repo[repo] = rels
    api.runs_by_repo[repo] = runs
    api.tags_by_repo[repo] = tags
    api.pulls[repo] = 1
    return repo


def lab4_tests(lap_s, corners, obs_lap_s=None):
    return [
        {"test-name": "A1 package", "score": 10, "max-score": 10, "detail": ""},
        {"test-name": "D1 lap", "score": 10, "max-score": 10, "detail": f"lap time {lap_s} s"},
        {"test-name": "D2 corners", "score": corners, "max-score": 5, "detail": ""},
        {"test-name": "D3 bonus", "score": 5 if obs_lap_s else 0, "max-score": 5,
         "detail": f"lap time {obs_lap_s} s" if obs_lap_s else "no clean lap"},
    ]


def test_two_boards_per_lab(world):
    api, data_dir, log, stream = world
    api.files[(f"{ORG}/classroom50", f"{CLASSROOM}/assignments.json")] = json.dumps(
        ASSIGNMENTS + [{"slug": LAB4, "name": "Lab 4: Follow the Gap", "due": "2026-09-26T03:59:00Z"}])
    api.files[(f"{ORG}/classroom50", f"{CLASSROOM}/autograders/{LAB4}/config.yaml")] = LAB4_YAML
    fast = add_lab4_student(api, "fay", [lab4_tests(12.0, 0)], ["2026-09-20T10:00:00Z"])      # fast, no obstacles
    add_lab4_student(api, "gus", [lab4_tests(20.0, 5, 40.0)], ["2026-09-20T11:00:00Z"])        # everything
    add_lab4_student(api, "cedrichld", [lab4_tests(15.0, 5, 30.0)], ["2026-09-19T10:00:00Z"]) # staff
    assert run_build(api, data_dir, log) == 0
    index = json.loads((data_dir / "index.json").read_text())
    boards = {e["slug"]: e for e in index["labs"] if e.get("assignment") == LAB4}
    assert set(boards) == {f"{LAB4}-lap", f"{LAB4}-obstacles"}
    assert boards[f"{LAB4}-lap"]["title"] == "Lab 4: Follow the Gap · Fastest clean lap"
    assert boards[f"{LAB4}-lap"]["short"] == "FTG" and boards[f"{LAB4}-obstacles"]["short"] == "FTG"
    assert boards[f"{LAB4}-lap"]["lab_title"] == "Lab 4: Follow the Gap"
    lab3 = next(e for e in index["labs"] if e["slug"] == SLUG)
    assert lab3["short"] == lab3["title"]          # no short: in the config -> the assignment's name
    assert boards[f"{LAB4}-lap"]["requirement"].startswith("full marks on everything but")
    lap_doc = json.loads((data_dir / f"{LAB4}-lap.json").read_text())
    obs_doc = json.loads((data_dir / f"{LAB4}-obstacles.json").read_text())
    assert [r["metric"] for r in lap_doc["rows"]] == [12.0, 20.0]      # fay first, no obstacles needed
    assert [r["metric"] for r in obs_doc["rows"]] == [40.0]            # only gus lapped the course
    assert lap_doc["reference"]["metric"] == 15.0 and obs_doc["reference"]["metric"] == 30.0
    assert (data_dir / f"{LAB4}.json").exists() is False
    # two sticky notes per student, one per board, and the wording carries the rule
    bodies = [c["body"] for c in api.comments[(fast, 1)]]
    assert len(bodies) == 2
    assert any("<!-- ese6150-leaderboard:lap -->" in b for b in bodies)
    assert any("<!-- ese6150-leaderboard:obstacles -->" in b and "Not on the board yet" in b for b in bodies)
    assert any("full marks on everything but the obstacle course" in b for b in bodies)
    # refund of an attempt reaches both boards
    fay_alias = next(a for a, p in lap_doc["players"].items())
    tag = api.tags_by_repo[fast][0]["tag_name"]
    assert build.refund(api, ORG, CLASSROOM, "salt", LAB4, "fay", tag, data_dir, unlock=False) == 0
    for name in (f"{LAB4}-lap.json", f"{LAB4}-obstacles.json"):
        doc = json.loads((data_dir / name).read_text())
        assert doc["players"][fay_alias]["submissions"][0]["refunded"] is True
