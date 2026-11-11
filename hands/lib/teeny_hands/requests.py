"""Build requests: somebody asked Teeny to make something.

Shared by every hand that reads what people say to Teeny (`mail ingest` and
`x requests`), so there is one list of asks to pick from, whatever channel
they came in on, and by `hands/requests`, which picks from it:

  build_requests(id, source, source_id, author, text, created_at, status, note)

  pending  noticed, nobody has decided
  picked   Teeny chose to build it (a promise is still never made in a reply)
  skipped  not doing it
  shipped  built and out (the builder sets it)
  failed   tried and did not ship (the builder sets it)

A request is recorded once per (source, source_id). Nothing here answers the
asker: the drafting agent may acknowledge a request ("noted, no promises"),
never promise one.

Picking is one at a time: `pick` refuses while another request is picked (or
in any state the builder moves it through before shipped or failed), so the
builder always has at most one thing to work on. Every decision is written to
`build_request_events`, the ledger of who moved which request and why.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Mapping

from . import hand

STATUSES = ("pending", "picked", "skipped", "shipped", "failed")
#: The statuses a request is finished or untouched in. Any other (picked, or
#: a step the builder records on the way) is the one request being worked on.
_IDLE = ("pending", "skipped", "shipped", "failed")

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
CREATE TABLE IF NOT EXISTS build_request_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    at         REAL NOT NULL,
    action     TEXT NOT NULL,
    status     TEXT NOT NULL,
    note       TEXT
);
CREATE INDEX IF NOT EXISTS build_request_events_request ON build_request_events (request_id, at);
"""

#: What an ask to build sounds like. Deliberately narrow: a false positive
#: only puts a line on a list, but a list full of noise is not read.
_ASK = re.compile(
    r"\b(?:"
    r"build\s+(?:me|us)\b"
    r"|(?:can|could|would|will)\s+(?:you|u|teeny)\s+(?:please\s+)?(?:make|build|create|code|write|ship)\b"
    r"|(?:please|pls|plz)\s+(?:make|build|create|code|ship)\b"
    r"|make\s+(?:me\s+|us\s+)?(?:a|an)\s+(?:tool|bot|app|script|site|website|extension|cli)\s+(?:that|which|to|for)\b"
    r"|i\s+wish\s+there\s+(?:was|were|is)\s+(?:a|an)\s+(?:tool|bot|app|script|site|website|extension|cli)\b"
    r"|feature\s+request\b"
    r"|i\s+(?:want|need|would\s+love)\s+(?:you\s+to\s+)?(?:a|an)\s+(?:bot|app|tool|site|website|script)\b"
    r")",
    re.IGNORECASE,
)

_TEXT_CHARS = 2000
_NOTE_CHARS = 500


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


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys() if row[k] is not None}


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
    return {"requests": [_row(r) for r in rows]}


