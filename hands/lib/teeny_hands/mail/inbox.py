"""Mail intake: what people wrote to Teeny, read once, screened, and offered
to the core as inbound events, as `x ingest` does for mentions.

`ingest` lists the inbox from a cursor kept in `mail_cursor` (the newest
timestamp stored), oldest first. Each new message becomes a row in
`mail_messages`, unique on its message id:

  pending  stored, not yet offered (or the offer failed; the next run retries)
  held     the inbound guard held it, or AgentMail's Agent Armor said review;
           nothing answers it
  skipped  never offered: Teeny's own sent mail
  offered  in the core's queue (`openhuman.inbound_offer`, source mail:inbox,
           keyed on the thread, so a thread's messages are drafted together)
  drafted  a draft came back and went through `hands/mail reply` under DRY_RUN
  replied  answered
  refused  `hands/mail reply` refused the draft (a rule or the guard)

A pending message that asks Teeny to build something is also recorded in
`build_requests` (teeny_hands.requests). Nothing here sends mail.
"""

from __future__ import annotations

import sqlite3
import sys
from typing import Any, Mapping

from .. import hand
from .. import requests as build_requests
from ..rest import RestApi
from . import client, reads

SOURCE = "mail:inbox"
STATUSES = ("pending", "held", "skipped", "offered", "drafted", "replied", "refused")
MARKS = ("drafted", "replied", "refused", "skipped", "held")
_OFFER_CHARS = 6000

SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS mail_cursor (
    name  TEXT PRIMARY KEY,
    after TEXT NOT NULL,
    at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mail_messages (
    message_id  TEXT PRIMARY KEY,
    thread_id   TEXT,
    sender      TEXT,
    subject     TEXT,
    received_at TEXT,
    text        TEXT NOT NULL,
    status      TEXT NOT NULL,
    note        TEXT,
    hold_id     INTEGER,
    seen_at     REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS mail_messages_status ON mail_messages (status, seen_at);
"""
    + build_requests.SCHEMA
)


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def _screen(text: str, message: Mapping[str, Any]) -> Any:
    """The inbound guard's verdict. Imported by name, so a test can stand in
    for the other hand's gate."""
    from ..guard.gate import inbound

    return inbound(text, surface="mail", context={"message_id": message.get("message_id"), "from": message.get("from")})


def _offer(row: Mapping[str, Any]) -> None:
    hand.core(
        "inbound_offer",
        {
            "source": SOURCE,
            "key": row["thread_id"] or row["message_id"],
            "event_id": row["message_id"],
            "text": row["text"],
            "payload": {
                "message_id": row["message_id"],
                "thread_id": row["thread_id"],
                "from": row["sender"],
                "subject": row["subject"],
                "received_at": row["received_at"],
            },
        },
        timeout=30.0,
    )


def ingest(max_results: int = 20, offer: bool = True, api: RestApi | None = None) -> Mapping[str, Any]:
    """Reads new mail into `mail_messages` and offers every pending one. Safe
    to run at any time and twice at once (a lock); it never sends anything."""
    api = api or client.api()
    with hand.lock("mail-ingest", timeout=60):
        conn = db()
        row = conn.execute("SELECT after FROM mail_cursor WHERE name = 'inbox'").fetchone()
        after = row["after"] if row else None
        page = reads.list_page(api, max_results, after=after, ascending=True)
        counts = {status: 0 for status in STATUSES}
        requests_noted = 0
        newest = after
        for item in page:
            mid = item.get("message_id")
            stamp = item.get("timestamp")
            if isinstance(stamp, str) and (newest is None or stamp > newest):
                newest = stamp
            if not isinstance(mid, str) or conn.execute("SELECT 1 FROM mail_messages WHERE message_id = ?", (mid,)).fetchone():
                continue
            status, note, hold_id = "pending", None, None
            message: Mapping[str, Any] = item
            if client.is_own(item):
                status, note = "skipped", "own message"
            else:
                message = reads.fetch(mid, api)
            sender = message.get("from")
            subject = message.get("subject") or ""
            body = client.body_text(message)
            text = f"From: {sender}\nSubject: {subject}\n\n{body}"[:_OFFER_CHARS]
            if status == "pending":
                armor = message.get("agent_armor")
                if isinstance(armor, Mapping) and armor.get("verdict") == "review":
                    status, note = "held", "agent armor: review"
                else:
                    verdict = _screen(f"{subject}\n\n{body}", message)
                    if verdict.kind != "allow":
                        status, note, hold_id = "held", verdict.reason, verdict.hold_id
            with hand.transaction(conn):
                conn.execute(
                    "INSERT OR IGNORE INTO mail_messages (message_id, thread_id, sender, subject, received_at, text,"
                    " status, note, hold_id, seen_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (mid, message.get("thread_id"), sender, subject, stamp, text, status, note, hold_id, hand.now(), hand.now()),
                )
                if status == "pending" and build_requests.looks_like_build_request(f"{subject}\n{body}"):
                    if build_requests.record(SOURCE, mid, client.address_of(sender) or sender, f"{subject}\n\n{body}", conn):
                        requests_noted += 1
            counts[status] += 1
        if newest and newest != after:
            conn.execute(
                "INSERT INTO mail_cursor (name, after, at) VALUES ('inbox', ?, ?)"
                " ON CONFLICT(name) DO UPDATE SET after = excluded.after, at = excluded.at",
                (newest, hand.now()),
            )

        offered, failed = 0, None
        if offer:
            for pending in conn.execute("SELECT * FROM mail_messages WHERE status = 'pending' ORDER BY seen_at, message_id").fetchall():
                try:
                    _offer(pending)
                except hand.CoreError as exc:
                    failed = str(exc)
                    break
                conn.execute(
                    "UPDATE mail_messages SET status = 'offered', updated_at = ? WHERE message_id = ?",
                    (hand.now(), pending["message_id"]),
                )
                offered += 1
        result: dict[str, Any] = {
            "read": len(page),
            "new": {s: n for s, n in counts.items() if n},
            "offered": offered,
            "build_requests": requests_noted,
            "cursor": newest,
        }
        if failed:
            print(f"hands/mail: offer failed, left pending: {failed}", file=sys.stderr)
            result["offer_error"] = failed
        return result


def queue(status: str | None = None, limit: int = 50) -> Mapping[str, Any]:
    conn = db()
    query = "SELECT message_id, thread_id, sender, subject, status, note, hold_id FROM mail_messages"
    params: tuple[Any, ...] = ()
    if status:
        if status not in STATUSES:
            raise hand.HandError(f"status must be one of {', '.join(STATUSES)}")
        query += " WHERE status = ?"
        params = (status,)
    rows = conn.execute(query + " ORDER BY seen_at DESC LIMIT ?", (*params, limit)).fetchall()
    return {"messages": [{k: r[k] for k in r.keys() if r[k] is not None} for r in rows]}


def mark(message_id: str, status: str, note: str | None = None) -> Mapping[str, Any]:
    if status not in MARKS:
        raise hand.HandError(f"status must be one of {', '.join(MARKS)}")
    conn = db()
    changed = conn.execute(
        "UPDATE mail_messages SET status = ?, note = ?, updated_at = ? WHERE message_id = ?",
        (status, note, hand.now(), message_id),
    ).rowcount
    if not changed:
        raise hand.Refused("unknown message", id=message_id)
    return {"id": message_id, "status": status}
