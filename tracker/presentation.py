"""Explainable title ranking and a self-contained, static job digest."""

import re
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlsplit


# Each group scores at most once. Keep these rules as the baseline for the ML model.
TITLE_RULES = [
    ("Data engineering", 5, r"\b(?:data (?:platform |pipeline |warehouse )?engineer(?:s|ing)?|engenheir[oa] de dados|engenharia de dados)\b"),
    ("AI / ML", 3, r"\b(?:AI|ML|LLMs?|NLP|artificial intelligence|machine learning|intelig[eê]ncia artificial)\b"),
    ("Agent systems", 4, r"\b(?:agentic|multi[- ]agent|(?:AI|LLM) agents?|agents? (?:developer|engineer|automation|platform))\b"),
    ("RAG", 4, r"\b(?:RAG|retrieval[- ]augmented(?: generation)?)\b"),
    ("ETL / ELT", 4, r"\b(?:ETL|ELT|data pipelines?)\b"),
    ("Python", 2, r"\bpython\b"),
    ("SQL", 2, r"\b(?:SQL|Postgres(?:QL)?|MySQL|T-SQL)\b"),
]


def _location_match(location):
    """Location describes what the employer listed, not work authorization."""
    location = location.strip()
    if re.search(r"\b(?:except|excluding|outside(?: of)?|not(?: in)?)\s+(?:brazil|brasil)\b", location, re.I):
        return "Brazil excluded", 0
    if re.search(r"\b(?:brazil|brasil|s[aã]o paulo|belo horizonte|rio de janeiro)\b", location, re.I):
        return "Brazil listed", 3
    if re.search(r"\b(?:LATAM|latin america|am[eé]rica latina)\b", location, re.I):
        return "LATAM listed", 2
    if re.search(r"\b(?:worldwide|anywhere in the world|globally remote)\b", location, re.I):
        return "Worldwide listed", 1
    if not location or re.fullmatch(
        r"(?:remote(?:[- ]only)?|(?:fully|100%) remote|remot[oa]|location unspecified|unspecified|unknown|n/a)",
        location, re.I,
    ):
        return "Location unspecified", 0
    return "Other listed location", 0


def ranked_jobs(state: dict) -> list[dict]:
    """Return open postings with auditable scores, in deterministic order."""
    result = []
    for job in state["jobs"]:
        if not job["is_open"]:
            continue
        title = job["title"]
        matches = [(label, points) for label, points, pattern in TITLE_RULES
                   if re.search(pattern, title, re.I)]
        if any(label == "AI / ML" for label, _ in matches) and re.search(
            r"\b(?:engineer(?:s|ing)?|developer|architect|engenheir[oa])\b", title, re.I
        ):
            matches.append(("AI engineering role", 2))
        eligibility, bonus = _location_match(job.get("location") or "")
        if matches and bonus:
            matches.append((eligibility, bonus))
        result.append({
            **job,
            "score": sum(points for _, points in matches),
            "reasons": [f"{label} +{points}" for label, points in matches] or ["No title keyword matched"],
            "eligibility": eligibility,
        })
    return sorted(result, key=lambda job: (
        -job["score"], job["title"].casefold(), job["employer"].casefold(),
        job["board"], str(job["job_id"]),
    ))


def _text(value):
    return escape(str(value) if value is not None else "", quote=True)


def _time(value):
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc)
            return _text(dt.strftime("%b %d, %Y · %H:%M UTC"))
    except ValueError:
        pass
    return _text(value)


def _job_link(job):
    title = _text(job["title"])
    url = str(job.get("url") or "")
    try:
        parts = urlsplit(url)
        if parts.scheme == "https" and parts.hostname and not any(char.isspace() for char in url):
            return f'<a href="{_text(url)}">{title}</a>'
    except ValueError:
        pass
    return title


def _job_row(job):
    reason = _text(" · ".join(job["reasons"]))
    location = _text(job["location"] or "Location unavailable")
    return f"""<li class="job">
      <div class="eyebrow">{_text(job['employer'])}</div>
      <h3>{_job_link(job)}</h3>
      <p class="location">{location} <span>· {_text(job['eligibility'])}</span></p>
      <p class="reason"><b>Score {_text(job['score'])}</b><span>{reason}</span></p>
    </li>"""


