"""
Fetch FS project tickets for the support metrics report.
Institution-from-email logic copied from ../ticket_effort/fetch_issues.py.

--refresh rebuilds both caches from scratch.
"""

import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from fetch_common import fetch_incremental, jira_session, parse_refresh_arg, read_json, write_json

load_dotenv()

JIRA_BASE_URL = os.environ["JIRA_BASE_URL"].rstrip("/")
JIRA_USER = os.environ["JIRA_USER"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]

PROJECT = "FS"
CACHE_FILE = Path("data/tickets_cache.json")
CSAT_CACHE_FILE = Path("data/csat_cache.json")
CSAT_PAGE_SIZE = 100
CSAT_OVERLAP_DAYS = 3 

FIELDS = [
    "reporter",
    "created",
    "updated",
    "resolutiondate",
    "comment",
    "customfield_10800",  # Operational categorization (category / subcategory)
    "customfield_11475",  # Effort (Low / Medium / High, set manually)
]

# Copied from ../ticket_effort/fetch_issues.py. Category is attached here
# since it's already known at match time.
SUFFIX_TERTIARY = [
    ("auckland.ac.nz",     "University of Auckland"),
    ("aucklanduni.ac.nz",  "University of Auckland"),
    ("aut.ac.nz",          "AUT"),
    ("autuni.ac.nz",       "AUT"),
    ("canterbury.ac.nz",   "University of Canterbury"),
    ("uclive.ac.nz",       "University of Canterbury"),
    ("lincoln.ac.nz",      "Lincoln University"),
    ("lincolnuni.ac.nz",   "Lincoln University"),
    ("massey.ac.nz",       "Massey University"),
    ("otago.ac.nz",        "University of Otago"),
    ("vuw.ac.nz",          "Victoria University of Wellington"),
    ("myvuw.ac.nz",        "Victoria University of Wellington"),
    ("waikato.ac.nz",      "University of Waikato"),
    ("op.ac.nz",           "Otago Polytechnic"),
    ("toiohomai.ac.nz",    "Toi Ohomai"),
    ("nmit.ac.nz",         "NMIT"),
    ("ara.ac.nz",          "Ara"),
    ("weltec.ac.nz",       "WelTec / Whitireia"),
    ("wintec.ac.nz",       "WinTec"),
    ("whitireia.ac.nz",    "WelTec / Whitireia"),
]
SUFFIX_PRO = [
    ("landcareresearch.co.nz", "Landcare Research"),
    ("niwa.co.nz",             "NIWA"),
    ("agresearch.co.nz",       "AgResearch"),
    ("gns.cri.nz",             "GNS Science"),
    ("scionresearch.com",      "Scion"),
    ("plantandfood.co.nz",     "Plant & Food Research"),
]
# Generic providers, bots, and NeSI/REANNZ staff — excluded from
# institution reporting entirely.
SUFFIX_NOISE = {
    "gmail.com", "google.com", "googlemail.com", "yahoo.com", "yahoo.com.tw",
    "hotmail.com", "hotmail.co.uk", "outlook.com", "live.com", "live.se",
    "live.co.za", "me.com", "icloud.com",
    "groups.globus.org", "globus.org",
    "mg.shodan.io", "10times.com", "business.lenovo.com",
    "docker.com", "github.com", "e.atlassian.com",
    "googlegroups.com", "em-s.dropbox.com", "mandrill.com",
    "nznesi.atlassian.net", "scival.com",
    "nesi.org.nz", "reannz.co.nz", "reannz.org.nz",
}

# Longest-suffix-first so a more specific suffix wins. Commercial has no
# list of its own — it's whatever doesn't match Tertiary/PRO.
_SUFFIX_LOOKUP = sorted(
    [(suffix, name, "Tertiary") for suffix, name in SUFFIX_TERTIARY]
    + [(suffix, name, "PRO") for suffix, name in SUFFIX_PRO],
    key=lambda x: len(x[0]), reverse=True,
)

# Reporter accounts on the AgResearch eRI have their emailAddress hidden by
# Jira's privacy settings (accountType "atlassian" with emailAddress: null).
# Real customer accounts always have an email (see resolve_institution), so
# this combination only shows up for AgResearch staff and associates.


def resolve_institution(email):
    """(name, category) from a reporter's email domain; (None, None) for noise."""
    if not email:
        return None, None
    domain = email.lower().split("@")[-1]
    if domain in SUFFIX_NOISE:
        return None, None
    for suffix, name, category in _SUFFIX_LOOKUP:
        if domain == suffix or domain.endswith("." + suffix):
            return name, category
    return domain, "Commercial"


_session = jira_session(JIRA_BASE_URL, JIRA_USER, JIRA_API_TOKEN)


