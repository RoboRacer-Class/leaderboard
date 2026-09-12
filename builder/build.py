"""Orchestration: read the class config, scan each lab's repos, rank, write
the public data, keep the per-student notes current, lock spent repos.

Everything printed goes to a public Actions log, so usernames and repo
names are redacted from every line."""
import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import yaml

from . import aliases, lock, notes, rules
from .gh import GitHub, GitHubError

SCHEMA = "ese6150/leaderboard/v1"
STAFF_TEAMS = ("teacher", "hta", "ta")
CONFIG_REPO = "classroom50"


class Log:
    """print() that scrubs every username and repo name it has been told about."""

    def __init__(self, stream=None):
        self.words: set[str] = set()
        self.stream = stream or sys.stdout

    def redact(self, *words: str) -> None:
        self.words.update(w for w in words if w)

    def scrub(self, text: str) -> str:
        for word in sorted(self.words, key=len, reverse=True):
            text = re.sub(re.escape(word), "<redacted>", text, flags=re.IGNORECASE)
        return text

    def __call__(self, message: str) -> None:
        print(self.scrub(message), file=self.stream, flush=True)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --- configuration ------------------------------------------------------------

class ConfigSource:
    """Class configuration files: read from the private class repo through
    the API, or from a local checkout of it (--config-dir) for offline runs."""

    def __init__(self, api, org: str, local_dir: Path | None = None):
        self.api, self.org, self.local_dir = api, org, local_dir

    def text(self, relpath: str) -> str:
        if self.local_dir is not None:
            path = self.local_dir / relpath
            if not path.is_file():
                raise GitHubError(404, relpath, "no such local file")
            return path.read_text()
        return self.api.file_text(f"{self.org}/{CONFIG_REPO}", relpath)


def load_labs(config: ConfigSource, classroom: str, only: set | None = None) -> list:
    """One board per `leaderboard:` block (or per entry of a `leaderboards:`
    list) in an assignment's grader config. A board is a lab entry with its
    own data file and tab: `slug` is the assignment (repo prefix), `board`
    the file/tab id (the slug, or slug-key for a keyed block), `ignore` the
    tests its `require:` rule excuses."""
    raw = json.loads(config.text(f"{classroom}/assignments.json"))
    entries = raw if isinstance(raw, list) else raw.get("assignments", [])
    labs = []
    for entry in entries:
        slug = entry.get("slug")
        if not slug:
            continue
        try:
            text = config.text(f"{classroom}/autograders/{slug}/config.yaml")
        except GitHubError as err:
            if err.status == 404:
                continue
            raise
        cfg = yaml.safe_load(text) or {}
        blocks = cfg.get("leaderboards") or ([cfg["leaderboard"]] if cfg.get("leaderboard") else [])
        cap = (cfg.get("submissions") or {}).get("cap")
        name = entry.get("name") or slug
        for block in blocks:
            key = str(block.get("key") or "").strip()
            board = f"{slug}-{key}" if key else slug
            if only and slug not in only and board not in only:
                continue
            metric = rules.metric_from_config(block)
            ignore = rules.ignored_tests(block)
            board_title = block.get("title") or metric.label
            labs.append({
                "slug": slug,
                "board": board,
                "key": key,
                "title": f"{name} · {board_title}" if len(blocks) > 1 else name,
                "board_title": board_title,
                "due": entry.get("due"),
                "available_from": entry.get("available_from"),
                "cap": int(cap) if cap else None,
                "podium": int(block.get("podium", 5)),
                "metric": metric,
                "ignore": ignore,
                "requirement": (str(block.get("requirement")) if block.get("requirement")
                                else "the full autograded score"),
            })
    return labs


def load_staff(api, org: str, classroom: str) -> set:
    staff = set()
    for team in STAFF_TEAMS:
        staff.update(m.lower() for m in api.team_members(org, f"classroom50-{classroom}-{team}"))
    return staff


