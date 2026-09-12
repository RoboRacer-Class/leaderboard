import datetime as dt

import pytest

from builder import rules

LAB3_BLOCK = {
    "test": "C1",
    "pattern": r"lap time (?P<lap_s>[0-9.]+) s(?:, average (?P<avg_mps>[0-9.]+) m/s)?(?:, top (?P<top_mps>[0-9.]+) m/s)?",
    "metric": "lap_s", "unit": "s", "label": "Lap time", "direction": "lower",
    "extras": [{"key": "avg_mps", "label": "Avg speed", "unit": "m/s"}],
}


def result(score, max_score, detail):
    return {"score": score, "max-score": max_score, "tests": [
        {"test-name": "A1 something", "passed": True, "score": 2, "max-score": 2, "detail": ""},
        {"test-name": "C1 drives one counter-clockwise lap", "passed": score == max_score,
         "score": 40, "max-score": 40, "detail": detail}]}


def test_metric_from_config_validates():
    m = rules.metric_from_config(LAB3_BLOCK)
    assert m.key == "lap_s" and m.direction == "lower" and m.extras[0]["key"] == "avg_mps"
    with pytest.raises(ValueError):
        rules.metric_from_config({**LAB3_BLOCK, "pattern": "no group"})
    with pytest.raises(ValueError):
        rules.metric_from_config({**LAB3_BLOCK, "direction": "sideways"})


def test_extract_metrics_from_real_detail():
    m = rules.metric_from_config(LAB3_BLOCK)
    got = rules.extract_metrics(result(90, 90, "lap time 13.47 s, average 4.66 m/s, top 5.0 m/s"), m)
    assert got == {"lap_s": 13.47, "avg_mps": 4.66, "top_mps": 5.0}
    partial = "the car touched a wall 17% of the way around; covered 17% of the loop; partial credit 3/40"
    assert rules.extract_metrics(result(53, 90, partial), m) is None
    assert rules.extract_metrics({"tests": []}, m) is None


def test_full_score():
    assert rules.full_score({"score": 90, "max-score": 90})
    assert not rules.full_score({"score": 89, "max-score": 90})
    assert not rules.full_score({"score": 0, "max-score": 0})
    assert not rules.full_score({"score": True, "max-score": 1})


def sub(tag, at, lap=None, full=True, refunded=False):
    metrics = {"lap_s": lap, "avg_mps": 4.0} if lap is not None else None
    s = {"id": rules.attempt_id(tag), "at": at, "graded": True, "full": full, "metrics": metrics}
    if refunded:
        s["refunded"] = True
    return s


DUE = rules.parse_time("2026-09-17T03:59:00Z")


def test_ranking_applies_cap_deadline_full_score_and_ties():
    m = rules.metric_from_config(LAB3_BLOCK)
    players = {
        "Fast One": {"submissions": [sub("submit/a", "2026-09-10T10:00:00Z", 15.0),
                                     sub("submit/b", "2026-09-11T10:00:00Z", 12.0)]},
        # Same best time as Fast One's second run but achieved earlier -> ranks above it.
        "Early Bird": {"submissions": [sub("submit/c", "2026-09-10T09:00:00Z", 12.0)]},
        # Only full-score runs count.
        "Almost": {"submissions": [sub("submit/d", "2026-09-10T10:00:00Z", 10.0, full=False)]},
        # Late runs never count, even if faster.
        "Late": {"submissions": [sub("submit/e", "2026-09-17T04:00:00Z", 9.0)]},
        # A sixth run that slipped past the cap is ignored; a refunded one does not spend an attempt.
        "Spent": {"submissions": [sub(f"submit/{i}", f"2026-09-1{i}T10:00:00Z", 20.0 - i) for i in range(1, 7)]
                  + [sub("submit/r", "2026-09-09T10:00:00Z", 1.0, refunded=True)], "locked": True},
    }
    rows, unranked = rules.rank_players(players, m, cap=5, due=DUE)
    by_alias = {r["alias"]: r for r in rows}
    assert [r["alias"] for r in rows] == ["Early Bird", "Fast One", "Spent"]
    assert by_alias["Early Bird"]["rank"] == 1 and by_alias["Fast One"]["rank"] == 2
    assert by_alias["Fast One"]["attempt"] == 2 and by_alias["Fast One"]["used"] == 2
    assert by_alias["Spent"]["metric"] == 15.0 and by_alias["Spent"]["used"] == 6
    assert by_alias["Spent"]["locked"] is True
    assert by_alias["Fast One"]["extras"] == {"avg_mps": 4.0}
    assert [u["alias"] for u in unranked] == ["Almost", "Late"]


def test_reference_ignores_cap_and_deadline():
    m = rules.metric_from_config(LAB3_BLOCK)
    subs = [sub("submit/x", "2026-09-20T00:00:00Z", 13.47), sub("submit/y", "2026-09-01T00:00:00Z", 14.0),
            sub("submit/z", "2026-09-02T00:00:00Z", 5.0, full=False)]
    assert rules.best_reference(subs, m)["metric"] == 13.47
    assert rules.best_reference([], m) is None


def test_full_score_can_ignore_tests():
    result = {"score": 80, "max-score": 90, "tests": [
        {"test-name": "A1 package", "score": 10, "max-score": 10},
        {"test-name": "D1 lap", "score": 10, "max-score": 10},
        {"test-name": "D2 corners", "score": 0, "max-score": 5},
        {"test-name": "D3 bonus", "score": 0, "max-score": 5},
    ]}
    assert not rules.full_score(result)
    assert rules.full_score(result, ignore=("D2", "D3"))
    assert not rules.full_score(result, ignore=("D3",))
    assert not rules.full_score({"score": 90, "max-score": 90, "tests": []}, ignore=("D2",))
    assert rules.ignored_tests({"require": {"ignore": ["D2", "D3"]}}) == ("D2", "D3")
    assert rules.ignored_tests({}) == () and rules.ignored_tests({"require": "full"}) == ()
    with pytest.raises(ValueError):
        rules.ignored_tests({"require": "some"})
