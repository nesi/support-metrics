"""
Fetch training-workshop events from Jira project TRNG.
Simplified version of Lai Kei

Pass --refresh to rebuild from scratch.
"""

import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from fetch_common import fetch_incremental, jira_session, parse_refresh_arg

load_dotenv()

JIRA_BASE_URL = os.environ["JIRA_BASE_URL"].rstrip("/")
JIRA_USER = os.environ["JIRA_USER"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]

PROJECT = "TRNG"
CACHE_FILE = Path("data/training_cache.json")

FIELD_MAP = {
    "start_date": "customfield_11456",
    "hours_coordination": "customfield_11460",
    "hours_instruction": "customfield_11461",
    "hours_support": "customfield_11462",
    "hours_material_prep": "customfield_11463",
    "attendees": "customfield_11469",
}

_session = jira_session(JIRA_BASE_URL, JIRA_USER, JIRA_API_TOKEN)


def _field(fields, key):
    """Single-select fields nest their value under .value; everything else is a plain scalar."""
    value = fields.get(FIELD_MAP[key])
    if isinstance(value, dict):
        return value.get("value")
    return value


def parse_issues(raw_issues):
    rows = []
    for raw in raw_issues:
        f = raw["fields"]
        row = {"key": raw["key"], "summary": f.get("summary")}
        row.update({name: _field(f, name) for name in FIELD_MAP})
        rows.append(row)

    df = pd.DataFrame(rows)
    for hour_col in ["hours_coordination", "hours_instruction", "hours_support", "hours_material_prep"]:
        df[hour_col] = pd.to_numeric(df[hour_col], errors="coerce").fillna(0)
    df["total_hours"] = (
        df["hours_coordination"] + df["hours_instruction"]
        + df["hours_support"] + df["hours_material_prep"]
    )
    return df


if __name__ == "__main__":
    force = parse_refresh_arg("Rebuild the cache from scratch")

    raw_issues = fetch_incremental(
        _session, JIRA_BASE_URL, PROJECT,
        fields=["summary", "updated"] + list(FIELD_MAP.values()),
        cache_file=CACHE_FILE, force=force, label="events",
    )
    df = parse_issues(raw_issues)
    Path("data").mkdir(exist_ok=True)
    df.to_csv("data/training.csv", index=False)
