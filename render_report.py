"""
Assemble a single HTML report for one calendar month.

Charts are Plotly; plotly.js is loaded from a CDN in <head> rather than
inlined, to keep the report file small.

Run after the fetch scripst have produced their files.
"""

import argparse
import base64
import colorsys
import io
import json
import string
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from wordcloud import WordCloud, STOPWORDS

DOCS_REPO_URL = "https://github.com/nesi/support-docs/blob/main"
TEMPLATE_PATH = Path(__file__).parent / "templates" / "report.html"

GRAY_UNSET = "#ABB0AC"
GRID_COLOR = "#E5E7E3"
INK = "#1E2422"
FONT_FAMILY = "-apple-system, Segoe UI, sans-serif"
LIST_PREVIEW_LIMIT = 8

# One accent per section — used for pill labels, the section heading's
# accent bar, and every chart in that section (see shade_ramp).
SECTION_COLOR = {
    "tickets": "#2a78d6",
    "docs": "#eb6834",
    "other": "#1baf7a",
    "training": "#4a3aa7",
    "builds": "#008300",
}

RESPONSE_HOURS_OUTLIER_CAP = 48       # 2 days
SOLVE_DAYS_OUTLIER_CAP = 30           # 1 month

HOUR_BUCKETS = [
    ("hours_material_prep", "Material prep"),
    ("hours_coordination", "Coordination"),
    ("hours_instruction", "Instruction"),
    ("hours_support", "Support"),
]

PLOTLY_CONFIG = {"displayModeBar": False, "responsive": True}


def _hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def shade_ramp(hex_color, n, hue_shift=0.2):
    """n pretty colors around hex_color"""
    if n <= 1:
        return [hex_color]
    r, g, b = _hex_to_rgb(hex_color)
    h, _, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    s = max(s, 0.35)
    shades = []
    for i in range(n):
        t = i / (n - 1)
        lightness = 0.78 - t * 0.42
        hue = (h + hue_shift * (t - 0.5)) % 1.0
        r, g, b = colorsys.hls_to_rgb(hue, lightness, s)
        shades.append(_rgb_to_hex((r * 255, g * 255, b * 255)))
    return shades


def style_layout(fig, orientation=None, height=340):
    fig.update_layout(
        font=dict(family=FONT_FAMILY, size=12, color=INK),
        plot_bgcolor="white",
        margin=dict(l=10, r=10, t=20, b=10),
        height=height,
        showlegend=False,
    )
    if orientation == "v":
        fig.update_xaxes(showgrid=False, zeroline=False)
        fig.update_yaxes(showgrid=True, gridcolor=GRID_COLOR, zeroline=False)
    elif orientation == "h":
        fig.update_xaxes(showgrid=True, gridcolor=GRID_COLOR, zeroline=False)
        fig.update_yaxes(showgrid=False, zeroline=False)
    return fig


def chart_html(fig):
    return fig.to_html(full_html=False, include_plotlyjs=False, config=PLOTLY_CONFIG)


def previous_month_range(today=None):
    today = today or date.today()
    first_of_this_month = today.replace(day=1)
    last_of_prev_month = first_of_this_month - timedelta(days=1)
    first_of_prev_month = last_of_prev_month.replace(day=1)
    return first_of_prev_month, last_of_prev_month


def period_label(month_start, month_end):
    if (month_start.year, month_start.month) == (month_end.year, month_end.month):
        return month_start.strftime("%B %Y")
    return f"{month_start.strftime('%d %b %Y')} – {month_end.strftime('%d %b %Y')}"


