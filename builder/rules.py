"""The board rules as pure functions: what a metric is, which submissions
qualify, and how the rows are ranked. No network, no files."""
import datetime as dt
import re
from dataclasses import dataclass, field


@dataclass
class Metric:
    key: str                 # named regex group used for ranking, e.g. lap_s
    label: str               # column header
    unit: str
    direction: str           # 'lower' or 'higher' is better
    test: str                # result.json test id whose detail carries it, e.g. C1
    pattern: str             # regex with named groups
    extras: list = field(default_factory=list)   # [{key, label, unit}] extra columns

    def better(self, a: float, b: float) -> bool:
        return a < b if self.direction == "lower" else a > b

    def sort_key(self, value: float) -> float:
        return value if self.direction == "lower" else -value


def metric_from_config(block: dict) -> Metric:
    """Build a Metric from a grader config.yaml `leaderboard:` block."""
    for required in ("test", "pattern", "metric"):
        if not block.get(required):
            raise ValueError(f"leaderboard block is missing '{required}'")
    if f"(?P<{block['metric']}>" not in block["pattern"]:
        raise ValueError(f"pattern has no named group '{block['metric']}'")
    direction = str(block.get("direction", "lower")).lower()
    if direction not in ("lower", "higher"):
        raise ValueError("direction must be 'lower' or 'higher'")
    extras = []
    for extra in block.get("extras") or []:
        extras.append({"key": extra["key"], "label": extra.get("label", extra["key"]),
                       "unit": extra.get("unit", "")})
    return Metric(key=block["metric"], label=block.get("label", block["metric"]),
                  unit=block.get("unit", ""), direction=direction, test=str(block["test"]),
                  pattern=block["pattern"], extras=extras)


def full_score(result: dict) -> bool:
    score, max_score = result.get("score"), result.get("max-score")
    return (isinstance(score, int) and isinstance(max_score, int)
            and not isinstance(score, bool) and max_score > 0 and score == max_score)


def extract_metrics(result: dict, metric: Metric) -> dict | None:
    """Named-group values from the metric test's detail string, or None when
    the test is absent or the detail does not carry the metric."""
    for test in result.get("tests") or []:
        name = str(test.get("test-name", ""))
        if name == metric.test or name.startswith(metric.test + " "):
            match = re.search(metric.pattern, str(test.get("detail", "")))
            if not match:
                return None
            values = {k: float(v) for k, v in match.groupdict().items() if v is not None}
            return values if metric.key in values else None
    return None


def parse_time(value: str) -> dt.datetime:
    value = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def counted_submissions(player: dict) -> list:
    """The player's non-refunded submissions in grading order."""
    subs = [s for s in player.get("submissions", []) if not (s.get("refunded") or s.get("ignored"))]
    subs.sort(key=lambda s: (s["at"], s["tag"]))
    return subs


def qualifies(sub: dict, metric: Metric, due: dt.datetime | None) -> bool:
    if not sub.get("full") or not sub.get("metrics"):
        return False
    if metric.key not in sub["metrics"]:
        return False
    if due is not None and parse_time(sub["at"]) > due:
        return False
    return True


def best_of(subs: list, metric: Metric, due: dt.datetime | None):
    """(attempt number, submission) of the best qualifying one, or None."""
    best = None
    for n, sub in enumerate(subs, start=1):
        if not qualifies(sub, metric, due):
            continue
        value = sub["metrics"][metric.key]
        if best is None or metric.better(value, best[1]["metrics"][metric.key]):
            best = (n, sub)
    return best


def rank_players(players: dict, metric: Metric, cap: int, due: dt.datetime | None):
    """Rows for the board and the list of players not on it yet.

    Only a player's first `cap` graded submissions can qualify; a sixth one
    that slipped through before the lock never counts."""
    rows, unranked = [], []
    for alias, player in players.items():
        subs = counted_submissions(player)
        used = len(subs)
        best = best_of(subs[:cap], metric, due)
        common = {"alias": alias, "used": used, "locked": bool(player.get("locked"))}
        if best is None:
            unranked.append(common)
            continue
        n, sub = best
        rows.append({**common, "metric": sub["metrics"][metric.key],
                     "extras": {k: v for k, v in sub["metrics"].items() if k != metric.key},
                     "at": sub["at"], "attempt": n})
    rows.sort(key=lambda r: (metric.sort_key(r["metric"]), r["at"], r["alias"]))
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    unranked.sort(key=lambda r: r["alias"])
    return rows, unranked


def best_reference(subs: list, metric: Metric):
    """The staff reference: best full-score submission, no cap or deadline."""
    best = best_of(sorted(subs, key=lambda s: (s["at"], s["tag"])), metric, None)
    if best is None:
        return None
    n, sub = best
    return {"metric": sub["metrics"][metric.key],
            "extras": {k: v for k, v in sub["metrics"].items() if k != metric.key},
            "at": sub["at"]}