def _source_card(source):
    error = source.get("error")
    if error:
        status = "Stale" if source.get("last_success") else "Unavailable"
    else:
        status = "Current" if source.get("last_success") else "Pending"
    detail = f'<p class="problem">{_text(error)}</p>' if error else ""
    tone = "warn" if error else "ok"
    return f"""<li class="source"><div class="source-title">
      <strong>{_text(source['employer'])}</strong><span class="status {tone}">{status}</span></div>
      <dl><dt>Last attempt</dt><dd>{_time(source.get('last_attempt'))}</dd>
      <dt>Last success</dt><dd>{_time(source.get('last_success'))}</dd></dl>{detail}</li>"""


def _activity(state):
    employers = {source["board"]: source["employer"] for source in state["sources"]}
    changes = [event for event in state.get("events", []) if event["kind"] != "baseline"]
    if not changes:
        return '<p class="empty">New, changed, closed, and reopened postings will appear here after the first snapshot.</p>'
    items = []
    for event in changes[:20]:
        employer = employers.get(event["board"], event["board"])
        items.append(f"""<li><b>{_text(event['kind'].capitalize())}</b> {_text(event['title'])}
            <small>{_text(employer)} · {_time(event['at'])}</small></li>""")
    return '<ul class="activity">' + "".join(items) + "</ul>"


def _run_history(runs):
    if not runs:
        return ""
    items = []
    for run in runs[:12]:
        outcome = "Failed" if run.get("error") else f"{run['job_count']} postings"
        items.append(f"""<li><b>{_text(run['employer'])}</b> · {_text(outcome)}
            <small>{_time(run['at'])}</small></li>""")
    return '<details><summary>Recent source runs</summary><ul class="activity">' + "".join(items) + "</ul></details>"


