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
import shutil
import sys
from pathlib import Path

import yaml

from . import aliases, lock, maps, notes, replays, rules, teams
from .gh import GitHub, GitHubError

SCHEMA = "ese6150/leaderboard/v1"
# the course this board belongs to: repository variables (or the environment) override these
DEFAULT_ORG = os.environ.get("LEADERBOARD_ORG") or "RoboRacer-Class"
DEFAULT_CLASSROOM = os.environ.get("LEADERBOARD_CLASSROOM") or "ese-6150"
# nobody can submit to a lab much before it opens: an older student attempt is a previous term's
TERM_SLACK = dt.timedelta(days=14)
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

    def bytes(self, relpath: str) -> bytes:
        if self.local_dir is not None:
            return (self.local_dir / relpath).read_bytes()
        return self.api.file_bytes(f"{self.org}/{CONFIG_REPO}", relpath)


def load_labs(config: ConfigSource, classroom: str, only: set | None = None) -> list:
    """One board per `leaderboard:` block (or per entry of a `leaderboards:`
    list) in an assignment's grader config. A board is a lab entry with its
    own data file: `slug` is the assignment (repo prefix), `board` the file
    id (the slug, or slug-key for a keyed block), `ignore` the tests its
    `require:` rule excuses. The page shows one tab per assignment (its
    label is set in docs/index.html) with a switch between the assignment's
    boards; `lab_title` is the assignment's name for that tab."""
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
        # `mode: team` is the only mode whose repos are named `-group-<n>`;
        # legacy `group` repos still carry the founder's username, so they are
        # scanned (and kept anonymous) exactly like individual ones.
        team_mode = str(entry.get("mode") or "").strip().lower() == "team"
        blocks = cfg.get("leaderboards") or ([cfg["leaderboard"]] if cfg.get("leaderboard") else [])
        cap = (cfg.get("submissions") or {}).get("cap")
        name = entry.get("name") or slug
        tab = next((str(b["tab"]).strip() for b in blocks if b.get("tab")), "")
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
                "team_mode": team_mode,
                "key": key,
                "title": f"{name} · {board_title}" if len(blocks) > 1 else name,
                "board_title": board_title,
                "lab_title": name,
                "tab_title": tab,
                "due": entry.get("due"),
                "available_from": entry.get("available_from"),
                "cap": int(cap) if cap else None,
                "podium": int(block.get("podium", 5)),
                "metric": metric,
                "ignore": ignore,
                "requirement": (str(block.get("requirement")) if block.get("requirement")
                                else "the full autograded score"),
                # release asset holding this board's recorded run; unset = no replays
                "replay": str(block.get("replay") or "").strip(),
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


def best_attempt(lab: dict, key: str, subs: list):
    """The attempt the board shows for this entry: cap and deadline for a
    player, neither for the reference."""
    metric = lab["metric"]
    if key == "reference":
        best = rules.best_of(sorted(subs, key=lambda s: (s["at"], s["id"])), metric, None)
    else:
        due = rules.parse_time(lab["due"]) if lab.get("due") else None
        best = rules.best_of(subs[:lab["cap"] or 10**9], metric, due)
        if best is None and due is not None:
            # late only: the unranked row below the standings is still watchable
            best = rules.best_of(subs[:lab["cap"] or 10**9], metric, None)
    return best[1] if best else None


def replay_behind(lab: dict, key: str, subs: list, data_dir: Path | None):
    """The entry's best attempt when it has no recording and has not had its one
    extra look yet: it was graded before this builder read recordings (the
    grader shipped first), and a release is otherwise read only once."""
    if not lab.get("replay") or data_dir is None:
        return None
    best = best_attempt(lab, key, subs)
    if best is None or best.get("replay") or best.get("replay_checked"):
        return None
    return best


