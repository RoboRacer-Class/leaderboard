"""A recording comes from a release in a repository its student controls, so it
is untrusted: the builder republishes only whitelisted numbers, or nothing."""
import json

from builder import replays

MAPS = {"levine_blocked", "levine_obs"}


def recording(**over):
    doc = {"v": 1, "map": "levine_blocked", "hz": 20, "n": 5,
           "x": [100, 5, 5, 5, 5], "y": [-20, 0, 1, 0, -1], "yaw": [0, 10, 10, -5, 0], "s": [400, 2, 1, 0, -3],
           "laps": [[1, 4]], "lap_ms": [11420], "best": 0, "end": "finished"}
    doc.update(over)
    return doc


def clean(doc, **kw):
    raw = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
    return replays.clean(raw, MAPS, **kw)


def test_valid_recording_passes_unchanged():
    assert clean(recording()) == recording()


def test_only_whitelisted_keys_survive():
    out = clean(recording(owner="someone", note="<script>alert(1)</script>", repo="org/ese-6150-lab-4-someone"))
    assert out == recording()


def test_columns_must_be_plain_integers():
    assert clean(recording(x=[100, "5", 5, 5, 5])) is None
    assert clean(recording(y=[-20, 0, 1.5, 0, -1])) is None
    assert clean(recording(s=[400, True, 1, 0, -3])) is None
    assert clean(recording(yaw="0,10,10")) is None
    assert clean(recording(x=[100, 5, 5, 5])) is None                 # columns differ in length


def test_shape_and_range_limits():
    assert clean(recording(v=2)) is None
    assert clean(recording(map="somewhere_else")) is None
    assert clean(recording(map="../../index")) is None
    assert clean(recording(hz=1000)) is None
    assert clean(recording(x=[100], y=[0], yaw=[0], s=[0], n=1, laps=[], lap_ms=[])) is None   # not a run
    assert clean(recording(x=[100, 50_000, 5, 5, 5])) is None         # a 500 m jump in one tick
    assert clean(recording(x=[10**9, 5, 5, 5, 5])) is None            # nowhere near a track
    long = 20 * 700
    assert clean(recording(n=long, x=[0] * long, y=[0] * long, yaw=[0] * long, s=[0] * long,
                           laps=[], lap_ms=[])) is None               # longer than any graded run


def test_oversized_or_broken_files_are_refused():
    assert replays.clean(b"x" * (replays.MAX_BYTES + 1), MAPS) is None
    assert replays.clean(b"{not json", MAPS) is None
    assert replays.clean(b"[1, 2, 3]", MAPS) is None
    assert replays.clean(b"\xff\xfe", MAPS) is None


def test_bad_lap_details_are_dropped_but_the_run_is_kept():
    out = clean(recording(laps=[[1, 99]], lap_ms=[11420], best=0))
    assert out is not None and out["laps"] == [] and out["lap_ms"] == [] and "best" not in out
    out = clean(recording(lap_ms=["fast"]))
    assert out["laps"] == [[1, 4]] and out["lap_ms"] == [] and out["best"] == 0
    assert "end" not in clean(recording(end="<b>won</b>"))
    assert "best" not in clean(recording(best=3))


def test_ranked_lap_must_match_the_board_time():
    assert clean(recording(), lap_seconds=11.42) is not None
    assert clean(recording(), lap_seconds=11.45) is not None            # rounding slack
    assert clean(recording(), lap_seconds=9.0) is None                  # someone else's lap
    # a ranked run's recording must name the lap the board ranks, with its time: the player
    # races on that lap, so an unnamed or untimed one would finish out of the board's order
    assert clean(recording(laps=[], lap_ms=[], best=None), lap_seconds=11.42) is None
    assert clean(recording(best=None), lap_seconds=11.42) is None
    assert clean(recording(lap_ms=[]), lap_seconds=11.42) is None
    assert clean(recording(laps=[], lap_ms=[], best=None)) is not None  # no time to hold it to