def get(request_id: int) -> Mapping[str, Any]:
    conn = _db()
    try:
        row = conn.execute(
            "SELECT id, source, source_id, author, text, created_at, status, note FROM build_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise hand.Refused("unknown build request", rule="unknown", id=request_id)
        events = conn.execute(
            "SELECT at, action, status, note FROM build_request_events WHERE request_id = ? ORDER BY id",
            (request_id,),
        ).fetchall()
    finally:
        conn.close()
    return {"request": _row(row), "events": [_row(e) for e in events]}


def mark(request_id: int, status: str, note: str | None = None) -> Mapping[str, Any]:
    if status not in STATUSES:
        raise hand.HandError(f"status must be one of {', '.join(STATUSES)}")
    conn = _db()
    try:
        with hand.transaction(conn):
            changed = conn.execute(
                "UPDATE build_requests SET status = ?, note = ? WHERE id = ?", (status, note, request_id)
            ).rowcount
            if changed:
                _event(conn, request_id, "mark", status, note)
    finally:
        conn.close()
    if not changed:
        raise hand.Refused("unknown build request", id=request_id)
    return {"id": request_id, "status": status}


# --- deciding -------------------------------------------------------------------------------


def _event(conn: sqlite3.Connection, request_id: int, action: str, status: str, note: str | None) -> None:
    conn.execute(
        "INSERT INTO build_request_events (request_id, at, action, status, note) VALUES (?, ?, ?, ?, ?)",
        (request_id, hand.now(), action, status, note),
    )


def _id(body: Mapping[str, Any]) -> int:
    value = body.get("id")
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise hand.HandError('"id" must be a build request id (a whole number)')
    return value


def _reason(body: Mapping[str, Any]) -> str:
    value = body.get("reason")
    if not isinstance(value, str) or not value.strip():
        raise hand.HandError('"reason" must say why, in a sentence')
    return value.strip()[:_NOTE_CHARS]


def _decision_body(body: Any) -> tuple[int, str]:
    if not isinstance(body, Mapping):
        raise hand.HandError("stdin must be a JSON object with id and reason")
    unknown = sorted(set(body) - {"id", "reason", "text"})
    if unknown:
        raise hand.HandError(f"unknown field(s) in the body: {', '.join(unknown)}")
    return _id(body), _reason(body)


def _origin_held(conn: sqlite3.Connection, source: str, source_id: str) -> str | None:
    """Why the guard held the message a request came from, if it did. The
    intake tables belong to the hands that read each channel; a table that
    is not there yet means that channel never held anything."""
    table = {"x:mentions": ("x_mentions", "id"), "mail:inbox": ("mail_messages", "message_id")}.get(source)
    if table is None:
        return None
    try:
        row = conn.execute(f"SELECT status, note FROM {table[0]} WHERE {table[1]} = ?", (source_id,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is not None and row["status"] == "held":
        return row["note"] or "held by the inbound guard"
    return None


def _screened(text: str) -> str | None:
    """The guard's lexicon on the request's words, read again at the pick:
    a request recorded before a term was added to the lexicon is not built."""
    from .guard.screen import INBOUND, screen_text

    match = screen_text(text, INBOUND)
    return match.category if match is not None else None


def pick(body: Any) -> Mapping[str, Any]:
    """Picks one pending request for the builder. Writes only hands.db and
    tells nobody, so DRY_RUN does not stop it: what the builder does with the
    pick goes through hands that keep DRY_RUN themselves."""
    request_id, reason = _decision_body(body)
    conn = _db()
    try:
        with hand.transaction(conn):
            row = conn.execute(
                "SELECT id, source, source_id, text, status FROM build_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise hand.Refused("unknown build request", rule="unknown", id=request_id)
            if row["status"] != "pending":
                raise hand.Refused(
                    f"request {request_id} is {row['status']}; only a pending request can be picked",
                    rule="status",
                    id=request_id,
                    status=row["status"],
                )
            placeholders = ", ".join("?" for _ in _IDLE)
            busy = conn.execute(
                f"SELECT id, status FROM build_requests WHERE status NOT IN ({placeholders}) ORDER BY id LIMIT 1",
                _IDLE,
            ).fetchone()
            if busy is not None:
                raise hand.Refused(
                    f"request {busy['id']} is already {busy['status']}; one at a time, until it ships or fails",
                    rule="one_at_a_time",
                    id=request_id,
                    active=busy["id"],
                )
            held = _origin_held(conn, row["source"], row["source_id"])
            category = _screened(row["text"])
            if held or category:
                note = f"held: {held or category}"
                conn.execute("UPDATE build_requests SET status = 'skipped', note = ? WHERE id = ?", (note, request_id))
                _event(conn, request_id, "pick", "skipped", note)
                # The skip above is the guard's decision and is kept; the
                # refusal below is raised after the commit.
        if held or category:
            raise hand.Refused(
                "the guard held the words this request came in; it is skipped, not built",
                rule="held",
                id=request_id,
            )
        with hand.transaction(conn):
            conn.execute("UPDATE build_requests SET status = 'picked', note = ? WHERE id = ?", (reason, request_id))
            _event(conn, request_id, "pick", "picked", reason)
    finally:
        conn.close()
    return {"id": request_id, "status": "picked", "reason": reason}


def skip(body: Any) -> Mapping[str, Any]:
    """Not doing this one. Only a pending request: a picked one is the
    builder's until it ships or fails (the operator can `reset` it)."""
    request_id, reason = _decision_body(body)
    conn = _db()
    try:
        with hand.transaction(conn):
            row = conn.execute("SELECT status FROM build_requests WHERE id = ?", (request_id,)).fetchone()
            if row is None:
                raise hand.Refused("unknown build request", rule="unknown", id=request_id)
            if row["status"] != "pending":
                raise hand.Refused(
                    f"request {request_id} is {row['status']}; only a pending request can be skipped",
                    rule="status",
                    id=request_id,
                    status=row["status"],
                )
            conn.execute("UPDATE build_requests SET status = 'skipped', note = ? WHERE id = ?", (reason, request_id))
            _event(conn, request_id, "skip", "skipped", reason)
    finally:
        conn.close()
    return {"id": request_id, "status": "skipped", "reason": reason}


def reset(request_id: int, note: str | None = None) -> Mapping[str, Any]:
    """The operator puts a request back to pending, whatever it was."""
    conn = _db()
    try:
        with hand.transaction(conn):
            row = conn.execute("SELECT status FROM build_requests WHERE id = ?", (request_id,)).fetchone()
            if row is None:
                raise hand.Refused("unknown build request", rule="unknown", id=request_id)
            text = (note or "reset by the operator")[:_NOTE_CHARS]
            conn.execute("UPDATE build_requests SET status = 'pending', note = ? WHERE id = ?", (text, request_id))
            _event(conn, request_id, "reset", "pending", f"was {row['status']}: {text}")
    finally:
        conn.close()
    return {"id": request_id, "status": "pending", "was": row["status"]}