def capture_replay(api, rel: dict, lab: dict, key: str, record: dict, subs: list,
                   data_dir: Path | None, log: Log, config=None, classroom: str = "") -> None:
    """Publish the run recorded for this attempt when it is the entry's best so
    far, by the board's own rule (cap and deadline for a player, neither for the
    reference). A release is read once, so this is the only chance; a missing,
    oversized or refused recording costs the row its eye button, nothing more."""
    name = lab.get("replay")
    if not name or data_dir is None:
        return
    metric = lab["metric"]
    if best_attempt(lab, key, subs) is not record:
        return
    asset = next((a for a in rel.get("assets") or [] if a.get("name") == name), None)
    if asset is None or (asset.get("size") or 0) > replays.MAX_BYTES:
        return
    value = record["metrics"][metric.key]
    try:
        raw = api.download_asset(asset["url"])
        track = replays.map_of(raw)
        if config is not None and track and track not in replays.known_maps(data_dir):
            maps.ensure(config, classroom, lab["slug"], track, data_dir, log)     # a new lab's map: no manual step
        doc = replays.clean(raw, replays.known_maps(data_dir),
                            lap_seconds=value if metric.unit == "s" else None)
    except Exception as err:  # noqa: BLE001 - a replay never fails a build
        log(f"  {key}: recording unreadable ({type(err).__name__})")
        return
    if doc is None:
        log(f"  {key}: recording refused")
        return
    record["replay"] = replays.save(data_dir, lab["board"], key, doc)
    log(f"  {key}: recording published")


def backfill_fits(data_dir: Path, entry: dict, metric: rules.Metric, value) -> bool:
    """The staff-made recording names its ranked lap, timed to the row's value, as a
    recording read from a release must (replays.clean). The player races on that
    lap, so a re-run left on its own clock (they differ by tenths) would finish the
    car out of the board's order and show a time the board does not have. The
    backfill tool puts every re-run on the board's clock (replays.retime); a file
    that is not stays unlinked, and `tools/retime_replays.py` fixes it."""
    if metric.unit != "s":
        return True
    try:
        doc = json.loads((data_dir / entry["replay"].partition("?v=")[0]).read_text())
    except (OSError, ValueError):
        return False
    seconds = replays.ranked_seconds(doc) if isinstance(doc, dict) else None
    return seconds is not None and abs(seconds - value) <= replays.LAP_SLACK_S


def merge_backfill(data_dir: Path, lab: dict, state: dict, rows: list, reference) -> None:
    """Give an entry the recording staff made for it (replays.load_backfill) when it
    has none of its own, the recording is of the very attempt the board shows (a
    later, better run is never passed off with an older run's replay) and its
    ranked lap is timed to the row (backfill_fits)."""
    index = replays.load_backfill(data_dir, lab["board"])
    if not index:
        return
    for row in rows:
        entry = index.get(row["alias"])
        if entry is None or row.get("replay"):
            continue
        subs = rules.counted_submissions(state["players"][row["alias"]])
        if subs[row["attempt"] - 1]["id"] == entry["attempt"] \
                and backfill_fits(data_dir, entry, lab["metric"], row["metric"]):
            row["replay"] = entry["replay"]
    entry = index.get("reference")
    if entry is not None and reference is not None and not reference.get("replay"):
        best = best_attempt(lab, "reference", state["reference_submissions"])
        if best is not None and best["id"] == entry["attempt"] \
                and backfill_fits(data_dir, entry, lab["metric"], reference["metric"]):
            reference["replay"] = entry["replay"]


def term_start(lab: dict):
    """Student attempts older than this belong to an earlier term (None: no opening date)."""
    return rules.parse_time(lab["available_from"]) - TERM_SLACK if lab.get("available_from") else None


def archive_term(data_dir: Path, label: str, log: Log):
    """Move the boards, their index and their recordings to `archive/<label>/`, where the
    page still shows them (`?term=<label>`). Returns the folder's name, or None when
    there was nothing to move. A label is never overwritten."""
    data_dir = Path(data_dir)
    files = [p for p in data_dir.glob("*.json")]
    if not files:
        return None
    base = replays.file_name(label)
    name, n = base, 1
    while (data_dir / "archive" / name).exists():
        n += 1
        name = f"{base}-{n}"
    target = data_dir / "archive" / name
    target.mkdir(parents=True)
    index = data_dir / "index.json"
    classroom = json.loads(index.read_text()).get("classroom", "") if index.is_file() else ""
    for path in files:
        shutil.move(str(path), str(target / path.name))
    if (data_dir / "replays").is_dir():
        shutil.move(str(data_dir / "replays"), str(target / "replays"))
    listing = data_dir / "archive" / "index.json"
    terms = json.loads(listing.read_text()).get("terms", []) if listing.is_file() else []
    terms.append({"label": name, "title": label, "classroom": classroom, "archived_at": rules.iso(now_utc())})
    listing.write_text(json.dumps({"terms": terms}, indent=1) + "\n")
    log(f"archived the previous term as {name}")
    return name