def load_lock_script(config: ConfigSource, classroom: str, log: Log):
    try:
        return config.text(f"{classroom}/scripts/lab-access.sh")
    except GitHubError as err:
        log(f"lab-access.sh not readable (HTTP {err.status}); locks fall back to the API")
        return None


# --- scanning --------------------------------------------------------------------

def submission_time(sha: str, runs: list, fallback: str) -> str:
    """When the student pushed: the creation time of the Actions run that
    graded this commit (server-side, survives regrades). Falls back to the
    given time."""
    times = [run["created_at"] for run in runs
             if sha and str(run.get("head_sha", "")).startswith(sha) and run.get("created_at")]
    if times:
        return rules.iso(rules.parse_time(min(times)))
    return rules.iso(rules.parse_time(fallback))


def graded_releases(releases: list) -> dict:
    """tag -> release, for releases that carry a result.json."""
    out = {}
    for rel in releases:
        tag = str(rel.get("tag_name", ""))
        if tag.startswith("submit/") and any(a.get("name") == "result.json" for a in rel.get("assets") or []):
            out[tag] = rel
    return out


def read_result(api, rel: dict) -> dict:
    asset = next(a for a in rel["assets"] if a["name"] == "result.json")
    return json.loads(api.download_asset(asset["url"]))


def scan_lab(api, org: str, classroom: str, lab: dict, repos: list, staff: set,
             salt: str, state: dict, log: Log) -> dict:
    """Merge every new attempt (a submit/* tag) and every newly graded one
    into `state`; returns alias -> username for this run only (never
    written anywhere)."""
    prefix = f"{classroom}-{lab['slug']}-"
    players = state.setdefault("players", {})
    reference = state.setdefault("reference_submissions", [])
    owners: dict[str, str] = {}
    metric = lab["metric"]
    for name in sorted(r for r in repos if r.startswith(prefix)):
        username = name[len(prefix):]
        log.redact(username, name)
        repo = f"{org}/{name}"
        is_staff = username.lower() in staff
        if is_staff:
            label, target = "reference", reference
        else:
            alias = aliases.resolve_alias(salt, username, players)
            player = players.setdefault(alias, {
                "owner": aliases.owner_fingerprint(salt, username), "submissions": [], "locked": False})
            owners[alias] = username
            label, target = alias, player["submissions"]
        try:
            tags = api.tag_refs(repo)
        except GitHubError as err:
            log(f"  {label}: tags unreadable (HTTP {err.status}); skipped this run")
            continue
        known = {s["id"]: s for s in target}
        pending = [t for t in tags if rules.attempt_id(t["name"]) not in known
                   or not known[rules.attempt_id(t["name"])].get("graded")]
        if not pending:
            continue
        try:
            releases = graded_releases(api.releases(repo))
        except GitHubError as err:
            log(f"  {label}: releases unreadable (HTTP {err.status}); attempts still counted")
            releases = {}
        runs = []
        try:
            runs = api.workflow_runs(repo)
        except GitHubError:
            pass
        now = rules.iso(now_utc())
        for tag in sorted(pending, key=lambda t: t["name"]):
            tid = rules.attempt_id(tag["name"])
            record = known.get(tid)
            if record is None:
                record = {"id": tid, "at": submission_time(tag["sha"], runs, now),
                          "graded": False, "full": False, "metrics": None}
                target.append(record)
                known[tid] = record
                log(f"  {label}: new attempt")
            rel = releases.get(tag["name"])
            if rel is None:
                continue
            try:
                result = read_result(api, rel)
            except Exception as err:  # noqa: BLE001 - retried next run
                log(f"  {label}: could not read one result ({type(err).__name__}); retry next run")
                continue
            if not isinstance(result.get("max-score"), int) or result.get("max-score", 0) <= 0:
                continue      # a synthetic/vacuous result, not a graded run
            record.update({"graded": True, "full": rules.full_score(result, lab.get("ignore", ())),
                           "metrics": rules.extract_metrics(result, metric),
                           "at": submission_time(tag["sha"], runs, record["at"])})
            value = (record.get("metrics") or {}).get(metric.key)
            log(f"  {label}: graded ({'full score' if record['full'] else 'not full score'}"
                + (f", {metric.key}={value}" if value is not None else "") + ")")
    return owners


