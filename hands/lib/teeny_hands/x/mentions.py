"""The mentions queue: what people said to the account, read once, screened,
and offered to the core as inbound events (docs/hands.md, "ingest").

`ingest` reads the account's mentions timeline (`/2/users/:id/mentions`)
from a cursor kept in `x_cursor`, so a mention is fetched, and billed, once.
Each new one becomes a row in `x_mentions`, unique on its post id:

  pending  stored, not yet offered (or the offer failed; the next run retries)
  held     the inbound guard held it, or the core's prompt-injection guard
           kept it out of the drafting turn; nothing answers it
  skipped  never offered: the account's own post, or already answered
  offered  in the core's queue (`openhuman.inbound_offer`, source x:mentions)
  drafted  a draft came back and went through `hands/x reply` under DRY_RUN
  replied  answered on X
  refused  `hands/x reply` refused the draft (a rule or the guard)

The core coalesces offered mentions into one turn of a tool-less agent and
keeps the draft for `bin/inbound-consume`, which answers through
`hands/x reply` and marks the rows (`mark`). Nothing here posts.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from typing import Any, Mapping

from .. import hand
from .. import requests as build_requests
from . import cache, open_db
from . import posting_rules as rules
from .client import XApi, number_setting
from .model import Json

#: The source the core is configured to coalesce (`[inbound.sources."x:mentions"]`).
SOURCE = "x:mentions"
#: Every mention lands in one conversation: one turn answers a batch.
KEY = "mentions"

STATUSES = ("pending", "held", "skipped", "offered", "drafted", "replied", "refused")
#: What `mark` may set: the outcomes after a draft.
MARKS = ("drafted", "replied", "refused", "skipped", "held")

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_cursor (
    name     TEXT PRIMARY KEY,
    since_id TEXT NOT NULL,
    at       REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS x_mentions (
    id              TEXT PRIMARY KEY,
    author_id       TEXT,
    author_username TEXT,
    conversation_id TEXT,
    created_at      TEXT,
    text            TEXT NOT NULL,
    post            TEXT NOT NULL,
    status          TEXT NOT NULL,
    note            TEXT,
    hold_id         INTEGER,
    seen_at         REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS x_mentions_status ON x_mentions (status, seen_at);
"""


def _db() -> sqlite3.Connection:
    conn = open_db(SCHEMA)
    rules.ensure_schema(conn)
    return conn


def _screen(post: Json) -> Any:
    """The inbound guard's verdict on a mention. Imported by name, so a test
    can stand in for the other hand's gate."""
    from ..guard.gate import inbound

    return inbound(post["text"], surface="x", context={"post_id": post["id"], "author": post.get("authorUsername")})


def _offer(post: Json) -> None:
    """One mention into the core's queue. A duplicate is the core's to drop."""
    hand.core(
        "inbound_offer",
        {
            "source": SOURCE,
            "key": KEY,
            "event_id": post["id"],
            "text": post["text"],
            "payload": {
                "id": post["id"],
                "author": post.get("authorUsername"),
                "author_id": post.get("authorId"),
                "conversation_id": post.get("conversationId"),
                "created_at": post.get("createdAt"),
            },
        },
        timeout=30.0,
    )


