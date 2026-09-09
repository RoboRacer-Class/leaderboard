from builder import notes

LAB = {"slug": "lab-3-wall-following", "title": "Lab 3: Wall Following", "podium": 5,
       "metric": {"key": "lap_s", "unit": "s", "label": "Lap time"}}


def test_render_mentions_alias_rank_link_and_cap():
    row = {"rank": 4, "metric": 13.47, "attempt": 2}
    text = notes.render(LAB, "Turbo Falcon 42", row, used=3, cap=5, locked=False, total_rows=31,
                        reference={"metric": 13.5}, board_url="https://x.github.io/leaderboard",
                        generated_at="2026-09-10 14:05 UTC")
    assert notes.MARKER in text
    assert "**Turbo Falcon 42**" in text and "Rank 4 of 31" in text and "13.47 s" in text
    assert "?lab=lab-3-wall-following&me=Turbo%20Falcon%2042" in text
    assert "3 of 5" in text and "2 left" in text


def test_render_when_unranked_and_locked():
    text = notes.render(LAB, "Misty Owl 10", None, used=5, cap=5, locked=True, total_rows=0,
                        reference=None, board_url="https://x", generated_at="now")
    assert "Not on the board yet" in text and "read-only" in text and "5 of 5" in text


def test_render_without_a_cap_promises_no_limit():
    row = {"rank": 4, "metric": 13.47, "attempt": 9}
    text = notes.render(LAB, "Turbo Falcon 42", row, used=9, cap=0, locked=False, total_rows=31,
                        reference=None, board_url="https://x", generated_at="now")
    assert "Submissions used: **9**" in text and "no limit" in text
    assert "read-only" not in text and "of 0" not in text


def test_fingerprint_changes_with_rank_or_usage():
    row = {"rank": 1, "metric": 12.0}
    a = notes.fingerprint(row, 1, 5, False, 10, None)
    b = notes.fingerprint({"rank": 2, "metric": 12.0}, 1, 5, False, 10, None)
    c = notes.fingerprint(row, 2, 5, False, 10, None)
    assert len({a, b, c}) == 3


def test_find_note_matches_marker_and_author():
    comments = [{"id": 1, "body": "hello", "user": {"login": "x"}},
                {"id": 2, "body": notes.MARKER + "\nhi", "user": {"login": "bot"}}]
    assert notes.find_note(comments) == 2
    assert notes.find_note(comments, author="bot") == 2
    assert notes.find_note(comments, author="other") is None
