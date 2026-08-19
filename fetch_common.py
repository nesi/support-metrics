"""
Shared helpers for the fetch scripts.
"""

import argparse
import json
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests


def read_json(path):
    """Parsed JSON from `path`, or None if it doesn't exist."""
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else None


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def previous_month_range(today=None):
    today = today or date.today()
    first_of_this_month = today.replace(day=1)
    last_of_prev_month = first_of_this_month - timedelta(days=1)
    first_of_prev_month = last_of_prev_month.replace(day=1)
    return first_of_prev_month, last_of_prev_month


def github_token():
    result = subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def add_period_args(parser, refresh_help="Rebuild the cache from scratch"):
    parser.add_argument("--since", type=date.fromisoformat)
    parser.add_argument("--until", type=date.fromisoformat)
    parser.add_argument("--refresh", action="store_true", help=refresh_help)


def resolve_period(args):
    return (args.since, args.until) if args.since and args.until else previous_month_range()


def parse_refresh_arg(refresh_help="Rebuild the cache from scratch"):
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help=refresh_help)
    return parser.parse_args().refresh


def jira_session(base_url, user, token):
    session = requests.Session()
    session.auth = (user, token)
    session.headers.update({"Accept": "application/json"})
    return session


def jira_search(session, base_url, jql, fields, page_size=100, on_issue=None):
    """Paginate JIRA call"""
    issues = []
    next_token = None
    while True:
        body = {"jql": jql, "maxResults": page_size, "fields": fields}
        if next_token:
            body["nextPageToken"] = next_token

        resp = session.post(
            f"{base_url}/rest/api/3/search/jql",
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        batch = data["issues"]
        if on_issue:
            for issue in batch:
                on_issue(issue)
        issues.extend(batch)

        if data.get("isLast", True) or not batch:
            break
        next_token = data.get("nextPageToken")
        if not next_token:
            break
        time.sleep(0.15)
    return issues


def fetch_incremental(session, base_url, project, fields, cache_file, force=False, on_issue=None, page_size=100, label="issues"):
    """Fetch a Jira project's issues, incrementally after the first run.
    Use --refresh for full history fetch"""
    cached = [] if force else (read_json(cache_file) or [])

    if cached:
        since = max(i["fields"]["updated"] for i in cached)
        since_dt = pd.to_datetime(since) - pd.Timedelta(days=1)  # small overlap buffer
        jql = f'project = {project} AND updated >= "{since_dt:%Y-%m-%d %H:%M}" ORDER BY updated ASC'
        since_label = f"{since_dt:%Y-%m-%d}"
    else:
        jql = f"project = {project} ORDER BY created ASC"
        since_label = "start"
    print(f"{label}: {len(cached):,} cached, since {since_label}")

    fetched = jira_search(session, base_url, jql, fields, page_size=page_size, on_issue=on_issue)
    print(f"{label}: {len(fetched):,} added")

    by_key = {i["key"]: i for i in cached}
    for issue in fetched:
        by_key[issue["key"]] = issue
    merged = list(by_key.values())

    write_json(cache_file, merged)
    return merged