def mpl_fig_to_data_uri(fig):
    """For the one chart that isn't Plotly — the word cloud."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def load_tickets():
    df = pd.read_csv("data/tickets.csv")
    df["created"] = pd.to_datetime(df["created"], utc=True, format="mixed")
    return df


def load_training():
    df = pd.read_csv("data/training.csv")
    df["start_date"] = pd.to_datetime(df["start_date"], errors="coerce")
    return df


def _load_period_cache(prefix, since, until):
    """Load the fetch_github.py / fetch_modules.py cache for this period."""
    path = Path(f"data/{prefix}_{since}_{until}.json")
    return json.loads(path.read_text())


def load_github(since, until):
    return _load_period_cache("github_activity", since, until)


def load_modules(since, until):
    return _load_period_cache("modules_diff", since, until)


def load_gitlab(since, until):
    return _load_period_cache("gitlab_activity", since, until)


# --------------------------------------------------------------------------- #
# Tickets
# --------------------------------------------------------------------------- #

def chart_ticket_volume(df):
    monthly = df.set_index("created").resample("ME").size().tail(12)
    fig = go.Figure(go.Scatter(
        x=monthly.index, y=monthly.values, mode="lines+markers",
        line=dict(color=SECTION_COLOR["tickets"], width=1.5),
        marker=dict(size=6, color=SECTION_COLOR["tickets"]),
        fill="tozeroy", fillcolor="rgba(42,120,214,0.08)",
        hovertemplate="%{x|%b %Y}: %{y} tickets<extra></extra>",
    ))
    fig.update_xaxes(tickformat="%b %Y", dtick="M1")
    style_layout(fig, orientation="v")
    return "Ticket volume", chart_html(fig)


def _sunburst_fig(grouped, parent_col, child_col, count_col, base_hex):
    top_order = grouped[parent_col].drop_duplicates().tolist()
    top_totals = grouped.groupby(parent_col, sort=False)[count_col].sum()
    top_colors = dict(zip(top_order, shade_ramp(base_hex, len(top_order))))

    ids, labels, parent_ids, values, colors = [], [], [], [], []
    for parent in top_order:
        ids.append(parent)
        labels.append(parent)
        parent_ids.append("")
        values.append(top_totals[parent])
        colors.append(top_colors[parent])

        children = grouped[grouped[parent_col] == parent]
        child_colors = shade_ramp(top_colors[parent], len(children))
        for (_, row), child_color in zip(children.iterrows(), child_colors):
            ids.append(f"{parent}/{row[child_col]}")
            labels.append(row[child_col])
            parent_ids.append(parent)
            values.append(row[count_col])
            colors.append(child_color)

    fig = go.Figure(go.Sunburst(
        ids=ids, labels=labels, parents=parent_ids, values=values,
        branchvalues="total", marker=dict(colors=colors),
        hovertemplate="%{label}: %{value} tickets<extra></extra>",
    ))
    style_layout(fig, height=420)
    return fig


def chart_institutions(df, month_start, month_end):
    scoped = df[
        (df["created"] >= month_start) & (df["created"] <= month_end)
        & df["institution"].notna()
    ]
    grouped = scoped.groupby(["institution_category", "institution"]).size().reset_index(name="count")
    fig = _sunburst_fig(grouped, "institution_category", "institution", "count", SECTION_COLOR["tickets"])
    return "Tickets by institution", chart_html(fig)


def chart_category(df, month_start, month_end):
    """Does not exclude admin tickets"""
    scoped = df[(df["created"] >= month_start) & (df["created"] <= month_end)]
    core = scoped.copy()
    core["category"] = core["category"].fillna("Unset")
    core["subcategory"] = core["subcategory"].fillna("(general)")
    grouped = core.groupby(["category", "subcategory"]).size().reset_index(name="count")
    fig = _sunburst_fig(grouped, "category", "subcategory", "count", SECTION_COLOR["tickets"])
    return "Tickets by category", chart_html(fig)


RESPONSE_BUCKETS = [
    (0, 1 / 6, "<10 min"),
    (1 / 6, 0.5, "10-30 min"),
    (0.5, 2, "30 min-2 hr"),
    (2, 8, "2-8 hr"),
    (8, 24, "8-24 hr"),
    (24, 48, "1-2 days"),
]


def chart_response_distribution(df, month_start, month_end):
    scoped = df[
        (df["created"] >= month_start) & (df["created"] <= month_end)
        & (df["category"] != "Admin")
    ].copy()
    scoped["category"] = scoped["category"].fillna("Unset")
    scoped = scoped.dropna(subset=["time_to_first_response_hours"])
    scoped = scoped[scoped["time_to_first_response_hours"] <= RESPONSE_HOURS_OUTLIER_CAP]
    order = scoped.groupby("category")["time_to_first_response_hours"].median().sort_values().index.tolist()

    bucket_labels = [label for _, _, label in RESPONSE_BUCKETS]
    scoped["bucket"] = pd.cut(
        scoped["time_to_first_response_hours"],
        bins=[b[0] for b in RESPONSE_BUCKETS] + [RESPONSE_BUCKETS[-1][1]],
        labels=bucket_labels, right=True, include_lowest=True,
    )
    counts = scoped.groupby(["bucket", "category"], observed=True).size().reset_index(name="count")
    counts["pct"] = counts["count"] / len(scoped) * 100

    fig = px.bar(
        counts, x="bucket", y="pct", color="category", custom_data=["count"],
        category_orders={"bucket": bucket_labels, "category": order},
        color_discrete_sequence=shade_ramp(SECTION_COLOR["tickets"], len(order)),
    )
    fig.update_traces(
        marker_line_width=1, marker_line_color="white",
        hovertemplate="%{fullData.name}: %{customdata[0]} tickets (%{y:.0f}%)<extra></extra>",
    )
    fig.update_xaxes(title=None, showgrid=False, zeroline=False)
    fig.update_yaxes(title=None, showgrid=True, gridcolor=GRID_COLOR, zeroline=False, ticksuffix="%")
    style_layout(fig, orientation="v")
    return "First response time by category", chart_html(fig)


def chart_effort(df, month_start, month_end):
    scoped = df[(df["created"] >= month_start) & (df["created"] <= month_end)]
    order = ["Unset", "Low", "Medium", "High"]
    colors = [GRAY_UNSET] + shade_ramp(SECTION_COLOR["tickets"], 3)
    counts = scoped["effort"].fillna("Unset").value_counts().reindex(order).fillna(0)
    fig = go.Figure(go.Bar(
        x=order, y=counts.values, marker_color=colors,
        hovertemplate="%{x}: %{y} tickets<extra></extra>",
    ))
    style_layout(fig, orientation="v", height=300)
    return "Effort", chart_html(fig)


def chart_csat(df, month_start, month_end):
    scoped = df[(df["created"] >= month_start) & (df["created"] <= month_end)]
    colors = shade_ramp(SECTION_COLOR["tickets"], 5)
    counts = scoped["csat_rating"].value_counts().reindex([1, 2, 3, 4, 5]).fillna(0)
    fig = go.Figure(go.Bar(
        x=[1, 2, 3, 4, 5], y=counts.values, marker_color=colors,
        hovertemplate="%{x} star: %{y} responses<extra></extra>",
    ))
    fig.update_xaxes(tickvals=[1, 2, 3, 4, 5])
    style_layout(fig, orientation="v", height=300)
    return "CSAT ratings", chart_html(fig)


def _wordcloud_color_func(word, font_size, position, orientation, random_state=None, **kwargs):
    return random_state.choice(shade_ramp(SECTION_COLOR["tickets"], 6))


def chart_csat_wordcloud(df, month_start, month_end):
    scoped = df[(df["created"] >= month_start) & (df["created"] <= month_end)]
    text = " ".join(scoped["csat_comment"].dropna().astype(str)).strip()
    if not text:
        return None

    wc = WordCloud(
        width=1000, height=420, background_color="white",
        color_func=_wordcloud_color_func, stopwords=STOPWORDS, max_words=50,
    ).generate(text)
    fig, ax = plt.subplots(figsize=(7, 3))
    ax.imshow(wc, interpolation="bilinear")
    ax.axis("off")
    fig.tight_layout(pad=0)
    return "CSAT feedback", mpl_fig_to_data_uri(fig)


# --------------------------------------------------------------------------- #
# Docs
# --------------------------------------------------------------------------- #

def _clean_page_name(path):
    name = path.rsplit("/", 1)[-1]
    name = name[:-3] if name.endswith(".md") else name
    return name.replace("_", " ")


def _collapsible_list(li_items):
    """Render <li> strings as a page-list, folding anything past
    LIST_PREVIEW_LIMIT into a native <details> accordion."""
    visible, hidden = li_items[:LIST_PREVIEW_LIMIT], li_items[LIST_PREVIEW_LIMIT:]
    html = f'<ul class="page-list">{"".join(visible)}</ul>'
    if hidden:
        html += (
            f'<details class="show-more"><summary>Show {len(hidden)} more</summary>'
            f'<ul class="page-list">{"".join(hidden)}</ul></details>'
        )
    return html


def new_pages_html(new_pages):
    color = SECTION_COLOR["docs"]
    items = [
        f'<li><a href="{DOCS_REPO_URL}/{path}" style="color:{color}">{_clean_page_name(path)}</a></li>'
        for path in new_pages
    ]
    return f'<div class="chart-section">{card_header("New pages added", color)}{_collapsible_list(items)}</div>'


# --------------------------------------------------------------------------- #
# Other Git
# --------------------------------------------------------------------------- #

def chart_repo_activity(activity):
    by_repo = activity["other_contributions"]["by_repo"]
    series = pd.Series(by_repo).sort_values(ascending=False).head(20).sort_values()
    labels = [name.split("/", 1)[-1] for name in series.index]

    fig = go.Figure(go.Bar(
        x=series.values, y=labels, orientation="h",
        marker_color=shade_ramp(SECTION_COLOR["other"], len(labels)),
        text=[f"{v:,}" for v in series.values], textposition="outside",
        hovertemplate="%{y}: %{x}<extra></extra>",
    ))
    fig.update_yaxes(categoryorder="array", categoryarray=labels)
    fig.update_xaxes(type="log")
    style_layout(fig, orientation="h", height=380)
    return "Activity by repo", chart_html(fig)


# --------------------------------------------------------------------------- #
# Training
# --------------------------------------------------------------------------- #



def chart_training_hours(training_df, month_start, month_end):
    scoped = training_df[
        (training_df["start_date"] >= month_start) & (training_df["start_date"] <= month_end)
    ]
    labels = [label for _, label in HOUR_BUCKETS]
    values = [scoped[col].sum() for col, _ in HOUR_BUCKETS]
    fig = go.Figure(go.Bar(
        x=labels, y=values, marker_color=shade_ramp(SECTION_COLOR["training"], len(labels)),
        hovertemplate="%{x}: %{y} hours<extra></extra>",
    ))
    style_layout(fig, orientation="v", height=320)
    return "Training hours", chart_html(fig)


# --------------------------------------------------------------------------- #
# Software Builds
# --------------------------------------------------------------------------- #

def named_list_html(heading, items):
    color = SECTION_COLOR["builds"]
    lis = [f"<li>{item}</li>" for item in items]
    return f'<div class="chart-section">{card_header(heading, color)}{_collapsible_list(lis)}</div>'


def builds_body(modules):
    version_items = lambda pairs: [f"{p['software']} {p['version']}" for p in pairs]
    return "".join([
        named_list_html("New software installed", modules["new_software"]),
        named_list_html("New versions added", version_items(modules["new_versions"])),
        named_list_html("Old software removed", modules["removed_software"]),
        named_list_html("Old versions removed", version_items(modules["removed_versions"])),
    ])


# --------------------------------------------------------------------------- #
# Pills
# --------------------------------------------------------------------------- #

def pills_html(cards, color):
    return "\n".join(
        f'<div class="headline-card"><div class="headline-label" style="color:{color}">{label}</div>'
        f'<div class="headline-figure">{figure}</div>'
        f'<div class="headline-sub">{sub}</div></div>'
        for label, figure, sub in cards
    )


def ticket_pills(df, month_start_ts, month_end_ts, prev_start_ts):
    this_month = df[(df["created"] >= month_start_ts) & (df["created"] <= month_end_ts)]
    prev_period = df[(df["created"] >= prev_start_ts) & (df["created"] < month_start_ts)]

    volume = len(this_month)
    delta = volume - len(prev_period)
    csat = this_month["csat_rating"].dropna()
    pct_positive = (csat >= 4).mean() if len(csat) else None

    # Filter out admin tickets
    non_admin = this_month[this_month["category"] != "Admin"]

    solve_days = non_admin["resolution_days"].dropna()
    solve_days = solve_days[solve_days <= SOLVE_DAYS_OUTLIER_CAP]
    if len(solve_days):
        solve_figure = f"{solve_days.mean():.1f} days"
        solve_sub = f"std dev {solve_days.std():.1f} days"
    else:
        solve_figure = "no data"
        solve_sub = "average solve time"

    return [
        ("Volume", f"{volume:,} tickets", f"{delta:+d} vs. prior period"),
        ("Quality", f"{pct_positive:.0%} positive" if pct_positive is not None else "no ratings yet",
         f"{len(csat)} CSAT responses"),
        ("Average solve time", solve_figure, solve_sub),
    ]


def ticket_activity_pills(df, month_start_ts, month_end_ts):
    this_month = df[(df["created"] >= month_start_ts) & (df["created"] <= month_end_ts)]
    counts = this_month["subcategory"].value_counts()
    return [
        ("New allocations", f"{counts.get('Allocation Request', 0)}", "allocation requests"),
        ("New projects", f"{counts.get('Project Request', 0)}", "project requests"),
        ("New users", f"{counts.get('Account Request', 0)}", "account requests"),
    ]


def docs_pills(activity):
    docs = activity["support_docs"]
    return [
        ("Commits", f"{docs['commits']}", "to support-docs"),
        ("PRs merged", f"{docs['prs_merged']}", "to support-docs"),
        ("Issues", f"{docs['issues_opened'] + docs['issues_closed']}", "actioned this month"),
    ]


def other_pills(activity):
    by_repo = activity["other_contributions"]["by_repo"]
    return [
        ("Activity", f"{sum(by_repo.values())}", "commits + PRs + issues"),
        ("Repos touched", f"{len(by_repo)}", "across the org"),
    ]


def training_pills(training_df, month_start_ts, month_end_ts):
    scoped = training_df[
        (training_df["start_date"] >= month_start_ts) & (training_df["start_date"] <= month_end_ts)
    ]
    return [
        ("Events", f"{len(scoped)}", "delivered this month"),
        ("Attendees", f"{int(scoped['attendees'].fillna(0).sum())}", "across all events"),
    ]


def builds_pills(modules):
    return [
        ("New software", f"{len(modules['new_software'])}", "installed this month"),
        ("New versions", f"{len(modules['new_versions'])}", "installed this month"),
        ("Software", f"{len(modules['removed_software'])}", "removed this month"),
        ("Versions", f"{len(modules['removed_versions'])}", "removed this month"),
    ]


# --------------------------------------------------------------------------- #
# Page assembly
# --------------------------------------------------------------------------- #

def card_header(title, color):
    "The same small uppercase label the stat pills use"
    return f'<div class="headline-label" style="color:{color}">{title}</div>'


def chart_block(entry, color):
    title, fragment = entry
    return f'<div class="chart-section">{card_header(title, color)}{fragment}</div>'


def image_block(entry, color):
    if entry is None:
        return None
    title, data_uri = entry
    return f'<div class="chart-section">{card_header(title, color)}<img src="{data_uri}"></div>'


def chart_row(*blocks):
    """Two small charts side by side instead of stacked full-width."""
    return f'<div class="chart-row">{"".join(blocks)}</div>'


def section_html(title, color, pills, body):
    return f"""
  <section class="report-section">
    <h2 style="border-left-color:{color}">{title}</h2>
    <div class="headline-row">{pills}</div>
    {body}
  </section>