def previous_term(data_dir: Path, classroom: str, labs: list):
    """A label when the published boards belong to an earlier term: another classroom,
    or student attempts from before a lab's current opening date."""
    index = Path(data_dir) / "index.json"
    if index.is_file():
        was = json.loads(index.read_text()).get("classroom")
        if was and was != classroom:
            return was
    for lab in labs:
        start, path = term_start(lab), Path(data_dir) / f"{lab['board']}.json"
        if start is None or not path.is_file():
            continue
        old = [s["at"] for p in load_state(path)["players"].values() for s in p["submissions"]
               if rules.parse_time(s["at"]) < start]
        if old:
            return f"{classroom}-{min(old)[:4]}"
    return None


def team_of(lab: dict, owner: str):
    """The team number a repo tail names on a team lab, else None. A staff
    test repo left over from before an assignment was flipped to team mode
    still carries a username, so it takes the alias path and stays redacted."""
    return teams.number(owner) if lab["team_mode"] else None


def scan_lab(api, org: str, classroom: str, lab: dict, repos: list, staff: set,
             salt: str, state: dict, log: Log, snapshot: dict | None = None,
             data_dir: Path | None = None, config=None) -> dict:
    """Merge every new attempt (a submit/* tag) and every newly graded one
    into `state`; returns board key -> repo tail for this run only (never
    written anywhere). The tail is what every side effect rebuilds the repo
    name from, so it is the username on an alias board and `group-<n>` on a
    team board."""
    prefix = f"{classroom}-{lab['slug']}-"
    players = state.setdefault("players", {})
    reference = state.setdefault("reference_submissions", [])
    owners: dict[str, str] = {}
    snapshot = snapshot or {}
    metric = lab["metric"]
    for name in sorted(r for r in repos if r.startswith(prefix)):
        username = name[len(prefix):]
        number = team_of(lab, username)
        if number is None:
            # A username: keep it and its repo out of the public Actions log,
            # and key the board off an alias.
            log.redact(username, name)
            key = aliases.resolve_alias(salt, username, players)
            owner_mark = aliases.owner_fingerprint(salt, username)
            is_staff = username.lower() in staff
        else:
            # A team number is public, so nothing here is hashed or redacted.
            # A team whose snapshot membership is entirely staff is the
            # reference row, never a competitor.
            key = teams.label(number)
            owner_mark = username
            is_staff = teams.is_staff_team(snapshot.get(lab["slug"], {}).get(number, []), staff)
        repo = f"{org}/{name}"
        if is_staff:
            label, target = "reference", reference
        else:
            player = players.setdefault(key, {
                "owner": owner_mark, "submissions": [], "locked": False})
            owners[key] = username
            label, target = key, player["submissions"]
        try:
            tags = api.tag_refs(repo)
        except GitHubError as err:
            log(f"  {label}: tags unreadable (HTTP {err.status}); skipped this run")
            continue
        start = term_start(lab)
        if start is not None and not is_staff:        # last term's repositories may still be in the org
            had = len(tags)
            tags = [t for t in tags if (rules.tag_time(t["name"]) or start) >= start]
            if had and not tags and not player["submissions"]:
                players.pop(key, None)                # every submission predates this term: not this term's entry
                owners.pop(key, None)
                continue
        known = {s["id"]: s for s in target}
        pending = [t for t in tags if rules.attempt_id(t["name"]) not in known
                   or not known[rules.attempt_id(t["name"])].get("graded")
                   or rules.glitched(known[rules.attempt_id(t["name"])], metric)]
        subs_now = (lambda: target) if is_staff else (lambda: rules.counted_submissions(player))
        if not pending and replay_behind(lab, label, subs_now(), data_dir) is None:
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
            capture_replay(api, rel, lab, label, record, subs_now(), data_dir, log, config, classroom)
        behind = replay_behind(lab, label, subs_now(), data_dir)
        if behind is not None:
            behind["replay_checked"] = True
            rel = next((r for t, r in releases.items() if rules.attempt_id(t) == behind["id"]), None)
            if rel is not None:
                capture_replay(api, rel, lab, label, behind, subs_now(), data_dir, log, config, classroom)
    return owners


