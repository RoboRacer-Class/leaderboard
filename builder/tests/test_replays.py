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
    assert clean(recording(laps=[], lap_ms=[], best=None), lap_seconds=9.0) is not None   # nothing to compare


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
