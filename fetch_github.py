"""
Fetch GitHub activity for the researcher-support team.

Run with --since/--until (YYYY-MM-DD), or no args for last full calendar
month.
"""

import argparse
import json
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import requests

from fetch_common import add_ARGS, github_token, read_json, resolve_period, write_json

ORGS = ["nesi", "GenomicsAotearoa", "AgResearch"]
DOCS_REPO = "nesi/support-docs"
TEAM_MEMBERS = "nesi/teams/researcher-support"
EXTRA_MEMBERS = ["nesi-mkdocs-bot"]  # not a human team member, but should count

SEARCH_DELAY_SECONDS = 3  # search API: 30 req/min even authenticated
PAGE_CAP = 10  # search API: 1000 results/query cap

BACKFILL_DAYS = 730  # history seeded on first fetch

EVENTS_CACHE_FILE = Path("data/github_events_cache.json")


def result_file(since, until):
    return Path(f"data/github_activity_{since}_{until}.json")


_session = requests.Session()
_session.headers.update({
    "Authorization": f"Bearer {github_token()}",
    "Accept": "application/vnd.github+json",
})


def _get(url, params=None):
    resp = _session.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _search(endpoint, query):
    """Page through a search query ("commits" or "issues"), returning raw items."""
    items = []
    page = 1
    while page <= PAGE_CAP:
        time.sleep(SEARCH_DELAY_SECONDS)
        resp = _session.get(
            f"https://api.github.com/search/{endpoint}",
            params={"q": query, "per_page": 100, "page": page},
        )
        if resp.status_code == 422:
            break
        resp.raise_for_status()
        batch = resp.json()["items"]
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def _commit_repo(item):
    return item["repository"]["full_name"]


def _commit_date(item):
    return item["commit"]["committer"]["date"]


def _issue_repo(item):
    return "/".join(item["repository_url"].split("/")[-2:])


def _issue_date(item):
    return item["closed_at"] or item["created_at"]  # closed_at when merged, created_at for open issues


def team_members():
    members = _get(f"https://api.github.com/orgs/{TEAM_MEMBERS}/members")
    logins = [m["login"] for m in members]
    return logins + EXTRA_MEMBERS


def _get_all_pages(url, params=None):
    """Page through a REST (non-search) endpoint, returning all items."""
    items = []
    page = 1
    params = dict(params or {}, per_page=100)
    while True:
        params["page"] = page
        batch = _get(url, params=params)
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def docs_events(since, until):
    """Raw dated commit/PR/issue events on the docs repo, from any contributor
    (not just team members), including new markdown pages added per commit."""
    events = []
    commits = _get_all_pages(
        f"https://api.github.com/repos/{DOCS_REPO}/commits",
        params={
            "since": f"{since}T00:00:00Z",
            "until": f"{until}T23:59:59Z",
        },
    )
    for c in commits:
        detail = _get(f"https://api.github.com/repos/{DOCS_REPO}/commits/{c['sha']}")
        new_pages = [
            file["filename"] for file in detail.get("files", [])
            if file["status"] == "added" and file["filename"].endswith(".md")
        ]
        events.append({
            "date": c["commit"]["committer"]["date"][:10],
            "kind": "commit",
            "new_pages": new_pages,
        })

    for item in _search(
        "issues",
        f"repo:{DOCS_REPO} is:pr is:merged merged:{since}..{until}",
    ):
        events.append({"date": _issue_date(item)[:10], "kind": "pr_merged"})

    for item in _search(
        "issues",
        f"repo:{DOCS_REPO} is:issue created:{since}..{until}",
    ):
        events.append({"date": _issue_date(item)[:10], "kind": "issue_opened"})

    for item in _search(
        "issues",
        f"repo:{DOCS_REPO} is:issue closed:{since}..{until}",
    ):
        events.append({"date": item["closed_at"][:10], "kind": "issue_closed"})

    return events


def other_events(login, since, until):
    # Exclude docs
    exclude = f"-repo:{DOCS_REPO}"
    events = []
    for org in ORGS:
        for item in _search("commits", f"org:{org} {exclude} author:{login} author-date:{since}..{until}"):
            events.append({"login": login, "date": _commit_date(item)[:10], "kind": "commit", "repo": _commit_repo(item)})
        for item in _search("issues", f"org:{org} {exclude} is:pr is:merged author:{login} merged:{since}..{until}"):
            events.append({"login": login, "date": _issue_date(item)[:10], "kind": "pr_merged", "repo": _issue_repo(item)})
        for item in _search("issues", f"org:{org} {exclude} is:issue author:{login} created:{since}..{until}"):
            events.append({"login": login, "date": _issue_date(item)[:10], "kind": "issue", "repo": _issue_repo(item)})
    return events


def fetch_range(since, until, members):
    """The actual API work, for one date range and member list."""
    docs = docs_events(since, until)
    other = []
    for login in members:
        other.extend(other_events(login, since, until))
    return docs, other


def ensure_range_cached(since, until, force=False):
    """Top up the raw event log to cover [since, until]."""
    cache = None if force else read_json(EVENTS_CACHE_FILE)

    if cache is None:
        members = team_members()
        backfill_since = min(since, until - timedelta(days=BACKFILL_DAYS))
        print(f"events: 0 cached, since {backfill_since}")
        docs, other = fetch_range(backfill_since, until, members)
        print(f"events: {len(docs) + len(other):,} added")
        cache = {
            "fetched_since": str(backfill_since),
            "fetched_until": str(until),
            "team_members": members,
            "docs_events": docs,
            "other_events": other,
        }
        write_json(EVENTS_CACHE_FILE, cache)
        return cache

    fetched_since = date.fromisoformat(cache["fetched_since"])
    fetched_until = date.fromisoformat(cache["fetched_until"])

    print(f"events: {len(cache['docs_events']) + len(cache['other_events']):,} cached, since {fetched_since}")

    if until > fetched_until:
        members = team_members()
        docs, other = fetch_range(fetched_until + timedelta(days=1), until, members)
        print(f"events: {len(docs) + len(other):,} added")
        cache["docs_events"].extend(docs)
        cache["other_events"].extend(other)
        cache["fetched_until"] = str(until)
        cache["team_members"] = members
        write_json(EVENTS_CACHE_FILE, cache)

    return cache


def aggregate(cache, since, until):
    since_s, until_s = str(since), str(until)

    docs = {"commits": 0, "prs_merged": 0, "issues_opened": 0, "issues_closed": 0, "new_pages": []}
    for e in cache["docs_events"]:
        if not (since_s <= e["date"] <= until_s):
            continue
        if e["kind"] == "commit":
            docs["commits"] += 1
            docs["new_pages"].extend(e.get("new_pages", []))
        elif e["kind"] == "pr_merged":
            docs["prs_merged"] += 1
        elif e["kind"] == "issue_opened":
            docs["issues_opened"] += 1
        elif e["kind"] == "issue_closed":
            docs["issues_closed"] += 1
    docs["new_pages"] = sorted(set(docs["new_pages"]))

    other_repo_counts = Counter()
    for e in cache["other_events"]:
        if since_s <= e["date"] <= until_s:
            other_repo_counts[e["repo"]] += 1

    return {
        "period": {"since": since_s, "until": until_s},
        "team_size": len(cache["team_members"]),
        "support_docs": docs,
        "other_contributions": {"by_repo": dict(other_repo_counts.most_common())},
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