# --- output -------------------------------------------------------------------------

def public_metric(metric: rules.Metric) -> dict:
    return {"key": metric.key, "label": metric.label, "unit": metric.unit,
            "direction": metric.direction, "extras": metric.extras}


def lab_document(lab: dict, state: dict, rows: list, unranked: list, reference, generated_at: str,
                 late: list = ()) -> dict:
    return {
        "schema": SCHEMA,
        "slug": lab["board"],
        "assignment": lab["slug"],
        "title": lab["title"],
        "board_title": lab["board_title"],
        "lab_title": lab["lab_title"],
        "requirement": lab["requirement"],
        "anonymous": not lab["team_mode"],
        "metric": public_metric(lab["metric"]),
        "due": lab["due"],
        "available_from": lab["available_from"],
        "cap": lab["cap"],
        "podium": lab["podium"],
        "generated_at": generated_at,
        "reference": reference,
        "rows": rows,
        "late": list(late),
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
    # A team board has no alias to hand out, so a note would only add a
    # comment (and a notification for the token's owner) to every team repo.
    if lab["team_mode"]:
        for player in state["players"].values():
            player.pop("note", None)
        return
    cap = lab["cap"] or 0
    by_alias = {r["alias"]: r for r in rows}
    public_lab = {"slug": lab["board"], "title": lab["title"], "podium": lab["podium"],
                  "metric": public_metric(lab["metric"]), "key": lab.get("key", ""),
                  "requirement": lab["requirement"], "anonymous": not lab["team_mode"]}
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
                    number = api.create_issue(repo, notes.issue_title(public_lab), body)
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
    label = previous_term(data_dir, classroom, labs)
    if label:
        archive_term(data_dir, label, log)
    repos = api.org_repos(org)
    # Only a team lab needs the membership snapshot, and only to spot a staff
    # team; skip the read entirely while every lab is alias-keyed.
    snapshot = teams.snapshot(config, classroom, log) if any(l["team_mode"] for l in labs) else {}
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
        owners = scan_lab(api, org, classroom, lab, repos, staff, salt, state, log, snapshot, data_dir, config)
        due = rules.parse_time(lab["due"]) if lab.get("due") else None
        apply_locks(api, org, classroom, lab, owners, state, token, script_text, log, dry_run)
        rows, unranked = rules.rank_players(state["players"], lab["metric"], lab["cap"] or 10**9, due)
        late, unranked = rules.late_rows(state["players"], lab["metric"], lab["cap"] or 10**9, due, unranked)
        reference = rules.best_reference(state["reference_submissions"], lab["metric"])
        merge_backfill(data_dir, lab, state, rows + late, reference)
        apply_notes(api, org, classroom, lab, owners, state, rows, reference, board_url,
                    generated_label, log, dry_run)
        changed = write_json(path, lab_document(lab, state, rows, unranked, reference, generated_at, late))
        lab_generated = generated_at if changed else json.loads(path.read_text()).get("generated_at", generated_at)
        index["labs"].append({
            "slug": lab["board"], "assignment": lab["slug"], "title": lab["title"],
            "board_title": lab["board_title"], "lab_title": lab["lab_title"], "tab_title": lab["tab_title"],
            "requirement": lab["requirement"], "anonymous": not lab["team_mode"],
            "due": lab["due"], "available_from": lab["available_from"], "cap": lab["cap"],
            "podium": lab["podium"], "metric": public_metric(lab["metric"]),
            "rows": len(rows), "late": len(late), "unranked": len(unranked), "reference": reference is not None,
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


def board_key(doc: dict, salt: str, who_arg: str) -> tuple:
    """`(players key, repo tail)` for the subject an ops command names.

    On a team board that is the team, written any of the ways a TA would
    reach for it (`7`, `group-7`, `Team 7`); on an alias board it is the
    student's GitHub username, whose alias is derived as always.
    """
    players = doc.get("players", {})
    if doc.get("anonymous", True):
        return aliases.resolve_alias(salt, who_arg, players), who_arg
    text = who_arg.strip()
    number = teams.number(text if text.lower().startswith("group-") else f"group-{text}")
    if number is None:
        match = re.fullmatch(r"(?i)team\s*([1-9][0-9]*)", text)
        number = int(match.group(1)) if match else None
    if number is None:
        raise ValueError(f"{who_arg!r} is not a team on this board; pass a team number "
                         "(7), a repo tail (group-7), or a label (\"Team 7\")")
    return teams.label(number), f"group-{number}"


def reveal(salt: str, slug: str, usernames: list, data_dir: Path) -> int:
    doc = json.loads((data_dir / f"{slug}.json").read_text())
    players = doc.get("players", {})
    by_alias = {r["alias"]: r for r in doc.get("rows", [])}
    for subject in usernames:
        try:
            alias, _ = board_key(doc, salt, subject)
        except ValueError as err:
            print(f"{subject}\t{err}")
            continue
        player = players.get(alias)
        if player is None:
            print(f"{subject}\t{alias}\t(no graded submission yet)")
            continue
        row = by_alias.get(alias)
        used = len(rules.counted_submissions(player))
        where = f"rank {row['rank']} with {row['metric']}" if row else "not on the board"
        print(f"{subject}\t{alias}\t{where}\tused {used}\tlocked {player.get('locked', False)}")
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
    try:
        alias, tail = board_key(doc, salt, username)
    except ValueError as err:
        print(err)
        return 1
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
    repo = f"{org}/{classroom}-{slug}-{tail}"
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
        if not lock.unlock_repo(api, org, classroom, slug, tail):
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
    if not doc.get("anonymous", True):
        # Nothing to de-anonymise: the rows already name the teams. Who is ON
        # each team lives in the classroom50 config repo, not here.
        print(f"{slug} is a team board — its rows are public team numbers, not aliases.\n"
              "For the members behind each team, use classroom50's "
              "ese-6150/scripts/leaderboard-status.py.")
        for row in sorted(doc.get("rows", []), key=lambda r: r["rank"]):
            print(f"{row['rank']}\t{row['alias']}\t{row['metric']}")
        return 0
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
    named = [r for r in sorted(repos) if not teams.is_team_repo(r)]
    if named:
        sample = f"{org}/{named[0]}"
        login = named[0].rsplit("-", 1)[-1]
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
    common.add_argument("--org", default=DEFAULT_ORG)
    common.add_argument("--classroom", default=DEFAULT_CLASSROOM)
    common.add_argument("--data-dir", default="docs/data")
    p = sub.add_parser("build", parents=[common], help="rebuild every lab board")
    p.add_argument("--labs", default="", help="comma-separated slugs to limit the run")
    p.add_argument("--dry-run", action="store_true", help="no notes, no locks; data is still written")
    p.add_argument("--config-dir", default="", help="local classroom50 checkout to read config from instead of the API")
    p.add_argument("--board-url", default=os.environ.get("BOARD_URL", "https://roboracer-class.github.io/leaderboard"))
    p = sub.add_parser("reveal", parents=[common],
                       help="which row a username holds (team board: a team number)")
    p.add_argument("slug")
    p.add_argument("usernames", nargs="+", metavar="subject")
    p = sub.add_parser("refund", parents=[common], help="give an attempt back and unlock")
    p.add_argument("slug")
    p.add_argument("username", metavar="subject",
                   help="GitHub username, or on a team board the team (7, group-7, \"Team 7\")")
    p.add_argument("tag")
    p.add_argument("--no-unlock", action="store_true")
    sub.add_parser("check-token", parents=[common], help="probe the token's permissions")
    p = sub.add_parser("new-term", parents=[common], help="archive the boards by hand (the rebuild does it by itself when the classroom or the dates change)")
    p.add_argument("label", help="what to call the term being archived, e.g. \"Fall 2026\"")
    p = sub.add_parser("who", parents=[common],
                       help="alias -> username for a roster (team board: prints the standings)")
    p.add_argument("slug")
    p.add_argument("--roster", required=True)
    args = parser.parse_args(argv)

    salt = os.environ.get("LEADERBOARD_SALT", "")
    if not salt and args.command != "check-token":
        print("LEADERBOARD_SALT is not set", file=sys.stderr)
        return 2
    data_dir = Path(args.data_dir)
    if args.command == "new-term":
        done = archive_term(Path(args.data_dir), args.label, Log())
        print(f"archived as {done}; commit docs/data and push" if done else "nothing to archive")
        return 0
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
