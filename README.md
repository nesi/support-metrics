# Support Metrics Report

[support-metrics](https://nesi.github.io/support-metrics/)

Report on RST work, Includes Jira tickets, GitHub/GitLab activity,
support docs, training, and software management.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:

## Run

Each data source has a `fetch_x` script that updates the cache.
All use `--since`/`--until`/`--refresh`

`render_report.py` actually builds the report.

All default to "last full calendar month".

All fetchers cache what they pull and only fetch again when a request
exceeds what's already cached, never re-fetching data they already have:

- `fetch_jira_tickets.py`/`fetch_jira_training.py` keep a running cache
  of every ticket/event ever pulled, topped up by "updated since the
  newest cached timestamp" on each run.
- `fetch_github.py`/`fetch_gitlab.py` keep a similar running cache of raw
  dated events underneath, seeded with 2 years of history on the first
  run and topped up forward-only as later periods are requested; the
  requested period is then aggregated from that cache. They also write a
  per-period result file, so re-running for an exact period already
  rendered skips straight to that file.
- `fetch_modules.py` computes a diff between two single points in time
  rather than accumulating records over a range, so there's no partial
  overlap to reuse — it caches per exact period only.

Pass `--refresh` to any fetcher to throw away its cache and rebuild from
scratch (e.g. after a query/field-mapping change, or a team roster
change for the GitHub/GitLab fetchers). `render_report.py` errors out if
the month it's asked to render hasn't been fetched yet.

The rendered report is a single self-contained HTML file (Plotly
charts + one embedded PNG for the CSAT word cloud) — open it directly
in a browser, no server needed.

## Automation

```bash
./fetch_render.sh                        # last full calendar month
./fetch_render.sh 2026-07-01 2026-07-31  # explicit period
```

Runs via a cron entry at 6am on the 2nd of each month (installed
directly with `crontab -e`, not tracked in this repo; output goes to
`data/run.log`). It fetches, renders, copies the result to
`public/main.html`, and pushes — that push triggers
`.github/workflows/deploy.yml`, which publishes `public/main.html` to
GitHub Pages. `data/` and `report/` stay local-only.

**Not live yet**: no GitHub remote configured, and Pages isn't enabled
(Settings → Pages → Source: GitHub Actions). Also worth deciding
deliberately: GitHub Pages sites are publicly reachable even from a
private repo (unless your plan has Enterprise Pages visibility
restrictions), and this report includes institution-level ticket
breakdowns and CSAT free-text feedback.

## Known data quirks

- **Effort** (`customfield_11475`) is manually set and sparse (~5-6%
  of tickets/month). The chart shows "Unset" as its own bar rather
  than hiding it.
- **Category** (`customfield_10800`) is set on ~half of tickets;
  "Admin" dominates. Unlike `../ticket_effort`, this report doesn't
  exclude Admin/Partnerships — the sunburst shows the real shape of
  the data.
- **CSAT** is heavily skewed toward 5 stars; only ~16% of `csat_sent`
  tickets get a response.
- **SLAs** aren't configured on this JSM project, so time-to-first-
  response uses the first comment's timestamp instead.
- The **CSAT feedback endpoint**
  (`/rest/servicedeskapi/request/{key}/feedback`) is an Atlassian
  experimental API (needs `X-ExperimentalApi: opt-in`) and could
  change without notice.
- **Institution categorization** relies on a `SUFFIX_TO_INSTITUTION`
  domain mapping (inherited from `../ticket_effort`, not audited
  here). Known gap: `wintec.ac.nz` isn't mapped, so it lands in
  Commercial instead of Tertiary.
</content>