# --- output -------------------------------------------------------------------------

def public_metric(metric: rules.Metric) -> dict:
    return {"key": metric.key, "label": metric.label, "unit": metric.unit,
            "direction": metric.direction, "extras": metric.extras}


def lab_document(lab: dict, state: dict, rows: list, unranked: list, reference, generated_at: str) -> dict:
    return {
        "schema": SCHEMA,
        "slug": lab["board"],
        "assignment": lab["slug"],
        "title": lab["title"],
        "board_title": lab["board_title"],
        "requirement": lab["requirement"],
        "metric": public_metric(lab["metric"]),
        "due": lab["due"],
        "available_from": lab["available_from"],
        "cap": lab["cap"],
        "podium": lab["podium"],
        "generated_at": generated_at,
        "reference": reference,
        "rows": rows,
        "unranked": unranked,
        "players": state.get("players", {}),
        "reference_submissions": state.get("reference_submissions", []),
    }


def load_state(path: Path) -> dict:
    if not path.is_file():
        return {"players": {}, "reference_submissions": []}
    data = json.loads(path.read_text())
    return {"players": data.get("players", {}),
            "reference_submissions": data.get("reference_submissions", [])}


def write_json(path: Path, data: dict, volatile: tuple = ("generated_at",)) -> bool:
    """Write `data` unless it differs from the file only in the volatile keys
    (the timestamp), so an unchanged board is not committed every run.
    Returns True when the file was written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            old = json.loads(path.read_text())
            strip = lambda d: {k: v for k, v in d.items() if k not in volatile}
            if strip(old) == strip(data):
                return False
        except (ValueError, OSError):
            pass
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    return True


# --- side effects -----------------------------------------------------------------

def apply_locks(api, org, classroom, lab, owners, state, token, script_text, log, dry_run):
    cap = lab["cap"]
    if not cap:
        return
    for alias, username in owners.items():
        player = state["players"][alias]
        used = len(rules.counted_submissions(player))
        if used < cap or player.get("locked"):
            continue
        if dry_run:
            log(f"  {alias}: would lock ({used}/{cap})")
            continue
        ok, how = lock.lock_repo(api, org, classroom, lab["slug"], username, token, script_text)
        if ok:
            player["locked"] = True
            player["locked_at"] = rules.iso(now_utc())
            log(f"  {alias}: locked via {how} ({used}/{cap})")
        else:
            log(f"  {alias}: lock FAILED via {how}; retrying next run")


def apply_notes(api, org, classroom, lab, owners, state, rows, reference, board_url,
                generated_label, log, dry_run):
    cap = lab["cap"] or 0
    by_alias = {r["alias"]: r for r in rows}
    public_lab = {"slug": lab["board"], "title": lab["title"], "podium": lab["podium"],
                  "metric": public_metric(lab["metric"]), "key": lab.get("key", ""),
                  "requirement": lab["requirement"]}
    marker = notes.marker_for(lab.get("key", ""))
    for alias, username in owners.items():
        player = state["players"][alias]
        row = by_alias.get(alias)
        used = len(rules.counted_submissions(player))
        locked = bool(player.get("locked"))
        fp = notes.fingerprint(row, used, cap, locked, len(rows), reference)
        note = player.setdefault("note", {})
        if note.get("fp") == fp:
            continue
        body = notes.render(public_lab, alias, row, used, cap, locked, len(rows), reference,
                            board_url, generated_label)
        if dry_run:
            log(f"  {alias}: would update note")
            continue
        repo = f"{org}/{classroom}-{lab['slug']}-{username}"
        try:
            if note.get("kind") == "issue" and note.get("number"):
                api.update_issue(repo, note["number"], body)
            else:
                number = note.get("number") or api.feedback_pr(repo)
                if number is None:
                    number = api.create_issue(repo, notes.ISSUE_TITLE, body)
                    note.update({"kind": "issue", "number": number, "comment_id": None})
                else:
                    comment_id = note.get("comment_id") or notes.find_note(api.issue_comments(repo, number), marker=marker)
                    if comment_id:
                        api.update_comment(repo, comment_id, body)
                    else:
                        comment_id = api.create_comment(repo, number, body)
                    note.update({"kind": "pr", "number": number, "comment_id": comment_id})
            note["fp"] = fp
            note["updated_at"] = rules.iso(now_utc())
            log(f"  {alias}: note updated")
        except GitHubError as err:
            log(f"  {alias}: note not updated (HTTP {err.status}); retrying next run")


# --- commands ---------------------------------------------------------------------

def build(api, org: str, classroom: str, salt: str, token: str, data_dir: Path, board_url: str,
          only: set | None, dry_run: bool, log: Log, config_dir: Path | None = None) -> int:
    config = ConfigSource(api, org, config_dir)
    labs = load_labs(config, classroom, only)
    if not labs:
        log("no assignment has a leaderboard block; nothing to do")
        return 0
    staff = load_staff(api, org, classroom)
    for login in staff:
        log.redact(login)
    if not staff:
        log("staff teams are empty or unreadable; refusing to run (staff repos would look like students)")
        return 1
    repos = api.org_repos(org)
    script_text = load_lock_script(config, classroom, log)
    generated = now_utc()
    generated_at = rules.iso(generated)
    generated_label = generated.strftime("%Y-%m-%d %H:%M UTC")
    index = {"schema": SCHEMA, "generated_at": generated_at, "org": org, "classroom": classroom,
             "labs": []}
    for lab in labs:
        log(f"{lab['board']}: scanning")
        path = data_dir / f"{lab['board']}.json"
        state = load_state(path)
        owners = scan_lab(api, org, classroom, lab, repos, staff, salt, state, log)
        due = rules.parse_time(lab["due"]) if lab.get("due") else None
        apply_locks(api, org, classroom, lab, owners, state, token, script_text, log, dry_run)
        rows, unranked = rules.rank_players(state["players"], lab["metric"], lab["cap"] or 10**9, due)
        reference = rules.best_reference(state["reference_submissions"], lab["metric"])
        apply_notes(api, org, classroom, lab, owners, state, rows, reference, board_url,
                    generated_label, log, dry_run)
        changed = write_json(path, lab_document(lab, state, rows, unranked, reference, generated_at))
        lab_generated = generated_at if changed else json.loads(path.read_text()).get("generated_at", generated_at)
        index["labs"].append({
            "slug": lab["board"], "assignment": lab["slug"], "title": lab["title"],
            "board_title": lab["board_title"], "requirement": lab["requirement"],
            "due": lab["due"], "available_from": lab["available_from"], "cap": lab["cap"],
            "podium": lab["podium"], "metric": public_metric(lab["metric"]),
            "rows": len(rows), "unranked": len(unranked), "reference": reference is not None,
            "file": f"{lab['board']}.json", "generated_at": lab_generated})
        log(f"{lab['board']}: {len(rows)} ranked, {len(unranked)} waiting, "
            f"reference {'set' if reference else 'missing'}")
    # Demo labs (index entries flagged "demo": true, with their own data
    # file) are kept until someone deletes them by hand.
    old_index = data_dir / "index.json"
    if old_index.is_file():
        try:
            for entry in json.loads(old_index.read_text()).get("labs", []):
                if entry.get("demo") and (data_dir / entry.get("file", "")).is_file():
                    index["labs"].append(entry)
        except (ValueError, OSError):
            pass
    write_json(data_dir / "index.json", index)
    return 0


def reveal(salt: str, slug: str, usernames: list, data_dir: Path) -> int:
    doc = json.loads((data_dir / f"{slug}.json").read_text())
    players = doc.get("players", {})
    by_alias = {r["alias"]: r for r in doc.get("rows", [])}
    for username in usernames:
        alias = aliases.resolve_alias(salt, username, players)
        player = players.get(alias)
        if player is None:
            print(f"{username}\t{alias}\t(no graded submission yet)")
            continue
        row = by_alias.get(alias)
        used = len(rules.counted_submissions(player))
        where = f"rank {row['rank']} with {row['metric']}" if row else "not on the board"
        print(f"{username}\t{alias}\t{where}\tused {used}\tlocked {player.get('locked', False)}")
    return 0


def board_files(data_dir: Path, slug: str) -> list:
    """The data files of every board of an assignment: its own slug and any
    keyed board whose document names it as its assignment."""
    files = []
    for path in sorted(data_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            doc = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if path.stem == slug or doc.get("assignment") == slug:
            files.append(path)
    return files


def refund(api, org: str, classroom: str, salt: str, slug: str, username: str, tag: str,
           data_dir: Path, unlock: bool) -> int:
    paths = board_files(data_dir, slug) or [data_dir / f"{slug}.json"]
    docs = [(p, json.loads(p.read_text())) for p in paths]
    path, doc = docs[0]
    alias = aliases.resolve_alias(salt, username, doc.get("players", {}))
    player = doc["players"].get(alias)
    if player is None:
        print(f"{username} has no submissions in {slug}")
        return 1
    tid = rules.attempt_id(tag)
    hits = [s for s in player["submissions"] if s["id"] == tid]
    if not hits:
        print(f"{username} has no attempt tagged {tag} ({len(player['submissions'])} attempts on record)")
        return 1
    hits[0]["refunded"] = True
    for other_path, other in docs[1:]:   # the same attempt on the assignment's other boards
        other_player = other.get("players", {}).get(alias)
        for sub in (other_player or {}).get("submissions", []):
            if sub["id"] == tid:
                sub["refunded"] = True
        if other_player:
            other_player.setdefault("note", {}).pop("fp", None)
        other_path.write_text(json.dumps(other, indent=1, sort_keys=True) + "\n")
    repo = f"{org}/{classroom}-{slug}-{username}"
    if hits[0].get("graded"):
        print(f"note: {tag} was graded, so its tag and release stay; the grader's own count still "
              "includes it until the tag is deleted by hand")
    else:
        try:
            api.delete_tag(repo, tag)
            print(f"deleted the tag {tag} so the grader stops counting it")
        except Exception as err:  # noqa: BLE001
            print(f"could not delete the tag {tag} ({err}); delete it by hand")
    if unlock:
        if not lock.unlock_repo(api, org, classroom, slug, username):
            print("unlock did not take; check the collaborator permission by hand")
            return 1
        player["locked"] = False
        player.pop("locked_at", None)
    player.setdefault("note", {}).pop("fp", None)   # forces a fresh note on the next build
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"refunded {tag} for {username} ({alias}); commit "
          f"{', '.join('data/' + p.name for p, _ in docs)} and rebuild")
    return 0


def who(salt: str, slug: str, roster: Path, data_dir: Path) -> int:
    doc = json.loads((data_dir / f"{slug}.json").read_text())
    players = doc.get("players", {})
    by_alias = {r["alias"]: r for r in doc.get("rows", [])}
    with roster.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for entry in rows:
        username = (entry.get("username") or "").strip()
        if not username:
            continue
        alias = aliases.resolve_alias(salt, username, players)
        row = by_alias.get(alias)
        out.append((row["rank"] if row else 10**9, alias, username, row["metric"] if row else ""))
    for rank, alias, username, metric in sorted(out):
        print(f"{'' if rank == 10**9 else rank}\t{alias}\t{username}\t{metric}")
    return 0


def check_token(api, org: str, classroom: str) -> int:
    """Try each call the builder makes and name the permission behind any
    failure. Returns 0 when everything works."""
    config_repo = f"{org}/{CONFIG_REPO}"
    checks = [
        ("Contents: Read (class config)", lambda: api.file_text(config_repo, f"{classroom}/assignments.json")),
        ("Organization Members: Read (staff teams)", lambda: api.team_members(org, f"classroom50-{classroom}-teacher")),
        ("Metadata: Read (repo listing)", lambda: api.org_repos(org)),
    ]
    prefix = f"{classroom}-"
    repos = []
    try:
        repos = [r for r in api.org_repos(org) if r.startswith(prefix + "lab-")]
    except GitHubError:
        pass
    if repos:
        sample = f"{org}/{sorted(repos)[0]}"
        login = sorted(repos)[0].rsplit("-", 1)[-1]
        checks += [
            ("Contents: Read (tags and releases)", lambda: api.tag_refs(sample)),
            ("Actions: Read (run times)", lambda: api.workflow_runs(sample)),
            ("Pull requests: Read (Feedback PR)", lambda: api.feedback_pr(sample)),
            ("Administration: Read (collaborator permission)", lambda: api.permission(sample, login)),
        ]
    failed = 0
    for label, call in checks:
        try:
            call()
            print(f"  ok   {label}")
        except GitHubError as err:
            failed += 1
            print(f"  FAIL {label}: HTTP {err.status}")
    if failed:
        print(f"{failed} permission(s) missing; edit the token at "
              "https://github.com/settings/personal-access-tokens and try again")
        return 1
    print("write permissions (Pull requests, Issues, Administration) cannot be probed without "
          "writing; they are used on the first student note and lock")
    return 0


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(prog="builder")
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--org", default=os.environ.get("LEADERBOARD_ORG", "RoboRacer-Class"))
    common.add_argument("--classroom", default=os.environ.get("LEADERBOARD_CLASSROOM", "ese-6150"))
    common.add_argument("--data-dir", default="docs/data")
    p = sub.add_parser("build", parents=[common], help="rebuild every lab board")
    p.add_argument("--labs", default="", help="comma-separated slugs to limit the run")
    p.add_argument("--dry-run", action="store_true", help="no notes, no locks; data is still written")
    p.add_argument("--config-dir", default="", help="local classroom50 checkout to read config from instead of the API")
    p.add_argument("--board-url", default=os.environ.get("BOARD_URL", "https://roboracer-class.github.io/leaderboard"))
    p = sub.add_parser("reveal", parents=[common], help="which alias a username holds")
    p.add_argument("slug")
    p.add_argument("usernames", nargs="+")
    p = sub.add_parser("refund", parents=[common], help="give an attempt back and unlock")
    p.add_argument("slug")
    p.add_argument("username")
    p.add_argument("tag")
    p.add_argument("--no-unlock", action="store_true")
    sub.add_parser("check-token", parents=[common], help="probe the token's permissions")
    p = sub.add_parser("who", parents=[common], help="alias -> username for a roster")
    p.add_argument("slug")
    p.add_argument("--roster", required=True)
    args = parser.parse_args(argv)

    salt = os.environ.get("LEADERBOARD_SALT", "")
    if not salt and args.command != "check-token":
        print("LEADERBOARD_SALT is not set", file=sys.stderr)
        return 2
    data_dir = Path(args.data_dir)
    if args.command == "reveal":
        return reveal(salt, args.slug, args.usernames, data_dir)
    if args.command == "who":
        return who(salt, args.slug, Path(args.roster), data_dir)

    token = os.environ.get("LEADERBOARD_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token:
        print("LEADERBOARD_TOKEN is not set (the repo secret holding the fine-grained PAT)",
              file=sys.stderr)
        return 2
    api = GitHub(token)
    log = Log()
    log.redact(token, salt)
    if args.command == "check-token":
        return check_token(api, args.org, args.classroom)
    if args.command == "refund":
        return refund(api, args.org, args.classroom, salt, args.slug, args.username, args.tag,
                      data_dir, unlock=not args.no_unlock)
    only = {s.strip() for s in args.labs.split(",") if s.strip()} or None
    try:
        rc = build(api, args.org, args.classroom, salt, token, data_dir, args.board_url,
                   only, args.dry_run, log, Path(args.config_dir) if args.config_dir else None)
    except Exception as err:  # noqa: BLE001 - keep names out of the public log
        log(f"build failed: {type(err).__name__}: {err}")
        return 1
    log(f"done in {api.calls} API calls")
    return rc
