# Support Metrics Report

Includes nice bright colors, and _pie charts_.

[support-metrics](https://nesi.github.io/support-metrics/)

Report on RST work, Includes Jira tickets, GitHub/GitLab activity,
support docs, training, and software management.

Currently just running locally, but plan to schedule run on cluster.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`.

## Run

Each data source has a `fetch_x` script that updates the cache.
All use `--since`/`--until`/`--refresh`

`render_report.py` actually builds the report.

All default to "last full calendar month".

All fetchers cache what they pull and only fetch again when a request
exceeds what's already cached, never re-fetching data they already have:

- `fetch_jira_tickets.py`/`fetch_jira_training.py` keep a running cache
  of every ticket/event ever pulled.
- `fetch_github.py`/`fetch_gitlab.py` keep a similar running cache of raw
  dated events underneath.
- `fetch_modules.py` computes a diff between two single points in time
  rather than accumulating records over a range.

Pass `--refresh` to any fetcher to throw away its cache and rebuild from
scratch.

## Automation

```bash
./fetch_render.sh                        # last full calendar month
./fetch_render.sh 2026-07-01 2026-07-31  # explicit period
```

## Data

- Institution categorization relies on a `SUFFIX_TO_INSTITUTION`
  domain mapping (inherited from `../ticket_effort`). 
- GitHub fetch checks the rse group, but GitLab has no such group so a manual list is provided. 
  Has to be updated for new team members.
- All tickets raised by portal are assumed to be AgR