def ingest(max_results: int | None = None, offer: bool = True, x: XApi | None = None) -> Mapping[str, Any]:
    """Reads new mentions into the queue and offers every pending one. Safe to
    run at any time and twice at once (a lock), and it never answers anyone."""
    x = x or XApi.from_settings()
    wanted = max_results if max_results is not None else int(number_setting("X_MENTIONS_MAX_RESULTS", 20, 5, 100))
    with hand.lock("x-ingest", timeout=60):
        conn = _db()
        me = cache.reads(x).identity()
        row = conn.execute("SELECT since_id FROM x_cursor WHERE name = 'mentions'").fetchone()
        since_id = row["since_id"] if row else None
        page = x.mentions_page(me["id"], since_id=since_id, max_results=wanted)
        posts = page["posts"]

        counts = {status: 0 for status in STATUSES}
        now = hand.now()
        for post in sorted(posts, key=lambda p: int(p["id"])):
            if conn.execute("SELECT 1 FROM x_mentions WHERE id = ?", (post["id"],)).fetchone():
                continue
            status, note, hold_id = "pending", None, None
            if post.get("authorId") == me["id"]:
                status, note = "skipped", "own post"
            elif conn.execute(
                "SELECT 1 FROM x_replies WHERE in_reply_to = ? AND reply_id IS NOT NULL", (post["id"],)
            ).fetchone():
                status, note = "skipped", "already answered"
            else:
                verdict = _screen(post)
                if verdict.kind != "allow":
                    status, note, hold_id = "held", verdict.reason, verdict.hold_id
            conn.execute(
                "INSERT OR IGNORE INTO x_mentions (id, author_id, author_username, conversation_id, created_at,"
                " text, post, status, note, hold_id, seen_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    post["id"],
                    post.get("authorId"),
                    post.get("authorUsername"),
                    post.get("conversationId"),
                    post.get("createdAt"),
                    post["text"],
                    json.dumps(post, ensure_ascii=False),
                    status,
                    note,
                    hold_id,
                    now,
                    now,
                ),
            )
            counts[status] += 1
        # Every post the page brought back is stored, so the cursor can move:
        # a failed offer is retried from the row, not by reading X again.
        newest = page.get("newestId") or (max((p["id"] for p in posts), key=int) if posts else None)
        if newest:
            conn.execute(
                "INSERT INTO x_cursor (name, since_id, at) VALUES ('mentions', ?, ?)"
                " ON CONFLICT(name) DO UPDATE SET since_id = excluded.since_id, at = excluded.at",
                (newest, now),
            )

        offered, failed = 0, None
        if offer:
            for pending in conn.execute("SELECT post FROM x_mentions WHERE status = 'pending' ORDER BY id").fetchall():
                post = json.loads(pending["post"])
                try:
                    _offer(post)
                except hand.CoreError as exc:
                    # The rest would fail the same way; the next run retries.
                    failed = str(exc)
                    break
                conn.execute(
                    "UPDATE x_mentions SET status = 'offered', updated_at = ? WHERE id = ?", (hand.now(), post["id"])
                )
                offered += 1
        new = {status: n for status, n in counts.items() if n}
        result: dict[str, Any] = {"read": len(posts), "new": new, "offered": offered, "cursor": newest or since_id}
        if failed:
            _warn(f"offer failed, left pending: {failed}")
            result["offer_error"] = failed
        return result


#: The mentions `requests` reads for asks to build: every row the guard let
#: through. A held mention is never a request, and a skipped one is the
#: account's own post or one already answered.
_REQUEST_STATUSES = ("pending", "offered", "drafted", "replied", "refused")


def note_requests(limit: int = 200) -> Mapping[str, Any]:
    """Reads the stored mentions (never X again: `ingest` already has them)
    for asks to build something and records each in `build_requests`, once
    per mention. Answers nobody."""
    conn = open_db(SCHEMA + build_requests.SCHEMA)
    try:
        placeholders = ", ".join("?" for _ in _REQUEST_STATUSES)
        rows = conn.execute(
            f"SELECT id, author_username, text FROM x_mentions WHERE status IN ({placeholders})"
            " ORDER BY seen_at DESC, CAST(id AS INTEGER) DESC LIMIT ?",
            (*_REQUEST_STATUSES, limit),
        ).fetchall()
        noted = []
        for row in rows:
            if build_requests.looks_like_build_request(row["text"]):
                if build_requests.record(SOURCE, row["id"], row["author_username"], row["text"], conn):
                    noted.append(row["id"])
    finally:
        conn.close()
    return {"scanned": len(rows), "noted": len(noted), "ids": noted}


def mentions_list(status: str | None = None, limit: int = 50) -> Mapping[str, Any]:
    """The queue, newest first."""
    conn = _db()
    query = "SELECT id, author_username, text, status, note, hold_id FROM x_mentions"
    params: tuple[Any, ...] = ()
    if status:
        query += " WHERE status = ?"
        params = (status,)
    rows = conn.execute(query + " ORDER BY CAST(id AS INTEGER) DESC LIMIT ?", (*params, limit)).fetchall()
    return {"mentions": [{k: r[k] for k in r.keys() if r[k] is not None} for r in rows]}


def mark(post_id: str, status: str, note: str | None = None) -> Mapping[str, Any]:
    """Records what became of a mention after its draft."""
    if status not in MARKS:
        raise hand.HandError(f"status must be one of {', '.join(MARKS)}")
    conn = _db()
    changed = conn.execute(
        "UPDATE x_mentions SET status = ?, note = ?, updated_at = ? WHERE id = ?",
        (status, note, hand.now(), post_id),
    ).rowcount
    if not changed:
        raise hand.Refused("unknown mention", id=post_id)
    return {"id": post_id, "status": status}


def _warn(message: str) -> None:
    print(f"hands/x: {message}", file=sys.stderr)
