"""The sticky note in a student's Feedback PR: how it reads and how it is
kept up to date without spamming."""
import urllib.parse

MARKER = "<!-- ese6150-leaderboard -->"
ISSUE_TITLE = "Leaderboard alias"


def marker_for(key: str = "") -> str:
    """The sticky comment's marker: the plain one for an assignment's default
    board, a keyed one for each extra board (each board keeps its own note)."""
    return f"<!-- ese6150-leaderboard:{key} -->" if key else MARKER


def board_link(board_url: str, slug: str, alias: str) -> str:
    return f"{board_url.rstrip('/')}/?lab={urllib.parse.quote(slug)}&me={urllib.parse.quote(alias)}"


def fingerprint(row, used: int, cap: int, locked: bool, total_rows: int, reference) -> str:
    rank = row["rank"] if row else "-"
    best = row["metric"] if row else "-"
    ref = reference["metric"] if reference else "-"
    return f"{rank}/{total_rows}|{best}|{used}/{cap}|{int(locked)}|{ref}"


def format_value(value: float, unit: str) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".") if unit == "s" else f"{value:g}"
    return f"{text} {unit}".strip()


def render(lab: dict, alias: str, row, used: int, cap: int, locked: bool,
           total_rows: int, reference, board_url: str, generated_at: str) -> str:
    metric = lab["metric"]
    unit = metric["unit"]
    link = board_link(board_url, lab["slug"], alias)
    requirement = lab.get("requirement") or "the full autograded score"
    lines = [marker_for(lab.get("key", "")),
             f"### 🏁 {lab['title']} leaderboard",
             "",
             f"Your alias on the public board is **{alias}**. Nobody else can tell it is you.",
             "",
             f"[Open the board with your row highlighted]({link})",
             ""]
    if row:
        lines.append(f"- **Rank {row['rank']} of {total_rows}** with "
                     f"{format_value(row['metric'], unit)} on submission #{row['attempt']}.")
    else:
        lines.append(f"- Not on the board yet: it takes a submission with {requirement} "
                     "before the deadline. Your best one is kept once you have it.")
    if reference:
        lines.append(f"- TA reference solution: {format_value(reference['metric'], unit)}.")
    if not cap:
        lines.append(f"- Submissions used: **{used}**. There is no limit on this lab: "
                     "push the tag again whenever you improve.")
    elif locked:
        lines.append(f"- Submissions used: **{used} of {cap}**. The cap is spent, so this "
                     "repository is now read-only. Your grade is your latest graded submission.")
    else:
        left = max(cap - used, 0)
        lines.append(f"- Submissions used: **{used} of {cap}** ({left} left). "
                     "Once the cap is spent the repository becomes read-only.")
    lines += ["",
              f"_Only submissions with {requirement}, graded before the deadline, count, and "
              f"only your best one. Extra credit goes to the top {lab['podium']}. Updated {generated_at}._"]
    return "\n".join(lines)


def find_note(comments: list, author: str | None = None, marker: str = MARKER):
    """The id of the existing note among a PR's comments, or None."""
    for comment in comments:
        if marker in (comment.get("body") or ""):
            login = (comment.get("user") or {}).get("login")
            if author is None or login == author:
                return comment["id"]
    return None
