"""Refresh public Greenhouse boards and keep their history in local SQLite."""

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    board TEXT PRIMARY KEY,
    employer TEXT NOT NULL,
    last_attempt TEXT NOT NULL,
    last_success TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    board TEXT NOT NULL REFERENCES sources(board),
    job_id TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT NOT NULL,
    url TEXT NOT NULL,
    is_open INTEGER NOT NULL CHECK (is_open IN (0, 1)),
    updated_at TEXT,
    PRIMARY KEY (board, job_id)
);
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY,
    board TEXT NOT NULL,
    job_id TEXT NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    at TEXT NOT NULL,
    FOREIGN KEY (board, job_id) REFERENCES jobs(board, job_id)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY,
    board TEXT NOT NULL REFERENCES sources(board),
    at TEXT NOT NULL,
    job_count INTEGER,
    error TEXT
);
"""


def fetch_board(board):
    """Fetch the complete metadata-only job list for a Greenhouse board."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", board):
        raise ValueError("Invalid Greenhouse board token")
    request = Request(
        f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
        headers={"Accept": "application/json", "User-Agent": "JobOpportunityTracker/1.0"},
    )
    with urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise ValueError(f"Incomplete HTTP response ({response.status})")
        return json.load(response)


def _parse_board(payload):
    """Validate the entire listing before treating missing IDs as closures."""
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError("Missing jobs list")
    rows = payload["jobs"]
    meta = payload.get("meta")
    total = meta.get("total") if isinstance(meta, dict) else None
    if type(total) is not int or total != len(rows):
        raise ValueError("Greenhouse total does not match the returned list")
    jobs = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid job entry")
        job_id = row.get("id")
        if type(job_id) is not int or job_id <= 0 or str(job_id) in jobs:
            raise ValueError(f"Invalid or duplicate job ID: {job_id!r}")
        title = row.get("title")
        location = row.get("location")
        url = row.get("absolute_url")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"Missing title for job {job_id}")
        if not isinstance(location, dict) or not isinstance(location.get("name"), str):
            raise ValueError(f"Missing location field for job {job_id}")
        if not isinstance(url, str) or any(char.isspace() for char in url):
            raise ValueError(f"Invalid link for job {job_id}")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None:
            raise ValueError(f"Invalid link for job {job_id}")
        updated_at = row.get("updated_at")
        if updated_at is not None:
            if not isinstance(updated_at, str):
                raise ValueError(f"Invalid update timestamp for job {job_id}")
            timestamp = datetime.fromisoformat(updated_at)
            if timestamp.tzinfo is None:
                raise ValueError(f"Missing time zone on update timestamp for job {job_id}")
            updated_at = timestamp.astimezone(timezone.utc).isoformat()
        jobs[str(job_id)] = (title.strip(), location["name"].strip(), url, updated_at)
    return jobs


def _apply_snapshot(db, board, jobs, at, is_baseline):
    previous = {
        row["job_id"]: row
        for row in db.execute("SELECT * FROM jobs WHERE board = ?", (board,))
    }
    events = []
    for job_id, fields in jobs.items():
        title, location, url, updated_at = fields
        old = previous.get(job_id)
        if old is None:
            kind = "baseline" if is_baseline else "new"
        elif not old["is_open"]:
            kind = "reopened"
        elif (title, location, url) != (old["title"], old["location"], old["url"]):
            kind = "changed"
        elif old["updated_at"] and updated_at and old["updated_at"] != updated_at:
            kind = "changed"
        else:
            if old["updated_at"] is None and updated_at is not None:
                # The first available upstream timestamp is our comparison point.
                db.execute("UPDATE jobs SET updated_at = ? WHERE board = ? AND job_id = ?",
                           (updated_at, board, job_id))
            continue
        db.execute(
            """INSERT INTO jobs (board, job_id, title, location, url, updated_at, is_open)
               VALUES (?, ?, ?, ?, ?, ?, 1)
               ON CONFLICT(board, job_id) DO UPDATE SET title = excluded.title,
               location = excluded.location, url = excluded.url, is_open = 1,
               updated_at = COALESCE(excluded.updated_at, jobs.updated_at)""",
            (board, job_id, *fields),
        )
        events.append((board, job_id, title, kind, at))
    for job_id, old in previous.items():
        if old["is_open"] and job_id not in jobs:
            db.execute("UPDATE jobs SET is_open = 0 WHERE board = ? AND job_id = ?", (board, job_id))
            events.append((board, job_id, old["title"], "closed", at))
    db.executemany("INSERT INTO events (board, job_id, title, kind, at) VALUES (?, ?, ?, ?, ?)", events)


def refresh(db_path: Path, boards: dict[str, str], *, fetch=None, now=None):
    """Refresh each board atomically. Return an error message or None per board."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    fetch = fetch_board if fetch is None else fetch
    results = {}
    with closing(sqlite3.connect(db_path, timeout=60)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(SCHEMA)
        with db:
            db.execute("BEGIN IMMEDIATE")
            if "updated_at" not in {r["name"] for r in db.execute("PRAGMA table_info(jobs)")}:
                db.execute("ALTER TABLE jobs ADD COLUMN updated_at TEXT")
        for board, employer in boards.items():
            with db:
                # Serialize overlapping refreshes before fetching to prevent an older
                # HTTP response from overwriting a newer snapshot of the same board.
                db.execute("BEGIN IMMEDIATE")
                at = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
                db.execute(
                    """INSERT INTO sources (board, employer, last_attempt) VALUES (?, ?, ?)
                       ON CONFLICT(board) DO UPDATE SET employer = excluded.employer,
                       last_attempt = excluded.last_attempt""", (board, employer, at)
                )
                last_success = db.execute(
                    "SELECT last_success FROM sources WHERE board = ?", (board,)
                ).fetchone()[0]
                try:
                    jobs = _parse_board(fetch(board))
                except Exception as exc:
                    # A source error belongs in the digest. SQLite persistence errors
                    # must propagate so the caller can report a failed refresh.
                    error = str(exc) or type(exc).__name__
                    results[board] = error
                    db.execute("UPDATE sources SET error = ? WHERE board = ?", (error, board))
                    db.execute("INSERT INTO runs (board, at, error) VALUES (?, ?, ?)", (board, at, error))
                    continue
                _apply_snapshot(db, board, jobs, at, last_success is None)
                db.execute("UPDATE sources SET error = NULL, last_success = ? WHERE board = ?", (at, board))
                db.execute("INSERT INTO runs (board, at, job_count) VALUES (?, ?, ?)", (board, at, len(jobs)))
                results[board] = None
    return results


def read_state(db_path: Path):
    """Return a consistent snapshot for the digest, label export, or inspection."""
    queries = {
        "jobs": """SELECT jobs.*, sources.employer FROM jobs JOIN sources USING (board)
                   ORDER BY board, job_id""",
        "sources": "SELECT * FROM sources ORDER BY board",
        "events": """SELECT events.*, sources.employer FROM events JOIN sources USING (board)
                     ORDER BY event_id DESC""",
        "runs": """SELECT runs.*, sources.employer FROM runs JOIN sources USING (board)
                   ORDER BY run_id DESC""",
    }
    if not Path(db_path).exists():
        return {key: [] for key in queries}
    with closing(sqlite3.connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        return {key: [dict(row) for row in db.execute(query)] for key, query in queries.items()}
