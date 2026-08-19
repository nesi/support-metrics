"""
Run with --since/--until (YYYY-MM-DD), or no args for last full calendar
month.
"""

import argparse
import json
import os
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from fetch_common import add_ARGS, read_json, resolve_period, write_json

load_dotenv()

GITLAB_BASE_URL = os.environ["GITLAB_BASE_URL"].rstrip("/")
GITLAB_TOKEN = os.environ["GITLAB_TOKEN"]
GROUP = "nesi1"

RST_TEAM = ["anthony.shaw", "andre.geldenhuis", "CallumWalley", "geoffreyweal", "greg.hall2", "jennifer.reeve", "lbrick", "mattbixley", "vicky.fan", "peter.maxwell", "WesHarrell"]

BACKFILL_DAYS = 730  # history seeded on first fetch

EVENTS_CACHE_FILE = Path("data/gitlab_events_cache.json")

_session = requests.Session()
_session.headers.update({"Authorization": f"Bearer {GITLAB_TOKEN}"})
_retry = Retry(total=3, connect=3, read=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
_session.mount("https://", HTTPAdapter(max_retries=_retry))


def result_file(since, until):
    return Path(f"data/gitlab_activity_{since}_{until}.json")


def _paginated(url, params):
    page = 1
    while True:
        resp = _session.get(url, params={**params, "per_page": 100, "page": page}, timeout=60)
        resp.raise_for_status()
        batch = resp.json()
        yield from batch
        if not resp.headers.get("x-next-page"):
            break
        page += 1


def group_projects():
    return list(_paginated(
        f"{GITLAB_BASE_URL}/api/v4/groups/{GROUP}/projects",
        {"include_subgroups": "true", "archived": "false"},
    ))


def user_id(username):
    resp = _session.get(f"{GITLAB_BASE_URL}/api/v4/users", params={"username": username}, timeout=30)
    resp.raise_for_status()
    users = resp.json()
    if not users:
        raise ValueError(f"No GitLab user found for username {username!r}")
    return users[0]["id"]


def member_events(uid, since, until):
    # after/before are exclusive, so widen by a day each side for an inclusive range
    after = str(since - timedelta(days=1))
    before = str(until + timedelta(days=1))
    return _paginated(f"{GITLAB_BASE_URL}/api/v4/users/{uid}/events", {"after": after, "before": before})


def fetch_range(since, until, members, project_names):
    """The actual API work, for one date range and member list."""
    events = []
    for username in members:
        uid = user_id(username)
        for event in member_events(uid, since, until):
            name = project_names.get(event["project_id"])
            if name is None:
                continue
            event_date = event["created_at"][:10]
            if "push_data" in event:
                n = event["push_data"]["commit_count"]
                events.append({"date": event_date, "project": name, "kind": "commit", "n": n})
            elif event.get("target_type") == "MergeRequest" and event["action_name"] == "accepted":
                events.append({"date": event_date, "project": name, "kind": "mr", "n": 1})
            elif event.get("target_type") == "Issue" and event["action_name"] == "opened":
                events.append({"date": event_date, "project": name, "kind": "issue", "n": 1})
    return events


def ensure_range_cached(since, until, force=False):
    """Top up the raw event log to cover [since, until]"""
    cache = None if force else read_json(EVENTS_CACHE_FILE)

    if cache is None:
        project_names = {p["id"]: p["path_with_namespace"] for p in group_projects()}
        backfill_since = min(since, until - timedelta(days=BACKFILL_DAYS))
        print(f"events: 0 cached, since {backfill_since}")
        events = fetch_range(backfill_since, until, RST_TEAM, project_names)
        print(f"events: {len(events):,} added")
        cache = {"fetched_since": str(backfill_since), "fetched_until": str(until), "events": events}
        write_json(EVENTS_CACHE_FILE, cache)
        return cache

    fetched_since = date.fromisoformat(cache["fetched_since"])
    fetched_until = date.fromisoformat(cache["fetched_until"])

    print(f"events: {len(cache['events']):,} cached, since {fetched_since}")

    if until > fetched_until:
        project_names = {p["id"]: p["path_with_namespace"] for p in group_projects()}
        events = fetch_range(fetched_until + timedelta(days=1), until, RST_TEAM, project_names)
        print(f"events: {len(events):,} added")
        cache["events"].extend(events)
        cache["fetched_until"] = str(until)
        write_json(EVENTS_CACHE_FILE, cache)

    return cache


def aggregate(cache, since, until):
    since_s, until_s = str(since), str(until)
    by_repo = Counter()
    for e in cache["events"]:
        if since_s <= e["date"] <= until_s:
            by_repo[e["project"]] += e["n"]
    return {
        "period": {"since": since_s, "until": until_s},
        "team": RST_TEAM,
        "by_repo": dict(by_repo.most_common()),
    }


def fetch(since, until, force=False):
    cache = ensure_range_cached(since, until, force=force)
    return aggregate(cache, since, until)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    add_ARGS(parser, "Rebuild the raw event log from scratch")
    args = parser.parse_args()
    since, until = resolve_period(args)

    out_path = result_file(since, until)
    result = None if args.refresh else read_json(out_path)
    if result is not None:
        print(f"activity: loaded from cache ({since}..{until})")
    else:
        result = fetch(since, until, force=args.refresh)
        write_json(out_path, result)
