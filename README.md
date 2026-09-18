# ESE 6150 leaderboard

Board: https://roboracer-class.github.io/leaderboard/ (GitHub Pages serves the `docs/` folder of this repo).

A row is a best full-score lap graded before the deadline (and among the first `submissions.cap` attempts on a lab that sets one). Staff repos give the reference row.

**Who a row is depends on the assignment's mode.** A `mode: team` lab (lab 4 onward) has one repo per team, named `<classroom>-<slug>-group-<n>`, so the row is `Team <n>` and the board is **not anonymous** — the team number is the key, and students just look for their own number. Every other lab keeps the racing aliases, which students learn from the note on their Feedback PR. The builder decides per board from `mode` in `assignments.json` and publishes it as `anonymous` in the board's data file, which is what switches the page's wording.

## Rebuilds

The workflow "Build the board" runs every five minutes (free: public repo), on demand from the Actions tab, and on a `graded` repository_dispatch. It rebuilds, commits changed data under `docs/data`, and Pages redeploys. GitHub switches the schedule off after 60 days without commits; re-enable it on the Actions tab. Fallback without Actions: `tools/rebuild.sh` from any machine holding the two secret files (see the end).

## Commands

Run from this directory with `export LEADERBOARD_SALT=$(cat ~/.config/ese6150-leaderboard/salt)` and `export LEADERBOARD_TOKEN=$(cat ~/.config/ese6150-leaderboard/token)`.

- Alias of a student: `python3 -m builder reveal lab-3-wall-following <username>`
- Whole class ranked: `python3 -m builder who lab-3-wall-following --roster ../classroom50/ese-6150/roster.csv`
- Give an attempt back and unlock: `python3 -m builder refund lab-3-wall-following <username> submit/<tag>` then commit `docs/data` and push.
- Unlock by hand: `classroom50/ese-6150/scripts/lab-access.sh unlock lab-3-wall-following --user <username>`
- **On a team board** the same commands take the team instead of a username, written any way you like — `7`, `group-7` or `"Team 7"`: `python3 -m builder refund lab-4-follow-the-gap 7 submit/<tag>`. `reveal` takes a team too; `who` just prints the standings, because the rows already name the teams. To see the people behind each team use `classroom50/ese-6150/scripts/leaderboard-status.py`, which joins the board against `teams.json`.
- Add a lab: copy the `leaderboard:` and `submissions:` blocks from lab 3's `config.yaml` into the new grader config. A lab can have several boards (one tab and one data file each): use a `leaderboards:` list instead, give each entry a `key` (the tab id becomes `<slug>-<key>`), a `title`, a `requirement` sentence for the page and notes, and a `require:` rule: `full` (the default, the full autograded score) or `{ignore: [D2, D3]}` (full marks on every test except those ids). Lab 4 is the example: a `lap` board that ignores the obstacle-course lines and an `obstacles` board that needs everything. `refund` reaches every board of the assignment; `reveal`/`who` take a board id (`lab-4-follow-the-gap-lap`). The page shows one tab per lab with a switch between the lab's boards; the tab labels (`Lab 3: Wall Follow`, `Lab 4: FTG`) are the `SHORT_NAMES` map at the top of the script in `docs/index.html`, keyed by assignment slug (a lab missing there shows its full name). Above the standings a lollipop chart plots every ranked driver's time (hover or focus a column for the tooltip, click one to highlight that alias; the TA reference as a dashed rule; `+`/`-` widen the columns and refit the time axis to the entries in view, because one slow lap otherwise flattens the podium); its colors live in the `--chart-*` tokens of `docs/index.html`, one validated step per theme.
- Tests: `python3 -m pytest builder/tests`

## Replays

The eye button on a row plays back the run that set that time. Nothing is simulated when someone watches: the referee records the graded run, and the page draws it.

- **Record**: the grader bundle's `replay_recorder.py` samples the car at 20 Hz while the referee times the run and writes `replay-<scenario>.json` into the checkout (a few KB; it can never change a verdict). The assignment's `release_assets` in `assignments.json` lists those names, so the runner attaches them to the submission's release next to `result.json`.
- **Publish**: a board opts in with `replay: <asset name>` in its `leaderboard:` block. When a newly graded attempt is an entry's best, the rebuild reads that asset, keeps only whitelisted numbers within sane bounds (`builder/replays.py`: a release lives in a repo its student can write to) and writes `docs/data/replays/<board>/<entry>.json`, one file per entry, overwritten by a better run. A missing or refused recording costs the row its button and nothing else. Runs graded before a board opted in have no recording; a new submission gets one.
- **Catch up and backfill**: a release is read once, so an entry's best attempt that has no recording gets one extra look (`replay_behind` in `builder/build.py`), which covers runs graded between the grader shipping and this builder shipping. Runs graded before any recording existed are re-run locally by staff, no Actions minutes: `python3 autograder/labs/backfill_replays.py <board> [--reference-dir <staff checkout>]` in the staff repo clones each entry's best submission into a temp folder, grades it in the local image and adds `docs/data/replays/<board>/<entry>.json` (marked `"rerun": 1`, the player says so) plus `backfill.json`, which names the attempt each file belongs to. The rebuild links a backfilled file only while the row still shows that attempt, and never edits it; commit the new files like any other change.
- **Play**: `docs/replay.js` and `replay.css`. It plays as a race on one clock: zero when every car crosses its ranked lap's start line, running until the slowest car on show has finished, then looping. The watched car drives in colour, every other entry of the board greyed out, the TA car always picked out; hover names a car, a click or the list switches to it without moving the clock or the track (the view is framed once per race, not per car). Full screen (`F`; the frame inside the dialog takes it, because the Fullscreen API refuses `<dialog>`), 0.25x to 8x. `?watch=<alias or team>` opens a run directly, which is what Copy link shares. Track images are `docs/assets/maps/`, made by `python3 tools/make_maps.py <dir with the map .yaml/.png>`; a recording whose map is not in `maps.json` is refused, so add the map before the lab opens.

## Where things are

- `docs/index.html`: the page. `docs/data/`: generated, committed by the rebuild.
- `builder/`: the rebuild logic. `tools/`: token and cron scripts.
- Team boards: the label `Team <n>` comes from the repo tail alone, never from a team's display name in `teams.json`, so renaming a team cannot move its attempt history. `teams.json` is read for one purpose only — a group team whose every member is staff becomes the reference row instead of a competitor, which is how a TA posts a reference lap on a lab where no individual repo exists. An unreadable `teams.json` only costs the reference row; no team is ever dropped from the board because of it.
- Cap and lock: **off** — `submissions.cap: 0` in the lab's grader config means unlimited `submit/*` tags. Set a positive cap and it comes back: the grader refuses the next tag, the rebuild locks the repo read-only with `lab-access.sh`, and `builder refund` gives an attempt back.

## Secrets (staff only)

- `LEADERBOARD_SALT`: repo secret, copy in `~/.config/ese6150-leaderboard/salt`. Never change it.
- `LEADERBOARD_TOKEN`: fine-grained PAT (see the link in the class notes). Save it with `./tools/token.sh`, which checks the permissions, writes `~/.config/ese6150-leaderboard/token`, and sets the repo secret.
