"""The only way hands/mail writes. Every action runs, in this one invocation:
the body's shape, the guard on every word it would send (`check_fields`,
surface mail), the rules, DRY_RUN, AgentMail, and the ledger (`mail_sent`).

The rules:
- `reply` answers a message that exists in the inbox, within its thread,
  never Teeny's own and never one the inbound guard held, and only once.
- `send` starts a conversation only with an address in `contacts` (the
  operator's `contacts add EMAIL --kind vendor|winner|collaborator`). Never
  cold mail.
- Every reply and send counts toward `MAIL_MAX_SENDS_PER_DAY` (20, UTC day),
  under AgentMail's free plan's 100.
- Under DRY_RUN nothing is written anywhere: AgentMail or the ledger. The
  answer says what would have happened (`would_*`), after every rule ran.
"""

from __future__ import annotations

import sqlite3
import sys
from typing import Any, Mapping

from .. import hand
from ..rest import RestApi, RestError
from . import client, inbox, reads

try:
    from ..guard.gate import check_fields
except ImportError:  # the guard hand is not installed: nothing is sent

    def check_fields(fields: Mapping[str, str], **_: Any) -> Any:
        raise hand.HandError("the content guard (hands/guard) is not installed, so nothing can be sent")


DEFAULT_MAX_SENDS_PER_DAY = 20
CONTACT_KINDS = ("vendor", "winner", "collaborator")
MAX_RECIPIENTS = 5

SCHEMA = (
    inbox.SCHEMA
    + """
CREATE TABLE IF NOT EXISTS mail_sent (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          REAL NOT NULL,
    kind        TEXT NOT NULL,
    message_id  TEXT,
    thread_id   TEXT,
    to_addr     TEXT NOT NULL,
    subject     TEXT,
    in_reply_to TEXT
);
CREATE INDEX IF NOT EXISTS mail_sent_at ON mail_sent (at);
CREATE TABLE IF NOT EXISTS contacts (
    email    TEXT PRIMARY KEY COLLATE NOCASE,
    kind     TEXT NOT NULL,
    note     TEXT,
    added_at REAL NOT NULL
);
"""
)

Json = dict[str, Any]


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def max_sends() -> int:
    return hand.number("MAIL_MAX_SENDS_PER_DAY", DEFAULT_MAX_SENDS_PER_DAY, 0, 100)


