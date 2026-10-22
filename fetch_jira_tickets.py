"""
Fetch FS project tickets for the support metrics report.

Institution is resolved from the reporter's JSM organization membership
(GET /rest/servicedeskapi/organization?accountId=...), which is populated
per-customer independently of any per-ticket field and works even when the
reporter's email is hidden (e.g. AgResearch eRI accounts). Falls back to
reporter-email-domain matching for reporters not (yet) linked to a known
org, which also covers Commercial customers -- JSM has no org record for
an arbitrary commercial domain.
Logic copied from ../ticket_effort/{fetch_issues,checks}.py.

--refresh rebuilds all caches from scratch.
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
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
REPORTER_ORG_CACHE_FILE = Path("data/reporter_orgs_cache.json")
REPORTER_ORG_WORKERS = 10

FIELDS = [
    "reporter",
    "created",
    "updated",
    "resolutiondate",
    "comment",
    "customfield_10800",  # Operational categorization (category / subcategory)
    "customfield_11475",  # Effort (Low / Medium / High, set manually)
]

# Maps a JSM organization id to (institution, category). Copied from
# ../ticket_effort/checks.py's ORG_ID_MAP, scoped to orgs linked to the FS
# service desk. Matched by id rather than the org record's own "name" --
# that name doesn't always match our canonical institution names (e.g. id
# 409's JSM name is "The University of Auckland"), and the site-wide org
# list also has unrelated/duplicate records we want to ignore.
ORG_ID_TO_INSTITUTION = {
    "409": ("University of Auckland", "Tertiary"),
    "410": ("AUT", "Tertiary"),
    "389": ("University of Canterbury", "Tertiary"),
    "586": ("Lincoln University", "Tertiary"),
    "392": ("Massey University", "Tertiary"),
    "386": ("University of Otago", "Tertiary"),
    "387": ("Victoria University of Wellington", "Tertiary"),
    "384": ("University of Waikato", "Tertiary"),
    "598": ("Toi Ohomai", "Tertiary"),
    "575": ("Otago Polytechnic", "Tertiary"),
    "562": ("NMIT", "Tertiary"),
    "497": ("Ara", "Tertiary"),
    "622": ("WelTec / Whitireia", "Tertiary"),
    "370": ("NeSI (internal)", "Internal"),
    "379": ("REANNZ (internal)", "Internal"),
    "380": ("Landcare Research", "PRO"),
    "375": ("NIWA", "PRO"),
    "374": ("AgResearch", "PRO"),
    "376": ("GNS Science", "PRO"),
    "620": ("Scion", "PRO"),
    "398": ("Plant & Food Research", "PRO"),
    "390": ("LIC", "PRO"),
    "377": ("Pacific Community (SPC)", "PRO"),
    "413": ("MPI", "PRO"),
    "671": ("Earth Sciences New Zealand", "PRO"),
    "530": ("ESR", "PRO"),
    "507": ("Cawthron Institute", "PRO"),
    "672": ("Bodeker Scientific", "PRO"),
    "673": ("MATAI Medical Research Institute", "PRO"),
}

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

def resolve_institution(email, org_ids):
    """(name, category) from the reporter's JSM organization membership,
    falling back to their email domain when they're not linked to a known
    org. (None, None) for internal staff and noise domains/orgs."""
    for org_id in org_ids or []:
        match = ORG_ID_TO_INSTITUTION.get(org_id)
        if match:
            name, category = match
            return (None, None) if category == "Internal" else (name, category)

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


def _fetch_reporter_org_ids(account_id):
    """Return (account_id, [org_id, ...]) for a customer's JSM org membership."""
    resp = _session.get(
        f"{JIRA_BASE_URL}/rest/servicedeskapi/organization",
        params={"accountId": account_id},
        timeout=30,
    )
    if resp.status_code == 404:  # deleted/unknown customer -- fall back to email domain
        return account_id, []
    resp.raise_for_status()
    return account_id, [org["id"] for org in resp.json()["values"]]


def fetch_reporter_orgs(account_ids, force=False):
    """Org ids per reporter accountId, cached -- org membership rarely changes."""
    cache = {} if force else (read_json(REPORTER_ORG_CACHE_FILE) or {})
    missing = [a for a in account_ids if a not in cache]

    print(f"reporter orgs: {len(cache):,} cached, {len(missing):,} to fetch")
    if missing:
        with ThreadPoolExecutor(max_workers=REPORTER_ORG_WORKERS) as pool:
            futures = [pool.submit(_fetch_reporter_org_ids, a) for a in missing]
            for fut in as_completed(futures):
                account_id, org_ids = fut.result()
                cache[account_id] = org_ids

    write_json(REPORTER_ORG_CACHE_FILE, cache)
    return cache


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


def parse_issues(raw_issues, csat_by_key, reporter_orgs):
    rows = []
    for raw in raw_issues:
        f = raw["fields"]
        key = raw["key"]
        reporter = f.get("reporter") or {}
        reporter_email = reporter.get("emailAddress")
        org_ids = reporter_orgs.get(reporter.get("accountId"))
        institution, institution_category = resolve_institution(reporter_email, org_ids)

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

    account_ids = {
        i["fields"]["reporter"].get("accountId")
        for i in raw_issues
        if i["fields"].get("reporter")
    } - {None}
    reporter_orgs = fetch_reporter_orgs(account_ids, force=force)

    df = parse_issues(raw_issues, csat, reporter_orgs)
    Path("data").mkdir(exist_ok=True)
    df.to_csv("data/tickets.csv", index=False)
