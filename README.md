# ESE 6150 leaderboard

Board: https://roboracer-class.github.io/leaderboard/ (GitHub Pages serves the `docs/` folder of this repo).

A row is a driver's best full-score lap graded before the deadline, among their first five attempts, under an alias. Staff repos give the reference row. Students learn their alias from the note on their Feedback PR.

## Rebuilds

The workflow "Build the board" runs every five minutes (free: public repo), on demand from the Actions tab, and on a `graded` repository_dispatch. It rebuilds, commits changed data under `docs/data`, and Pages redeploys. GitHub switches the schedule off after 60 days without commits; re-enable it on the Actions tab. Fallback without Actions: `tools/rebuild.sh` from any machine holding the two secret files (see the end).

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

## Secrets (staff only)

- `LEADERBOARD_SALT`: repo secret, copy in `~/.config/ese6150-leaderboard/salt`. Never change it.
- `LEADERBOARD_TOKEN`: fine-grained PAT (see the link in the class notes). Save it with `./tools/token.sh`, which checks the permissions, writes `~/.config/ese6150-leaderboard/token`, and sets the repo secret.