_CSS = """
:root {
  color-scheme: light;
  --paper: #f6f5f0;
  --card: #fffefa;
  --ink: #182d29;
  --muted: #50635a;
  --line: #cdd4c9;
  --accent: #1c624c;
  --sans: "Inter","Segoe UI",Arial,sans-serif;
}

* {
  box-sizing: border-box;
}

html {
  scroll-behavior: smooth;
}

body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font: 1rem/1.5 var(--sans);
}

header {
  background: #193a31;
  color: #fff;
}

header>div,main {
  width: min(74rem,92%);
  margin: auto;
}

header>div {
  min-height: 4.1rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: .8rem;
  flex-wrap: wrap;
  padding: .65rem 0;
}

.brand {
  font-size: 1.1rem;
  font-weight: 700;
  letter-spacing: -.03em;
}

nav {
  display: flex;
  gap: 1.25rem;
  flex-wrap: wrap;
}

nav a {
  color: #e2eee5;
  font-size: .92rem;
}

a {
  color: var(--accent);
  text-underline-offset: .18em;
}

a:hover {
  text-decoration-thickness: 2px;
}

:focus-visible {
  outline: 3px solid #b46b25;
  outline-offset: 3px;
}

main {
  padding: clamp(1.7rem,5vw,3.7rem) 0 3.5rem;
}

.lead {
  margin: 0 0 2rem;
}

h1,h2,h3,p,ul,ol {
  margin: 0;
}

h1 {
  font: normal clamp(2.45rem,6vw,4.2rem)/1.09 Georgia,serif;
  letter-spacing: -.045em;
}

.lead p {
  color: var(--muted);
  margin-top: .7rem;
}

.layout {
  display: grid;
  grid-template-columns: minmax(0,1.85fr) minmax(17.5rem,1fr);
  gap: clamp(1rem,3vw,2.75rem);
}

section,aside {
  min-width: 0;
}

h2 {
  font-size: 1.2rem;
  line-height: 1.3;
  margin-bottom: .8rem;
}

.jobs,.sources,.activity {
  list-style: none;
  padding: 0;
}

.jobs {
  border-top: 2px solid var(--ink);
}

.job {
  border-bottom: 1px solid var(--line);
  padding: 1.05rem .1rem 1.15rem;
}

.eyebrow,.location,.reason,.activity small,dl,.empty {
  color: var(--muted);
}

.eyebrow {
  font-size: .85rem;
  font-weight: 650;
}

.job h3 {
  font-size: 1.15rem;
  line-height: 1.35;
  margin: .12rem 0 .25rem;
}

.job h3 a {
  color: var(--ink);
}

.location,.reason {
  font-size: .88rem;
}

.reason {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  column-gap: .6rem;
  margin-top: .55rem;
}

.reason b {
  color: var(--accent);
  font-weight: 700;
}

.panel {
  background: var(--card);
  padding: 1.15rem 1.2rem;
  border: 1px solid var(--line);
  border-radius: .65rem;
  margin-bottom: 1rem;
}

.source+.source,.activity li+li {
  border-top: 1px solid var(--line);
}

.source {
  padding: .7rem 0;
}

.source:last-child {
  padding-bottom: 0;
}

.source-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: .5rem;
}

.status {
  font-size: .78rem;
  font-weight: 700;
  border-radius: 1rem;
  padding: .16rem .55rem;
}

.ok {
  color: #20533e;
  background: #e1eee2;
}

.warn {
  color: #723f15;
  background: #fae9d3;
}

dl {
  font-size: .8rem;
  margin: .4rem 0 0;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: .13rem .65rem;
}

dd {
  margin: 0;
  text-align: right;
}

.problem {
  color: #753e18;
  font-size: .85rem;
  margin-top: .45rem;
}

.activity li {
  font-size: .9rem;
  padding: .6rem 0;
}

.activity small {
  display: block;
}

.empty {
  font-size: .92rem;
}

.activity,.job,.source {
  overflow-wrap: anywhere;
}

details {
  border-top: 1px solid var(--line);
  margin-top: 1rem;
  padding-top: .75rem;
}

summary {
  cursor: pointer;
  font-weight: 650;
  font-size: .94rem;
}

.skip {
  position: absolute;
  left: -999rem;
}

.skip:focus {
  left: .5rem;
  top: .5rem;
  background: var(--card);
  padding: .5rem;
  z-index: 1;
}

@media (max-width:48rem) {
  .layout {
    grid-template-columns: 1fr;
  }
  header>div {
    min-height: 3.5rem;
  }
  .lead {
    margin-bottom: 1.35rem;
  }
  .panel {
    padding: 1rem;
  }
}

@media (prefers-reduced-motion:reduce) {
  html {
    scroll-behavior: auto;
  }
}
"""


def render_digest(state: dict) -> str:
    jobs = ranked_jobs(state)
    rows = "".join(_job_row(job) for job in jobs)
    openings = (f'<ol class="jobs">{rows}</ol>' if rows else
                '<p class="empty">No open postings in the stored snapshots.</p>')
    sources = "".join(_source_card(source) for source in state["sources"])
    source_list = (f'<ul class="sources">{sources}</ul>' if sources else
                   '<p class="empty">No source has been refreshed yet.</p>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job opportunities · Job tracker</title><link rel="icon" href="data:,"><style>{_CSS}</style></head>
<body><a class="skip" href="#openings">Skip to openings</a><header><div>
<span class="brand">Job tracker</span><nav aria-label="Digest sections">
<a href="#openings">Openings</a><a href="#sources">Sources</a><a href="#changes">Changes</a></nav>
</div></header><main><div class="lead"><h1>Job opportunities</h1>
<p>Ranked by title keywords and listed location. Check the employer’s posting for eligibility.</p></div>
<div class="layout"><section id="openings"><h2>Open postings ({len(jobs)})</h2>{openings}</section>
<aside><section class="panel" id="sources"><h2>Source health</h2>{source_list}
{_run_history(state.get('runs', []))}</section>
<section class="panel" id="changes"><h2>Recent changes</h2>{_activity(state)}</section></aside>
</div></main></body></html>"""
