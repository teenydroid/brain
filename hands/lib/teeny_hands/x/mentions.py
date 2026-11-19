"""The mentions queue: what people said to the account, read once, screened,
and offered to the core as inbound events (docs/hands.md, "ingest").

X's webhook (x/webhook.py) queues each mention as it is written. `ingest`
screens and offers what is queued on every run, and once an hour
(`X_MENTIONS_POLL_HOURS`, 1) also reads the account's mentions timeline
(`/2/users/:id/mentions`) for anything the webhook lost: a read of X is
a call, and one every five minutes mostly found nothing. That read
starts from a cursor kept in `x_cursor`, so a mention is fetched, and billed,
once. Each new one becomes a row in `x_mentions`, unique on its post id:

  unscreened  queued by X's webhook (x/webhook.py), which runs where the
           core, and so the safety judge, is out of reach; the next ingest
           screens it (`screen_queued`) before anything is offered
  pending  stored, not yet offered (or the offer failed; the next run retries)
  held     the inbound guard held it, or the core's prompt-injection guard
           kept it out of the drafting turn; nothing answers it
  skipped  never offered: the account's own post, or already answered
  offered  handed on: to Teeny's mind (the stimuli inbox, bin/tick) or, with
           TEENY_INBOX=core, the core's queue (`openhuman.inbound_offer`,
           source x:mentions)
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
from . import cache, open_db
from . import posting_rules as rules
from .client import XApi, number_setting
from .model import Json

#: The source the core is configured to coalesce (`[inbound.sources."x:mentions"]`).
SOURCE = "x:mentions"
#: Every mention lands in one conversation: one turn answers a batch.
KEY = "mentions"

STATUSES = ("unscreened", "pending", "held", "skipped", "offered", "drafted", "replied", "refused")
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

#: What X's webhook says about a mention's author, when the delivery carries
#: it (`includes.users`): kept so a giveaway's filters need no paid lookup.
#: Null on a row from the mentions timeline, which asks only for usernames.
AUTHOR_COLUMNS = {
    "author_created_at": "TEXT",
    "author_followers": "INTEGER",
    "author_verified": "INTEGER",
}


def _db() -> sqlite3.Connection:
    conn = open_db(SCHEMA)
    hand.add_columns(conn, "x_mentions", AUTHOR_COLUMNS)
    rules.ensure_schema(conn)
    return conn


def _screen(post: Json) -> Any:
    """The inbound guard's verdict on a mention. Imported by name, so a test
    can stand in for the other hand's gate."""
    from ..guard.gate import inbound

    return inbound(post["text"], surface="x", context={"post_id": post["id"], "author": post.get("authorUsername")})


def _offer(post: Json) -> None:
    """One mention to whoever answers: Teeny's mind (the stimuli inbox, by
    default) or the core's queue (TEENY_INBOX=core). A duplicate is dropped
    either way."""
    from .. import stimuli

    if stimuli.to_mind():
        payload = {k: v for k, v in (("conversation_id", post.get("conversationId")),
                                     ("in_reply_to", post.get("inReplyToId")),
                                     ("created_at", post.get("createdAt"))) if v}
        stimuli.add("x", "mention", post["id"], post["text"], who=post.get("authorUsername"), payload=payload)
        return
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


def _store(
    conn, me: Mapping[str, Any], post: Mapping[str, Any], counts: dict[str, int], now: float, screen: bool = True
) -> None:
    """One post into the queue, once: screened, or skipped when it is the
    account's own or already answered. With `screen` False (X's webhook,
    where the judge cannot be reached) a post that would be screened is
    queued `unscreened`, for `screen_queued` to screen in the spine."""
    if conn.execute("SELECT 1 FROM x_mentions WHERE id = ?", (post["id"],)).fetchone():
        return
    status, note, hold_id = "pending", None, None
    if post.get("authorId") == me["id"]:
        status, note = "skipped", "own post"
    elif conn.execute("SELECT 1 FROM x_replies WHERE in_reply_to = ? AND reply_id IS NOT NULL", (post["id"],)).fetchone():
        status, note = "skipped", "already answered"
    elif not screen:
        status = "unscreened"
    else:
        verdict = _screen(post)
        if verdict.kind != "allow":
            status, note, hold_id = "held", verdict.reason, verdict.hold_id
    verified = post.get("authorVerified")
    conn.execute(
        "INSERT OR IGNORE INTO x_mentions (id, author_id, author_username, conversation_id, created_at,"
        " text, post, status, note, hold_id, seen_at, updated_at, author_created_at, author_followers,"
        " author_verified) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
            post.get("authorCreatedAt"),
            post.get("authorFollowers"),
            None if not isinstance(verified, bool) else int(verified),
        ),
    )
    counts[status] += 1


def screen_queued(conn, counts: dict[str, int]) -> None:
    """Screens each mention the webhook queued unscreened, oldest first: the
    inbound guard (the lexicon, then the judge) makes it `pending` or `held`,
    as `_store` would have. Runs in the spine, beside the core; a judge that
    cannot be reached holds (and the guard's re-screen asks again later)."""
    for row in conn.execute("SELECT post FROM x_mentions WHERE status = 'unscreened' ORDER BY CAST(id AS INTEGER)").fetchall():
        post = json.loads(row["post"])
        verdict = _screen(post)
        status, note, hold_id = ("pending", None, None) if verdict.kind == "allow" else ("held", verdict.reason, verdict.hold_id)
        changed = conn.execute(
            "UPDATE x_mentions SET status = ?, note = ?, hold_id = ?, updated_at = ? WHERE id = ? AND status = 'unscreened'",
            (status, note, hold_id, hand.now(), post["id"]),
        ).rowcount
        if changed:
            counts[status] += 1


