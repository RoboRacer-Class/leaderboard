# ESE 6150 leaderboard

Live board: **https://roboracer-class.github.io/leaderboard/**

Every timed lab gets a tab. A row is one driver's best lap in the grading simulator, shown under a racing alias: their best full-score submission graded before the deadline, among their first five submissions. The TA solution sits in the table as an unranked reference row. The top five earn extra credit.

## How it works

- **What counts.** A lab is on the board when its grader config (`classroom50/ese-6150/autograders/<lab>/config.yaml`) has a `leaderboard:` block: which test's detail string carries the metric, the regex that reads it, the direction, the podium size. The same file's `submissions.cap` is the number of submissions a repo gets. Every `submit/*` tag is one attempt, graded or not: the runner mints one per submission before grading, so a run that fails for infrastructure reasons still spends an attempt (see refunds).
- **Rebuilds.** The workflow in this repo runs every five minutes (and on demand: Actions, "Build the board", "Run workflow"). It reads `assignments.json` and the grader configs from the private class repo, lists each student repo's `submit/*` tags and releases, downloads new `result.json` files, and rewrites `data/<lab>.json` and `data/index.json`. Changed data is committed to `main`, which GitHub Pages serves. The minutes are free because this repo is public.
- **Submission time** is the creation time of the Actions run that graded the tag: server-side, and unchanged by regrades. "On time" means before the assignment's `due`.
- **Aliases** are a keyed hash of the GitHub username (`LEADERBOARD_SALT`), rendered as adjective + noun + number. The public data holds aliases and a second keyed fingerprint per alias, nothing else. Without the salt neither can be reversed.
- **Finding yourself.** Each student gets a sticky note on the Feedback pull request of their own repo with their alias, rank, best time, submissions used, lock state, and a link that highlights their row. The note is edited in place whenever those numbers change. The page also has a find-me box that remembers the alias.
- **The cap.** Once a repo has five attempts the builder runs the class's own `lab-access.sh lock <lab> --user <login>` (falling back to the collaborator API) and the repo becomes read-only. The grader itself counts the `submit/*` tags in its checkout and refuses a sixth push that slips in before the lock, with the commit status "submission cap reached: not graded, grade unchanged" and no release. The builder remembers every attempt it has seen, so deleting tags or releases does not reset a count.
- **Staff repos** (members of the teacher, hta, and ta teams) supply the reference row and are never capped, locked, or noted.

## Setup, once

1. `LEADERBOARD_SALT` is set as a repository secret and kept in `~/.config/ese6150-leaderboard/salt` on Cedric's machine. Never rotate it: every alias would change.
2. `LEADERBOARD_TOKEN` is a fine-grained personal access token. Create it at https://github.com/settings/personal-access-tokens/new with resource owner `RoboRacer-Class`, access to **all repositories**, and these permissions:
   - Repository: Contents **Read** (releases and the class config), Actions **Read** (run times), Pull requests **Read and write** (the notes), Issues **Read and write** (fallback when a repo has no Feedback PR), Administration **Read and write** (the lock), Metadata Read (automatic).
   - Organization: Members **Read** (the staff teams).

   Then store it: `gh secret set LEADERBOARD_TOKEN -R RoboRacer-Class/leaderboard`. Until it exists, scheduled runs exit green with a warning and the board is not rebuilt; a manual run fails.
3. GitHub switches off scheduled workflows after 60 days without a commit. If the board stops updating between labs, re-enable the workflow on the Actions tab.

## Day to day

All commands run from a clone of this repo with the salt exported:

```bash
export LEADERBOARD_SALT=$(cat ~/.config/ese6150-leaderboard/salt)
```

- **Who holds an alias:** `python3 -m builder reveal lab-3-wall-following <username> [<username> ...]`
- **The whole class, ranked** (for extra credit): `python3 -m builder who lab-3-wall-following --roster ../classroom50/ese-6150/roster.csv`
- **Refund an attempt** after a grading-infrastructure failure: `GH_TOKEN=$(gh auth token) python3 -m builder refund lab-3-wall-following <username> submit/<tag>` marks that attempt refunded, deletes its tag when it never graded (so the grader stops counting it), and unlocks the repo. Commit `data/lab-3-wall-following.json` and push; the next build re-posts the student's note. The tag name is in the student's Actions run or in `git ls-remote --tags`.
- **Lock or unlock by hand:** `classroom50/ese-6150/scripts/lab-access.sh`.
- **Add a lab:** copy the `leaderboard:` and `submissions:` blocks from lab 3's `config.yaml` into the new lab's config, adjust the test id and the regex to that grader's detail string, push the class repo. The next build adds the tab.
- **Rebuild now:** Actions, "Build the board", "Run workflow". Tick "dry run" to see what would change without posting notes or locking.
- **Run locally against the live org** (nothing is written to GitHub): `GH_TOKEN=$(gh auth token) python3 -m builder build --dry-run --config-dir ../classroom50 --data-dir /tmp/board`
- **Tests:** `python3 -m pytest builder/tests`

## Privacy

Public: aliases, lap times, submission timestamps, attempt counts, lock state, keyed owner fingerprints, one-way ids of submission tags. Never usernames, repo names, tag names, or scores. The workflow log is public too, so the builder redacts every username and repo name it encounters before printing.
