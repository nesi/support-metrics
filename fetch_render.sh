#!/usr/bin/env bash
# Fetches fresh data and renders the report for one calendar month.
#
# Usage: fetch_render.sh [since] [until]
#   Both ISO dates (YYYY-MM-DD), or omit both for "last full calendar month".
# All take --since/--until and cache/report by that exact period.
set -euo pipefail

cd "$(dirname "$0")"
source .venv/bin/activate

if [ $# -eq 2 ]; then
  ARGS=(--since "$1" --until "$2")
  echo "$1 till $2"
else
  ARGS=()
  echo "last full calendar month"
fi

python3 fetch_jira_tickets.py
python3 fetch_jira_training.py
python3 fetch_github.py "${ARGS[@]}"
python3 fetch_modules.py "${ARGS[@]}"
python3 fetch_gitlab.py "${ARGS[@]}"
python3 render_report.py "${ARGS[@]}"

# Fetching and rendering both happen here, locally — .github/workflows/deploy.yml

# git add public/index.html 
# if ! git diff --cached --quiet; then
#   git commit -m "Report for $SINCE..$UNTIL"
#   git push
# else
#   echo "No report changes to push."
# fi