def _set_cursor(conn, name: str, since_id: str | None, now: float) -> None:
    conn.execute(
        "INSERT INTO x_cursor (name, since_id, at) VALUES (?, ?, ?)"
        " ON CONFLICT(name) DO UPDATE SET since_id = excluded.since_id, at = excluded.at",
        (name, since_id or "", now),
    )


def _open_drop_posts(conn) -> set[str]:
    """The posts of the Tiny Drops still open (hands/giveaway): a reply in one
    of their threads is an entry, never a mention to answer. No table, no drops."""
    try:
        rows = conn.execute("SELECT post_id FROM giveaway_rounds WHERE status = 'open' AND post_id IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        return set()
    return {str(r["post_id"]) for r in rows}


#: The `x_cursor` row that keeps when X's mentions timeline was last read.
POLL = "mentions_poll"
#: Hours between two reads of the timeline, by default: X's webhook brings
#: each mention as it is written, so the timeline is only the backstop for a
#: delivery the webhook lost.
DEFAULT_POLL_HOURS = 1.0


def _poll_due(conn, now: float) -> float | None:
    """None when X's mentions timeline is read this run, else when it next
    is. It is read when it never was, or `X_MENTIONS_POLL_HOURS` (1; 0 reads
    it every run) have passed since the last read that X answered."""
    every = number_setting("X_MENTIONS_POLL_HOURS", DEFAULT_POLL_HOURS, 0.0, 168.0) * 3600.0
    row = conn.execute("SELECT at FROM x_cursor WHERE name = ?", (POLL,)).fetchone()
    if row is None or now - row["at"] >= every:
        return None
    return row["at"] + every


def ingest(max_results: int | None = None, offer: bool = True, x: XApi | None = None, poll: bool = False) -> Mapping[str, Any]:
    """Screens what X's webhook queued and offers every pending mention; once
    an hour (`_poll_due`), or when `poll` forces it, it first reads X's mentions
    timeline for anything the webhook lost. The run in between makes no X
    call at all. Safe to run at any time and twice at once (a lock), and it
    never answers anyone."""
    with hand.lock("x-ingest", timeout=60):
        conn = _db()
        now = hand.now()
        next_poll = None if poll else _poll_due(conn, now)
        counts = {status: 0 for status in STATUSES}
        posts: list[Json] = []
        newest = None
        row = conn.execute("SELECT since_id FROM x_cursor WHERE name = 'mentions'").fetchone()
        since_id = row["since_id"] if row else None
        if next_poll is None:
            x = x or XApi.from_settings()
            wanted = max_results if max_results is not None else int(number_setting("X_MENTIONS_MAX_RESULTS", 20, 5, 100))
            me = cache.reads(x).identity()
            page = x.mentions_page(me["id"], since_id=since_id, max_results=wanted)
            posts = page["posts"]
            cache.remember(posts)
            for post in sorted(posts, key=lambda p: int(p["id"])):
                _store(conn, me, post, counts, now)
            # Every post the page brought back is stored, so the cursor can move:
            # a failed offer is retried from the row, not by reading X again.
            newest = page.get("newestId") or (max((p["id"] for p in posts), key=int) if posts else None)
            if newest:
                _set_cursor(conn, "mentions", newest, now)
            # Only a read X answered counts: a failed one is tried again next run.
            _set_cursor(conn, POLL, None, now)
        screened = {status: 0 for status in STATUSES}
        screen_queued(conn, screened)

        offered, failed = 0, None
        if offer:
            drops = _open_drop_posts(conn)
            for pending in conn.execute(
                    "SELECT post, conversation_id FROM x_mentions WHERE status = 'pending' ORDER BY id").fetchall():
                post = json.loads(pending["post"])
                if pending["conversation_id"] in drops:
                    # An entry to an open Tiny Drop: hands/giveaway reads it from
                    # this row; answering every entry would be reply spam.
                    conn.execute("UPDATE x_mentions SET status = 'skipped', note = 'drop entry', updated_at = ? WHERE id = ?",
                                 (hand.now(), post["id"]))
                    continue
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
        if any(screened.values()):
            result["screened"] = {status: n for status, n in screened.items() if n}
        if next_poll is not None:
            result["poll"] = {"skipped": True, "next_at": rules.iso(next_poll)}
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
    from .. import requests as build_requests

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


def release_held(post_id: str, hold_id: int) -> bool:
    """Puts a mention the guard held back to `pending`, for the next ingest to
    offer, once the guard's re-screen found it clean. Only a row held by that
    very hold moves; False when there is none (answered, skipped, gone, or
    held for another reason)."""
    conn = _db()
    changed = conn.execute(
        "UPDATE x_mentions SET status = 'pending', note = ?, updated_at = ? WHERE id = ? AND status = 'held' AND hold_id = ?",
        ("released: the guard re-screened it clean", hand.now(), post_id, hold_id),
    ).rowcount
    return changed == 1


def _warn(message: str) -> None:
    print(f"hands/x: {message}", file=sys.stderr)
