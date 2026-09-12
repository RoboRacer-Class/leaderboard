# ESE 6150 leaderboard

Board: https://roboracer-class.github.io/leaderboard/ (GitHub Pages serves the `docs/` folder of this repo).

A row is a driver's best full-score lap graded before the deadline, under an alias (and among the first `submissions.cap` attempts on a lab that sets one). Staff repos give the reference row. Students learn their alias from the note on their Feedback PR.

## Rebuilds

The workflow "Build the board" runs every five minutes (free: public repo), on demand from the Actions tab, and on a `graded` repository_dispatch. It rebuilds, commits changed data under `docs/data`, and Pages redeploys. GitHub switches the schedule off after 60 days without commits; re-enable it on the Actions tab. Fallback without Actions: `tools/rebuild.sh` from any machine holding the two secret files (see the end).

## Commands

Run from this directory with `export LEADERBOARD_SALT=$(cat ~/.config/ese6150-leaderboard/salt)` and `export LEADERBOARD_TOKEN=$(cat ~/.config/ese6150-leaderboard/token)`.

- Alias of a student: `python3 -m builder reveal lab-3-wall-following <username>`
- Whole class ranked: `python3 -m builder who lab-3-wall-following --roster ../classroom50/ese-6150/roster.csv`
- Give an attempt back and unlock: `python3 -m builder refund lab-3-wall-following <username> submit/<tag>` then commit `docs/data` and push.
- Unlock by hand: `classroom50/ese-6150/scripts/lab-access.sh unlock lab-3-wall-following --user <username>`
- Add a lab: copy the `leaderboard:` and `submissions:` blocks from lab 3's `config.yaml` into the new grader config. A lab can have several boards (one tab and one data file each): use a `leaderboards:` list instead, give each entry a `key` (the tab id becomes `<slug>-<key>`), a `title`, a `requirement` sentence for the page and notes, and a `require:` rule: `full` (the default, the full autograded score) or `{ignore: [D2, D3]}` (full marks on every test except those ids). Lab 4 is the example: a `lap` board that ignores the obstacle-course lines and an `obstacles` board that needs everything. `refund` reaches every board of the assignment; `reveal`/`who` take a board id (`lab-4-follow-the-gap-lap`). The page shows one tab per lab with a switch between the lab's boards; the tab labels (`Wall follow`, `FTG`) are the `SHORT_NAMES` map at the top of the script in `docs/index.html`, keyed by assignment slug (a lab missing there shows its full name). Above the standings a lollipop chart plots every ranked driver's time (hover or focus a column for the tooltip, click one to highlight that alias; the TA reference as a dashed rule); its colors live in the `--chart-*` tokens of `docs/index.html`, one validated step per theme.
- Tests: `python3 -m pytest builder/tests`

## Where things are

- `docs/index.html`: the page. `docs/data/`: generated, committed by the rebuild.
- `builder/`: the rebuild logic. `tools/`: token and cron scripts.
- Cap and lock: **off** — `submissions.cap: 0` in the lab's grader config means unlimited `submit/*` tags. Set a positive cap and it comes back: the grader refuses the next tag, the rebuild locks the repo read-only with `lab-access.sh`, and `builder refund` gives an attempt back.

## Secrets (staff only)

- `LEADERBOARD_SALT`: repo secret, copy in `~/.config/ese6150-leaderboard/salt`. Never change it.
- `LEADERBOARD_TOKEN`: fine-grained PAT (see the link in the class notes). Save it with `./tools/token.sh`, which checks the permissions, writes `~/.config/ese6150-leaderboard/token`, and sets the repo secret.