def _body(body: Any, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise hand.HandError(f"stdin must be a JSON object with {', '.join(required)}")
    unknown = sorted(set(body) - {*required, *optional})
    if unknown:
        raise hand.HandError(f"unknown field(s) in the body: {', '.join(unknown)}")
    for key in required:
        if key not in body:
            raise hand.HandError(f'"{key}" is required')
    return dict(body)


def _string(body: Mapping[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise hand.HandError(f'"{key}" must be a non-empty string')
    return value


def guarded(fields: Mapping[str, str], kind: str, context: Mapping[str, Any]) -> dict[str, str]:
    verdict = check_fields(dict(fields), surface="mail", kind=kind, context=dict(context))
    if verdict.kind == "allow" and isinstance(verdict.fields, dict) and set(verdict.fields) == set(fields):
        return dict(verdict.fields)
    if verdict.kind == "hold":
        raise hand.Refused("held", category=verdict.category, hold_id=verdict.hold_id, field=verdict.field)
    if verdict.kind == "refuse":
        raise hand.Refused(verdict.reason, rewrite=True, field=verdict.field)
    raise hand.HandError(f"the guard answered {verdict.kind!r}, which hands/mail does not send on")


def _rate_limited(error: RestError) -> hand.Refused:
    detail: dict[str, Any] = {"rule": "rate_limit"}
    if error.retry_after is not None:
        detail["wait_seconds"] = int(error.retry_after) + 1
    return hand.Refused("AgentMail says slow down (429); nothing was retried", **detail)


def _check_cap(conn: sqlite3.Connection) -> None:
    cap = max_sends()
    sent = conn.execute("SELECT count(*) FROM mail_sent WHERE at >= ?", (hand.day_start(),)).fetchone()[0]
    if sent >= cap:
        raise hand.Refused(f"today's {cap} e-mail(s) are spent (UTC)", rule="daily_cap")


def _record(conn: sqlite3.Connection, kind: str, got: Any, to: str, subject: str | None, in_reply_to: str | None) -> None:
    got = got if isinstance(got, Mapping) else {}
    try:
        conn.execute(
            "INSERT INTO mail_sent (at, kind, message_id, thread_id, to_addr, subject, in_reply_to) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (hand.now(), kind, got.get("message_id"), got.get("thread_id"), to, subject, in_reply_to),
        )
    except Exception as exc:  # noqa: BLE001 - logged, never raised: the mail went
        print(f"hands/mail: sent a {kind} but could not record it: {type(exc).__name__}", file=sys.stderr)


def reply(body: Any, api: RestApi | None = None) -> Json:
    body = _body(body, ("message_id", "body"))
    mid = client.message_id(body["message_id"])
    text = guarded({"body": _string(body, "body")}, "reply", {"message_id": mid})["body"]
    conn = db()
    row = conn.execute("SELECT status FROM mail_messages WHERE message_id = ?", (mid,)).fetchone()
    if row is not None and row["status"] == "held":
        raise hand.Refused("the inbound guard held that message; nothing answers it", rule="held")
    api = api or client.api()
    with hand.lock("mail-send"):
        done = conn.execute("SELECT message_id FROM mail_sent WHERE in_reply_to = ?", (mid,)).fetchone()
        if done is not None:
            raise hand.Refused("Teeny already replied to that message", rule="replied", reply_id=done["message_id"])
        _check_cap(conn)
        try:
            original = reads.fetch(mid, api)
        except RestError as error:
            if error.status == 404:
                raise hand.Refused("no such message in Teeny's inbox; a reply answers an existing thread", rule="no_thread") from None
            raise
        if client.is_own(original):
            raise hand.Refused("that message is Teeny's own", rule="own")
        to = client.address_of(original.get("from")) or str(original.get("from"))
        subject = original.get("subject")
        if hand.dry_run():
            return {"dry_run": True, "would_reply": {"message_id": mid, "to": to, "subject": subject, "body": text}}
        try:
            got = api.call("POST", client.inbox_path("messages", mid, "reply"), body={"text": text})
        except RestError as error:
            if error.status == 429:
                raise _rate_limited(error) from None
            raise
        _record(conn, "reply", got, to, subject, mid)
    return {"replied": mid, "to": to, "message_id": got.get("message_id") if isinstance(got, Mapping) else None,
            "thread_id": got.get("thread_id") if isinstance(got, Mapping) else None}


def _recipients(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    if not items or len(items) > MAX_RECIPIENTS:
        raise hand.HandError(f'"to" is one address or a list of at most {MAX_RECIPIENTS}')
    return list(dict.fromkeys(client.address(item) for item in items))


def send(body: Any, api: RestApi | None = None) -> Json:
    body = _body(body, ("to", "subject", "body"))
    to = _recipients(body["to"])
    fields = guarded({"subject": _string(body, "subject"), "body": _string(body, "body")}, "message", {"to": to})
    conn = db()
    strangers = [a for a in to if not conn.execute("SELECT 1 FROM contacts WHERE email = ?", (a,)).fetchone()]
    if strangers:
        raise hand.Refused(
            "Teeny only starts mail with its contacts (hands/mail contacts add); never cold mail: " + ", ".join(strangers),
            rule="contacts",
            not_contacts=strangers,
        )
    with hand.lock("mail-send"):
        _check_cap(conn)
        if hand.dry_run():
            return {"dry_run": True, "would_send": {"to": to, **fields}}
        api = api or client.api()
        try:
            got = api.call("POST", client.inbox_path("messages", "send"), body={"to": to, "subject": fields["subject"], "text": fields["body"]})
        except RestError as error:
            if error.status == 429:
                raise _rate_limited(error) from None
            raise
        _record(conn, "send", got, ",".join(to), fields["subject"], None)
    got = got if isinstance(got, Mapping) else {}
    return {"sent": to, "message_id": got.get("message_id"), "thread_id": got.get("thread_id")}


def label(body: Any, api: RestApi | None = None) -> Json:
    body = _body(body, ("message_id",), ("add", "remove"))
    mid = client.message_id(body["message_id"])
    changes: dict[str, list[str]] = {}
    for key in ("add", "remove"):
        value = body.get(key) or []
        if not isinstance(value, list):
            raise hand.HandError(f'"{key}" must be a list of labels')
        if value:
            changes[f"{key}_labels"] = [client.label(v) for v in value]
    if not changes:
        raise hand.HandError('a label change needs "add" or "remove"')
    if hand.dry_run():
        return {"dry_run": True, "would_label": mid, **changes}
    api = api or client.api()
    try:
        got = api.call("PATCH", client.inbox_path("messages", mid), body=changes)
    except RestError as error:
        if error.status == 429:
            raise _rate_limited(error) from None
        raise
    return {"labelled": mid, "labels": got.get("labels") if isinstance(got, Mapping) else None}


# --- contacts (operator) ------------------------------------------------------------------------

_NOTE_CHARS = 500


def contacts_add(email: str, kind: str, note: str | None) -> Json:
    address = client.address(email, "EMAIL")
    if kind not in CONTACT_KINDS:
        raise hand.HandError(f"--kind is one of {', '.join(CONTACT_KINDS)}")
    conn = db()
    conn.execute(
        "INSERT INTO contacts (email, kind, note, added_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(email) DO UPDATE SET kind = excluded.kind, note = excluded.note",
        (address, kind, (note or "")[:_NOTE_CHARS] or None, hand.now()),
    )
    return {"contact": address, "kind": kind}


def contacts_list() -> Json:
    conn = db()
    rows = conn.execute("SELECT email, kind, note, added_at FROM contacts ORDER BY email").fetchall()
    return {"contacts": [{k: r[k] for k in r.keys() if r[k] is not None} for r in rows]}


def contacts_remove(email: str) -> Json:
    address = client.address(email, "EMAIL")
    if not db().execute("DELETE FROM contacts WHERE email = ?", (address,)).rowcount:
        raise hand.Refused("no such contact", email=address)
    return {"removed": address}

