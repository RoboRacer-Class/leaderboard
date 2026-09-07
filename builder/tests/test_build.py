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
    """results: list of (score, max, detail); times: list of ISO push times."""
    name = f"{CLASSROOM}-{SLUG}-{username}"
    api.repos.append(name)
    repo = f"{ORG}/{name}"
    rels, runs = [], []
    for i, ((score, mx, detail), at) in enumerate(zip(results, times)):
        sha = f"{i:07x}"
        tag = f"submit/2026-09-1{i}T00-00-00Z-{sha}"
        url = f"https://api.example/{name}/{i}"
        api.assets[url] = json.dumps({"score": score, "max-score": mx, "tests": [
            {"test-name": "C1 drives one lap", "passed": score == mx, "score": 40, "max-score": 40, "detail": detail}]}).encode()
        rels.append({"tag_name": tag, "created_at": "2026-09-30T00:00:00Z",   # regraded later: must not matter
                     "assets": [{"name": "result.json", "url": url}]})
        runs.append({"head_sha": sha + "0" * 33, "created_at": at, "name": "Autograde"})
    api.releases_by_repo[repo] = rels
    api.runs_by_repo[repo] = runs
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
    for name in ("alice", "bob", "carol", "dave", "cedrichld", "rahulmangharam"):
        assert name not in text.lower(), f"{name} leaked"
    assert [lab["slug"] for lab in index["labs"]] == [SLUG]
    assert doc["reference"]["metric"] == 13.47 and doc["cap"] == 5 and doc["podium"] == 5
    ranked = [(r["rank"], r["metric"], r["attempt"], r["used"], r["locked"]) for r in doc["rows"]]
    assert ranked == [(1, 13.0, 1, 1, False), (2, 13.2, 5, 5, True), (3, 14.2, 2, 2, False)]
    assert [(u["used"]) for u in doc["unranked"]] == [1]          # carol: late, still spent one
    assert len(doc["players"]) == 4
    # bob hit the cap: repo is read-only, via the API fallback (no script in this world)
    assert api.perms[(f"{ORG}/{CLASSROOM}-{SLUG}-bob", "bob")] == "read"
    assert "locked via api (5/5)" in stream.getvalue()
    # notes: one PR comment each for alice/bob/carol, an issue for dave, nothing for staff
    bodies = [c["body"] for lst in api.comments.values() for c in lst]
    assert len(bodies) == 3 and all("Your alias on the public board is" in b for b in bodies)
    assert len(api.issues) == 1
    assert not any("cedrichld" in k[0] for k in api.comments) and not any("cedrichld" in k[0] for k in api.issues)
    bob_alias = next(r["alias"] for r in doc["rows"] if r["rank"] == 2)
    assert any(f"**{bob_alias}**" in b and "5 of 5" in b and "read-only" in b for b in bodies)


def test_second_build_is_idempotent_and_survives_deleted_releases(world):
    api, data_dir, log, stream = world
    run_build(api, data_dir, log)
    first = (data_dir / f"{SLUG}.json").read_text()
    comments_before = repr(sorted(api.comments.items()))
    # bob deletes his releases to reset the count: the state remembers them
    api.releases_by_repo[f"{ORG}/{CLASSROOM}-{SLUG}-bob"] = []
    run_build(api, data_dir, log)
    second = json.loads((data_dir / f"{SLUG}.json").read_text())
    assert json.loads(first)["rows"] == second["rows"]
    assert repr(sorted(api.comments.items())) == comments_before
    assert "new submission" not in stream.getvalue().split("scanning", 2)[-1]


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
    tag = doc["players"][bob_alias]["submissions"][0]["tag"]
    assert build.refund(api, ORG, CLASSROOM, "salt", SLUG, "bob", tag, data_dir, unlock=True) == 0
    assert api.perms[(f"{ORG}/{CLASSROOM}-{SLUG}-bob", "bob")] == "write"
    run_build(api, data_dir, log)
    doc = json.loads((data_dir / f"{SLUG}.json").read_text())
    bob = next(r for r in doc["rows"] if r["alias"] == bob_alias)
    assert bob["used"] == 4 and bob["locked"] is False and bob["metric"] == 13.2


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
    rel = {"tag_name": "submit/2026-09-07T03-46-22Z-9bb0e0a", "created_at": "2026-09-20T00:00:00Z"}
    runs = [{"head_sha": "9bb0e0a" + "f" * 33, "created_at": "2026-09-07T03:46:01Z"},
            {"head_sha": "9bb0e0a" + "f" * 33, "created_at": "2026-09-09T00:00:00Z"}]
    assert build.submission_time(rel, runs) == "2026-09-07T03:46:01Z"
    assert build.submission_time(rel, []) == "2026-09-20T00:00:00Z"


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
