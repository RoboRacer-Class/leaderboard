"""Team identity for a `mode: team` assignment.

Classroom 50 names a team assignment's shared repo `<classroom>-<slug>-group-<n>`,
so the repo tail carries the team number instead of a username. That number IS
the board's public key: a team board is ranked by `Team <n>`, with no racing
alias and nothing to keep anonymous.

The only thing this module needs from the private config repo is
`<classroom>/teams.json` (the teacher's membership snapshot), and only to tell
a staff team's repo from a student team's — a staff team is the reference row,
never a competitor.
"""
import json
import re

TAIL = re.compile(r"^group-([1-9][0-9]*)$")
SLUG_TAIL = re.compile(r"^classroom50-group-[0-9a-f]{16}-([1-9][0-9]*)$")
REPO_TAIL = re.compile(r"-group-[1-9][0-9]*$")


def number(owner: str):
    """The team number behind a repo tail (`group-3` -> 3), or None when the
    tail is a username. Mirrors collect_scores.py's team_repo_counter:
    counters start at 1 and carry no leading zeros."""
    match = TAIL.match(owner.strip().lower())
    return int(match.group(1)) if match else None


def label(n: int) -> str:
    """The public board key for a team. Deliberately derived from the number
    alone, not from the team's display name in teams.json: the key is what a
    player's whole attempt history hangs off, so it must not move when someone
    renames a team between labs."""
    return f"Team {n}"


def is_team_repo(name: str) -> bool:
    """Whether a repo name ends in a team tail. For the places that hold only
    a repo name and not the assignment's mode."""
    return REPO_TAIL.search(name.strip().lower()) is not None


def snapshot(config, classroom: str, log) -> dict:
    """`{assignment slug: {team number: [member logins]}}` from the config
    repo's teams.json.

    A missing or malformed file is not fatal — it only means no team can be
    recognised as staff, which costs a reference row rather than mis-ranking a
    student team.
    """
    try:
        raw = json.loads(config.text(f"{classroom}/teams.json"))
    except Exception as err:  # noqa: BLE001 - any read/parse failure degrades the same way
        log(f"teams.json not readable ({type(err).__name__}); "
            "no team can be recognised as a staff team this run")
        return {}
    out: dict[str, dict[int, list]] = {}
    for slug, bucket in (raw.get("assignments") or {}).items():
        teams: dict[int, list] = {}
        for team in (bucket or {}).get("teams") or []:
            match = SLUG_TAIL.match(str(team.get("slug", "")))
            if not match:
                continue
            teams[int(match.group(1))] = [str(m).lower() for m in team.get("members") or []]
        out[slug] = teams
    return out


def is_staff_team(members: list, staff: set) -> bool:
    """A team whose every recorded member is staff is the reference, not a
    competitor. An empty membership list is NOT staff: an unpopulated or
    drifted snapshot must never silently drop a student team off the board."""
    return bool(members) and all(m.lower() in staff for m in members)
