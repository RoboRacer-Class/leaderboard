# ESE 6150 leaderboard

Board: https://roboracer-class.github.io/leaderboard/ (public page; this repo is private and only `docs/` is published).

A row is a driver's best full-score lap graded before the deadline, among their first five attempts, under an alias. Staff repos give the reference row. Students learn their alias from the note on their Feedback PR.

## Secrets

- `LEADERBOARD_SALT`: repo secret, copy in `~/.config/ese6150-leaderboard/salt`. Never change it.
- `LEADERBOARD_TOKEN`: fine-grained PAT (see the link in the class notes). Save it with `./tools/token.sh`, which checks the permissions, writes `~/.config/ese6150-leaderboard/token`, and sets the repo secret.

## Rebuilds

`tools/rebuild.sh` runs from cron every five minutes on Cedric's machine (`crontab -l`). It clones into `~/.local/share/ese6150-leaderboard/repo`, rebuilds, and pushes changed data. It does nothing until the token exists. Fallback: Actions, "Build the board", "Run workflow" (billed minutes, private repo).

## Commands

Run from this directory with `export LEADERBOARD_SALT=$(cat ~/.config/ese6150-leaderboard/salt)` and `export LEADERBOARD_TOKEN=$(cat ~/.config/ese6150-leaderboard/token)`.

- Alias of a student: `python3 -m builder reveal lab-3-wall-following <username>`
- Whole class ranked: `python3 -m builder who lab-3-wall-following --roster ../classroom50/ese-6150/roster.csv`
- Give an attempt back and unlock: `python3 -m builder refund lab-3-wall-following <username> submit/<tag>` then commit `docs/data` and push.
- Unlock by hand: `classroom50/ese-6150/scripts/lab-access.sh unlock lab-3-wall-following --user <username>`
- Add a lab: copy the `leaderboard:` and `submissions:` blocks from lab 3's `config.yaml` into the new grader config.
- Tests: `python3 -m pytest builder/tests`

## Where things are

- `docs/index.html`: the page. `docs/data/`: generated, committed by the rebuild.
- `builder/`: the rebuild logic. `tools/`: token and cron scripts.
- Cap and lock: five `submit/*` tags per repo; the grader refuses the sixth, the rebuild locks the repo with `lab-access.sh`.