def _strip_comment_bodies(issue):
    """Drop comment bodies."""
    comments = issue["fields"].get("comment", {}).get("comments", [])
    issue["fields"]["comment"] = {
        "comments": [{"created": c["created"]} for c in comments]
    }


def _fetch_csat_page(start_date, end_date, start):
    resp = _session.get(
        f"{JIRA_BASE_URL}/rest/servicedesk/1/projects/{PROJECT}/report/feedback/date-range",
        params={
            "start": start, "limit": CSAT_PAGE_SIZE, "expand": "overall",
            "startDate": f"{start_date:%Y-%m-%d}", "endDate": f"{end_date:%Y-%m-%d}",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["pagedResults"]


def fetch_csat(earliest_date, force=False):
    """Bulk CSAT fetch by date range, keyed by issue."""
    cache = {} if force else (read_json(CSAT_CACHE_FILE) or {})
    ratings = cache.get("ratings", {})

    today = pd.Timestamp.now(tz="UTC").date()
    if cache.get("fetched_through"):
        start_date = pd.to_datetime(cache["fetched_through"]).date() - pd.Timedelta(days=CSAT_OVERLAP_DAYS)
    else:
        start_date = pd.to_datetime(earliest_date).date()

    print(f"csat: {len(ratings):,} cached, since {start_date}")

    # API caps a single call at 366 days.
    fetched = 0
    chunk_start = start_date
    while chunk_start <= today:
        chunk_end = min(chunk_start + pd.Timedelta(days=365), today)
        start = 0
        while True:
            paged = _fetch_csat_page(chunk_start, chunk_end, start)
            for entry in paged["results"]:
                ratings[entry["issueKey"]] = {
                    "rating": entry["rating"],
                    "comment": entry["comment"] or None,
                }
            fetched += len(paged["results"])
            start += CSAT_PAGE_SIZE
            if start >= paged["size"]:
                break
        chunk_start = chunk_end + pd.Timedelta(days=1)

    print(f"csat: {fetched:,} added")
    write_json(CSAT_CACHE_FILE, {"fetched_through": f"{today}", "ratings": ratings})
    return ratings


def parse_issues(raw_issues, csat_by_key):
    # Some reporters have a null emailAddress on part of their tickets (Jira
    # privacy setting) but a visible NeSI/REANNZ email elsewhere — those are
    # staff, not AgResearch, so the blanket rule below must skip them.
    staff_names = {
        (f.get("reporter") or {}).get("displayName")
        for raw in raw_issues
        for f in [raw["fields"]]
        if ((f.get("reporter") or {}).get("emailAddress") or "").lower().split("@")[-1]
        in {"nesi.org.nz", "reannz.co.nz", "reannz.org.nz"}
    }

    rows = []
    for raw in raw_issues:
        f = raw["fields"]
        key = raw["key"]
        reporter = f.get("reporter") or {}
        reporter_email = reporter.get("emailAddress")
        institution, institution_category = resolve_institution(reporter_email)
        if (
            reporter_email is None
            and reporter.get("accountType") == "atlassian"
            and reporter.get("displayName") not in staff_names
        ):
            institution, institution_category = "AgResearch", "PRO"

        created = pd.to_datetime(f.get("created"), utc=True)
        resolved = pd.to_datetime(f.get("resolutiondate"), utc=True)

        comments = f.get("comment", {}).get("comments", [])
        first_comment_at = (
            pd.to_datetime(min(c["created"] for c in comments), utc=True) if comments else pd.NaT
        )
        time_to_first_response_hours = (
            (first_comment_at - created).total_seconds() / 3600
            if pd.notna(first_comment_at) else None
        )

        category_field = f.get("customfield_10800") or {}
        effort_field = f.get("customfield_11475") or {}

        rows.append({
            "key": key,
            "institution": institution,
            "institution_category": institution_category,
            "created": created,
            "resolution_days": (resolved - created).days if pd.notna(resolved) else None,
            "time_to_first_response_hours": time_to_first_response_hours,
            "category": category_field.get("value"),
            "subcategory": (category_field.get("child") or {}).get("value"),
            "effort": effort_field.get("value"),
            "csat_rating": csat_by_key.get(key, {}).get("rating"),
            "csat_comment": csat_by_key.get(key, {}).get("comment"),
        })

    return pd.DataFrame(rows)


if __name__ == "__main__":
    force = parse_refresh_arg("Rebuild the caches from scratch")

    raw_issues = fetch_incremental(
        _session, JIRA_BASE_URL, PROJECT, fields=FIELDS,
        cache_file=CACHE_FILE, force=force, on_issue=_strip_comment_bodies, label="tickets",
    )

    earliest_date = min(i["fields"]["created"] for i in raw_issues)
    csat = fetch_csat(earliest_date, force=force)

    df = parse_issues(raw_issues, csat)
    Path("data").mkdir(exist_ok=True)
    df.to_csv("data/tickets.csv", index=False)
