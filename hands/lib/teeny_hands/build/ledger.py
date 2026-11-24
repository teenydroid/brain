"""What the builder left: `build_jobs` and `build_ideas`, read only.

The builder (an agent job that built small programs and Teeny's own changes
from a desk under state/workbench/) is retired (2026-11-24): the agentic mind
ships things itself, through its broker (docs/mind.md). Its ledgers stay in
state/hands.db as they were, never dropped or altered, because the dream, the
receipts, the proofs, the prompts, the goals, the world score and the memory
probe read what it shipped. This module keeps their shape, so a reader (or a
test) gets the tables even on a box that never ran the builder, and the one
read verb, `hands/build jobs`.

  build_jobs(id, kind, request_id, slug, repo, text, workdir, status,
             claimed_at, finished_at, summary, tests, ship_attempts,
             ship_error, shipped_at, repo_url, commit_url, pr_url, verified,
             parent_id, branch, rubric)

  kind     request, daily, self, fix or upstream
  status   claimed, ok, failed, shipped or gave_up

  build_ideas(day, title, who, source, pitch, created_at, job_id)
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .. import hand
from .. import requests as build_requests

KINDS = ("request", "daily", "self", "fix", "upstream")
STATUSES = ("claimed", "ok", "failed", "shipped", "gave_up")

SCHEMA = """
CREATE TABLE IF NOT EXISTS build_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,
    request_id    INTEGER UNIQUE,
    slug          TEXT NOT NULL UNIQUE,
    repo          TEXT NOT NULL,
    text          TEXT NOT NULL,
    workdir       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'claimed',
    claimed_at    REAL NOT NULL,
    finished_at   REAL,
    summary       TEXT,
    tests         TEXT,
    ship_attempts INTEGER NOT NULL DEFAULT 0,
    ship_error    TEXT,
    shipped_at    REAL,
    repo_url      TEXT,
    commit_url    TEXT,
    pr_url        TEXT,
    verified      TEXT,
    parent_id     INTEGER,
    branch        TEXT,
    rubric        TEXT
);
CREATE INDEX IF NOT EXISTS build_jobs_status ON build_jobs (status, claimed_at);
CREATE TABLE IF NOT EXISTS build_ideas (
    day        TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    who        TEXT NOT NULL,
    source     TEXT NOT NULL,
    pitch      TEXT NOT NULL,
    created_at REAL NOT NULL,
    job_id     INTEGER
);
"""

Json = dict[str, Any]


def db() -> sqlite3.Connection:
    """hands.db with the builder's tables and the requests in place. A table
    already there is left exactly as it is (CREATE ... IF NOT EXISTS)."""
    return hand.open_db(SCHEMA + build_requests.SCHEMA)


def _row(row: sqlite3.Row) -> Json:
    out = {k: row[k] for k in row.keys() if row[k] is not None}
    for key in ("verified", "rubric", "tests"):
        if isinstance(out.get(key), str):
            try:
                out[key] = json.loads(out[key])
            except ValueError:
                pass
    return out


def listing(status: str | None = None, limit: int = 50) -> Json:
    """`hands/build jobs`: the builder's jobs, newest first."""
    if status is not None and status not in STATUSES:
        raise hand.HandError(f"status must be one of {', '.join(STATUSES)}")
    conn = db()
    try:
        query = "SELECT * FROM build_jobs"
        params: tuple[Any, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        rows = conn.execute(query + " ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
    finally:
        conn.close()
    return {"jobs": [_row(r) for r in rows]}
