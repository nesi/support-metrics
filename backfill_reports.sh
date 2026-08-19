#!/usr/bin/env bash
# Backfill monthly reports for a range of calendar months.
#
# Usage: backfill_reports.sh <first-month> <last-month>
#   Both YYYY-MM. Runs fetch_render.sh once per calendar month in
#   [first-month, last-month], committing public/index.html after each
#   (matching the "Report for $since..$until" convention already in git
#   history). The current in-progress month is truncated at today.
#
# Nothing is pushed — review with `git log` and push when ready.
set -euo pipefail

cd "$(dirname "$0")"

first_month="$1"
last_month="$2"
today=$(date +%Y-%m-%d)

current="${first_month}-01"
while [[ "$(date -d "$current" +%Y-%m)" < "$last_month" || "$(date -d "$current" +%Y-%m)" == "$last_month" ]]; do
  since="$current"
  until=$(date -d "$since +1 month -1 day" +%Y-%m-%d)
  if [[ "$until" > "$today" ]]; then
    until="$today"
  fi

  echo "=== $since .. $until ==="
  bash fetch_render.sh "$since" "$until"

  git add public/index.html
  if ! git diff --cached --quiet; then
    git commit -m "Report for $since..$until"
  else
    echo "No report changes to commit for $since..$until"
  fi

  current=$(date -d "$current +1 month" +%Y-%m-%d)
done

echo "Backfill complete. Review with 'git log', then push when ready."