"""


def render(month_start, month_end):
    df = load_tickets()
    training_df = load_training()
    activity = load_github(month_start, month_end)
    gitlab_activity = load_gitlab(month_start, month_end)
    # GitHub and GitLab repo names never collide (distinct org/group
    # namespaces), so a straight count merge is safe.
    activity["other_contributions"]["by_repo"] = dict(
        (Counter(activity["other_contributions"]["by_repo"]) + Counter(gitlab_activity["by_repo"])).most_common()
    )

    month_start_ts = pd.Timestamp(month_start, tz="UTC")
    month_end_ts = pd.Timestamp(month_end, tz="UTC") + pd.Timedelta(hours=23, minutes=59, seconds=59)
    period_days = (month_end - month_start).days + 1
    prev_start_ts = month_start_ts - pd.Timedelta(days=period_days)

    training_start_ts = pd.Timestamp(month_start)
    training_end_ts = pd.Timestamp(month_end) + pd.Timedelta(hours=23, minutes=59, seconds=59)

    # Tickets
    tickets_color = SECTION_COLOR["tickets"]
    wordcloud_entry = chart_csat_wordcloud(df, month_start_ts, month_end_ts)
    tickets_body = "".join(filter(None, [
        f'<div class="chart-hidden">{chart_block(chart_ticket_volume(df), tickets_color)}</div>',
        chart_row(
            chart_block(chart_institutions(df, month_start_ts, month_end_ts), tickets_color),
            chart_block(chart_category(df, month_start_ts, month_end_ts), tickets_color),
        ),
        f'<div class="headline-row">{pills_html(ticket_activity_pills(df, month_start_ts, month_end_ts), tickets_color)}</div>',
        chart_block(chart_response_distribution(df, month_start_ts, month_end_ts), tickets_color),
        chart_row(
            chart_block(chart_effort(df, month_start_ts, month_end_ts), tickets_color),
            chart_block(chart_csat(df, month_start_ts, month_end_ts), tickets_color),
        ),
        image_block(wordcloud_entry, tickets_color),
    ]))
    tickets_section = section_html(
        "Customer Satisfaction & Ticket Performance", tickets_color,
        pills_html(ticket_pills(df, month_start_ts, month_end_ts, prev_start_ts), tickets_color),
        tickets_body,
    )

    # Docs
    docs_body = new_pages_html(activity["support_docs"]["new_pages"])
    docs_section = section_html(
        "Core User Knowledge Base Contributions", SECTION_COLOR["docs"],
        pills_html(docs_pills(activity), SECTION_COLOR["docs"]),
        docs_body,
    )

    # Other Git
    other_color = SECTION_COLOR["other"]
    other_body = chart_block(chart_repo_activity(activity), other_color)
    other_section = section_html(
        "Full Stack Software Contributions", other_color,
        pills_html(other_pills(activity), other_color),
        other_body,
    )

    # Training
    training_color = SECTION_COLOR["training"]
    training_body = chart_block(chart_training_hours(training_df, training_start_ts, training_end_ts), training_color)
    training_section = section_html(
        "Community Enablement Training & Stakeholder Engagement", training_color,
        pills_html(training_pills(training_df, training_start_ts, training_end_ts), training_color),
        training_body,
    )

    # Software Builds
    modules = load_modules(month_start, month_end)
    builds_section = section_html(
        "Software Build Deliverables", SECTION_COLOR["builds"],
        pills_html(builds_pills(modules), SECTION_COLOR["builds"]),
        builds_body(modules),
    )

    html = string.Template(TEMPLATE_PATH.read_text()).substitute(
        month_label=period_label(month_start, month_end),
        sections=tickets_section + docs_section + other_section + training_section + builds_section,
    )

    out_path = Path("public") / "index.html"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(html)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=date.fromisoformat)
    parser.add_argument("--until", type=date.fromisoformat)
    args = parser.parse_args()

    if args.since and args.until:
        month_start, month_end = args.since, args.until
    else:
        month_start, month_end = previous_month_range()
    render(month_start, month_end)
