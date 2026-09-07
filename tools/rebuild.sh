#!/usr/bin/env bash
# Rebuild the board and publish changed data. Cron runs this every five
# minutes; it stays silent until the token and salt exist. Works in its own
# clone under ~/.local/share so it never touches a development checkout.
set -euo pipefail
DIR=$HOME/.config/ese6150-leaderboard
[[ -s $DIR/token && -s $DIR/salt ]] || exit 0
export LEADERBOARD_TOKEN LEADERBOARD_SALT
LEADERBOARD_TOKEN=$(cat "$DIR/token"); LEADERBOARD_SALT=$(cat "$DIR/salt")
WORK=$HOME/.local/share/ese6150-leaderboard/repo
exec 9>"$DIR/rebuild.lock"; flock -n 9 || exit 0
if [[ ! -d $WORK/.git ]]; then
  mkdir -p "$(dirname "$WORK")"
  git clone -q https://github.com/RoboRacer-Class/leaderboard.git "$WORK"
fi
cd "$WORK"
git fetch -q origin main && git reset -q --hard origin/main
python3 -m builder build --data-dir docs/data
git add docs/data
git diff --cached --quiet && exit 0
git -c user.name="leaderboard bot" -c user.email="leaderboard-bot@users.noreply.github.com" \
  commit -q -m "board: $(date -u +%Y-%m-%dT%H:%MZ)"
git push -q origin main
echo "$(date -u +%FT%TZ) published"
