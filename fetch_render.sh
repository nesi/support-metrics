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
  SINCE="$1"
  UNTIL="$2"
else
  read -r SINCE UNTIL < <(python3 -c "
from datetime import date, timedelta
today = date.today()
first_this_month = today.replace(day=1)
last_prev_month = first_this_month - timedelta(days=1)
first_prev_month = last_prev_month.replace(day=1)
print(first_prev_month, last_prev_month)
")
fi

echo "$SINCE till $UNTIL"

python3 fetch_jira_tickets.py
python3 fetch_jira_training.py
python3 fetch_github.py --since "$SINCE" --until "$UNTIL"
python3 fetch_modules.py --since "$SINCE" --until "$UNTIL"
python3 fetch_gitlab.py --since "$SINCE" --until "$UNTIL"
python3 render_report.py --since "$SINCE" --until "$UNTIL"

# Fetching and rendering both happen here, locally — .github/workflows/deploy.yml

git add public/index.html 
# if ! git diff --cached --quiet; then
#   git commit -m "Report for $SINCE..$UNTIL"
#   git push
# else
#   echo "No report changes to push."
# fi
