"""
Fetch software-build changes for the month: diff nesi/modules-list's
module-list.json between two points in its commit history.

  end   = state as of `until` (the last commit at or before it — the
          normal "as of this date" rule).
  start = state as of `since`, "rounded up": the FIRST commit at or after
          it, not the last one before — so a change from just before the
          period began doesn't get attributed to it.

module-list.json is a dict keyed by software name, each with a "versions"
list. The diff reports four things, matching a name change and a version
change as genuinely different events:
  - new_software      software present at `end` but not at `start`
  - removed_software   software present at `start` but not at `end`
  - new_versions       (software, version) pairs added to existing software
  - removed_versions   (software, version) pairs dropped from existing software

Cached by exact date range: re-running for a period already fetched loads
the cache instead of re-hitting the API. Pass --refresh to force a
re-fetch. Unlike fetch_github.py/fetch_gitlab.py, this doesn't gain
anything from an incremental "only pull what's new" cache — the result is
a diff between two single points in time, not something accumulated over
a range, so there's no partial overlap between periods to reuse.

Run with --since/--until (YYYY-MM-DD), or no args for last full calendar
month. Shared cache/CLI helpers live in fetch_common.py.
"""

import argparse
import base64
import json
from pathlib import Path

import requests

from fetch_common import add_period_args, github_token, read_json, resolve_period, write_json

ORG = "nesi"
REPO = "modules-list"
FILE_PATH = "module-list.json"


def result_file(since, until):
    return Path(f"data/modules_diff_{since}_{until}.json")


_session = requests.Session()
_session.headers.update({
    "Authorization": f"Bearer {github_token()}",
    "Accept": "application/vnd.github+json",
})


def _commits(params):
    resp = _session.get(
        f"https://api.github.com/repos/{ORG}/{REPO}/commits",
        params={"path": FILE_PATH, "per_page": 100, **params},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def commit_as_of(target_date):
    """Last commit at or before target_date."""
    commits = _commits({"until": f"{target_date}T23:59:59Z"})
    if not commits:
        raise RuntimeError(f"No commits to {FILE_PATH} found before {target_date}")
    return commits[0]  # newest-first, so index 0 is the most recent one <= target_date


def commit_rounded_up(target_date):
    """First commit at or after target_date — round UP to the next commit
    rather than back to the last one before it."""
    commits = _commits({"since": f"{target_date}T00:00:00Z"})
    if not commits:
        raise RuntimeError(f"No commits to {FILE_PATH} found at or after {target_date}")
    return commits[-1]  # newest-first, so the last item is the oldest in this window


def file_at(sha):
    resp = _session.get(
        f"https://api.github.com/repos/{ORG}/{REPO}/contents/{FILE_PATH}",
        params={"ref": sha},
        timeout=20,
    )
    resp.raise_for_status()
    content = base64.b64decode(resp.json()["content"])
    return json.loads(content)


def diff(start_data, end_data):
    start_software = set(start_data)
    end_software = set(end_data)

    new_software = sorted(end_software - start_software)
    removed_software = sorted(start_software - end_software)

    new_versions, removed_versions = [], []
    for name in sorted(start_software & end_software):
        start_versions = set(start_data[name].get("versions", []))
        end_versions = set(end_data[name].get("versions", []))
        for v in sorted(end_versions - start_versions):
            new_versions.append({"software": name, "version": v})
        for v in sorted(start_versions - end_versions):
            removed_versions.append({"software": name, "version": v})

    return {
        "new_software": new_software,
        "removed_software": removed_software,
        "new_versions": new_versions,
        "removed_versions": removed_versions,
    }


def fetch(since, until):
    start_commit = commit_rounded_up(since)
    end_commit = commit_as_of(until)
    print(f"  start: {start_commit['sha'][:8]} ({start_commit['commit']['committer']['date']})")
    print(f"  end:   {end_commit['sha'][:8]} ({end_commit['commit']['committer']['date']})")

    start_data = file_at(start_commit["sha"])
    end_data = file_at(end_commit["sha"])

    return {
        "period": {"since": str(since), "until": str(until)},
        "start_commit": {"sha": start_commit["sha"], "date": start_commit["commit"]["committer"]["date"]},
        "end_commit": {"sha": end_commit["sha"], "date": end_commit["commit"]["committer"]["date"]},
        **diff(start_data, end_data),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    add_period_args(parser, "Rebuild the cached diff from scratch")
    args = parser.parse_args()
    since, until = resolve_period(args)

    out_path = result_file(since, until)
    result = None if args.refresh else read_json(out_path)
    if result is not None:
        pass
    else:
        print(f"Fetching module-list.json diff for {since}..{until}")
        result = fetch(since, until)
        write_json(out_path, result)
