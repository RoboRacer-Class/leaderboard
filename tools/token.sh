#!/usr/bin/env bash
# Save the leaderboard token: paste it once, it is checked against the org,
# written to ~/.config/ese6150-leaderboard/token, and stored as the repo
# secret LEADERBOARD_TOKEN.
#
#   ./tools/token.sh            # prompts for the token
#   ./tools/token.sh < file     # or read it from a file
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=$HOME/.config/ese6150-leaderboard
mkdir -p "$DIR"; chmod 700 "$DIR"
if [[ -t 0 ]]; then
  read -r -s -p "Paste the fine-grained token (github_pat_...): " TOKEN; echo
else
  TOKEN=$(head -1)
fi
TOKEN=${TOKEN//[[:space:]]/}
[[ $TOKEN == github_pat_* ]] || { echo "that is not a fine-grained token (they start with github_pat_)" >&2; exit 1; }
LEADERBOARD_TOKEN=$TOKEN python3 -m builder check-token
printf '%s\n' "$TOKEN" > "$DIR/token"; chmod 600 "$DIR/token"
gh secret set LEADERBOARD_TOKEN -R RoboRacer-Class/leaderboard --body "$TOKEN"
echo "saved to $DIR/token and stored as the repo secret; the rebuild picks it up on its next run"
