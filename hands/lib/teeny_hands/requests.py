"""Build requests: somebody asked Teeny to make something.

Shared by every hand that reads what people say to Teeny (`mail ingest`
today; `x ingest` can call `record` the same way), so there is one list of
asks to pick from, whatever channel they came in on:

  build_requests(id, source, source_id, author, text, created_at, status, note)

  pending  noticed, nobody has decided
  picked   Teeny chose to build it (a promise is still never made in a reply)
  skipped  not doing it
  shipped  built and out
  failed   Teeny tried to build it and could not (hands/build says why)

A request is recorded once per (source, source_id). Nothing here answers the
asker: the drafting agent may acknowledge a request ("noted, no promises"),
never promise one.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Mapping

from . import hand

STATUSES = ("pending", "picked", "skipped", "shipped", "failed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS build_requests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    author     TEXT,
    text       TEXT NOT NULL,
    created_at REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    note       TEXT,
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS build_requests_status ON build_requests (status, created_at);
"""

#: What an ask to build sounds like. Deliberately narrow: a false positive
#: only puts a line on a list, but a list full of noise is not read.
_ASK = re.compile(
    r"\b(?:"
    r"build\s+(?:me|us)\b"
    r"|(?:can|could|would|will)\s+(?:you|u|teeny)\s+(?:please\s+)?(?:make|build|create|code|write|ship)\b"
    r"|(?:please|pls|plz)\s+(?:make|build|create|code|ship)\b"
    r"|feature\s+request\b"
    r"|i\s+(?:want|need|would\s+love)\s+(?:you\s+to\s+)?(?:a|an)\s+(?:bot|app|tool|site|website|script)\b"
    r")",
    re.IGNORECASE,
)

_TEXT_CHARS = 2000


def looks_like_build_request(text: str) -> bool:
    return bool(_ASK.search(text or ""))


def _db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def record(source: str, source_id: str, author: str | None, text: str, conn: sqlite3.Connection | None = None) -> bool:
    """Adds a request; False when that (source, source_id) is already there.
    A caller passing its own connection has put SCHEMA in place already
    (executescript would commit an open transaction)."""
    own = conn is None
    conn = conn or _db()
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO build_requests (source, source_id, author, text, created_at) VALUES (?, ?, ?, ?, ?)",
            (source, source_id, author, (text or "")[:_TEXT_CHARS], hand.now()),
        )
        return cursor.rowcount > 0
    finally:
        if own:
            conn.close()


def listing(status: str | None = None, limit: int = 50) -> Mapping[str, Any]:
    conn = _db()
    try:
        query = "SELECT id, source, source_id, author, text, created_at, status, note FROM build_requests"
        params: tuple[Any, ...] = ()
        if status:
            if status not in STATUSES:
                raise hand.HandError(f"status must be one of {', '.join(STATUSES)}")
            query += " WHERE status = ?"
            params = (status,)
        rows = conn.execute(query + " ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
    finally:
        conn.close()
    return {"requests": [{k: r[k] for k in r.keys() if r[k] is not None} for r in rows]}


def mark(request_id: int, status: str, note: str | None = None) -> Mapping[str, Any]:
    if status not in STATUSES:
        raise hand.HandError(f"status must be one of {', '.join(STATUSES)}")
    conn = _db()
    try:
        changed = conn.execute(
            "UPDATE build_requests SET status = ?, note = ? WHERE id = ?", (status, note, request_id)
        ).rowcount
    finally:
        conn.close()
    if not changed:
        raise hand.Refused("unknown build request", id=request_id)
    return {"id": request_id, "status": status}