def drive(seconds=12.0, speed=4.0, hz=20, lap=(1.0, 11.0), **over):
    """A car driving straight at `speed` m/s, one timed lap between the given seconds."""
    n = round(seconds * hz) + 1
    step = round(speed * 100 / hz)
    doc = {"v": 1, "map": "levine_blocked", "hz": hz, "n": n,
           "x": [0] + [step] * (n - 1), "y": [-20] + [0] * (n - 1), "yaw": [0] + [0] * (n - 1),
           "s": [round(speed * 100)] + [0] * (n - 1),
           "laps": [[round(lap[0] * hz), round(lap[1] * hz)]], "lap_ms": [round((lap[1] - lap[0]) * 1000)],
           "best": 0, "end": "finished"}
    doc.update(over)
    return doc


def total(column):
    out, t = [], 0
    for d in column:
        t += d
        out.append(t)
    return out


def test_retime_puts_the_ranked_lap_on_the_board_time():
    out = replays.retime(drive(), 9.5)                                  # the re-run took 10.0 s, the graded run 9.5 s
    assert out["lap_ms"] == [9500] and out["best"] == 0 and out["end"] == "finished"
    assert out["n"] == 229 and out["laps"] == [[19, 209]]               # 240 intervals * 0.95, the lap's ends with them
    assert out["hz"] == 20 and out["map"] == "levine_blocked" and out["v"] == 1
    x = total(out["x"])
    assert x[0] == 0 and x[-1] == 4800                                  # the same drive, first to last metre
    assert x[100] == round(2000 / 0.95)                                 # ... reached earlier
    assert set(out["s"][1:]) == {0} and out["s"][0] == 421              # and faster: 4 m/s over 0.95 of the time
    assert set(total(out["y"])) == {-20}
    assert clean(out, lap_seconds=9.5) == out                           # publishable as is, and it matches the board


def test_retime_stretches_a_faster_rerun_too():
    out = replays.retime(drive(), 11.0)
    assert out["lap_ms"] == [11000] and out["n"] == 265 and out["laps"] == [[22, 242]]
    assert out["s"][0] == 364 and total(out["x"])[-1] == 4800
    assert clean(out, lap_seconds=11.0) == out


def test_retime_on_the_board_time_already_changes_nothing():
    assert replays.retime(drive(), 10.0) == drive()


def test_retime_scales_every_lap_and_keeps_the_ranked_one_exact():
    doc = drive(seconds=22.0, laps=[[20, 220], [220, 420]], lap_ms=[10000, 10020], best=1)
    out = replays.retime(doc, 9.9)
    assert out["lap_ms"] == [9880, 9900] and out["best"] == 1
    assert out["laps"] == [[20, 217], [217, 415]]


def test_retime_refuses_what_it_cannot_put_on_a_clock():
    assert replays.retime(drive(best=None), 9.5) is None                # no ranked lap to hold to
    assert replays.retime(drive(lap_ms=[]), 9.5) is None
    assert replays.retime(drive(), 7.0) is None                         # a third faster: another run, not this one
    assert replays.retime(drive(), 13.0) is None
    assert replays.retime(drive(), 8.0) is not None and replays.retime(drive(), 12.5) is not None


def test_file_names_come_from_the_public_key():
    assert replays.file_name("Team 2") == "team-2"
    assert replays.file_name("Thunder Mamba 45") == "thunder-mamba-45"
    assert replays.file_name("reference") == "reference"
    assert replays.file_name("../../etc/passwd") == "etc-passwd"
    assert replays.file_name("") == "entry"


def test_save_writes_compact_json_and_a_versioned_reference(tmp_path):
    ref = replays.save(tmp_path, "lab-4-follow-the-gap-lap", "Team 2", recording())
    path, _, version = ref.partition("?v=")
    assert path == "replays/lab-4-follow-the-gap-lap/team-2.json" and len(version) == 8
    text = (tmp_path / path).read_text()
    assert json.loads(text) == recording() and " " not in text
    # a better run overwrites the same file and changes the version, so no browser serves the old lap
    again = replays.save(tmp_path, "lab-4-follow-the-gap-lap", "Team 2", recording(lap_ms=[11000]))
    assert again.startswith(path + "?v=") and again != ref


def test_known_maps_reads_the_page_assets(tmp_path):
    data = tmp_path / "docs" / "data"
    data.mkdir(parents=True)
    assert replays.known_maps(data) == set()
    maps = tmp_path / "docs" / "assets" / "maps"
    maps.mkdir(parents=True)
    (maps / "maps.json").write_text(json.dumps({"levine_blocked": {}, "levine_obs": {}}))
    assert replays.known_maps(data) == MAPS
