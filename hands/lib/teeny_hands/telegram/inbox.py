"""Telegram intake: what was said to Teeny, and around it, read once,
screened, and handed to its mind (the stimuli inbox).

`ingest` reads the bot's updates from a cursor (`telegram_cursor`, the last
update id). Each text message becomes a row in `telegram_messages`, keyed
`<chat id>:<message id>`:

  offered  reached the mind
  held     the inbound guard held it; nothing answers it
  skipped  a bot (Teeny included), a group Teeny does not take part in
           (TELEGRAM_CHATS), or a stranger's DM while DMs are closed
           (TELEGRAM_OPEN_DMS)
  seen     group chatter: gathered, with the rest of that chat's new lines,
           into one `telegram · chatter` stimulus per chat per run
  replied  Teeny answered it

What reaches the mind:

- a DM from an owner (TELEGRAM_OWNERS) as `telegram · dm`, marked
  `owner`, so it wakes Teeny even at night: this is how its makers talk
  to it like a person;
- any other DM (only with TELEGRAM_OPEN_DMS=1), as `telegram · dm`;
- a group message that is for Teeny (names @its_bot, answers one of its
  messages, or says "teeny") as `telegram · mention`;
- the rest of a group's talk as one `telegram · chatter` stimulus, so Teeny
  can read the room and join in when it has something to say.

Every chat seen is remembered in `telegram_chats`; hands/telegram send only
writes to those. Nothing here sends anything.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Mapping

from .. import hand
from . import client

STATUSES = ("offered", "held", "skipped", "seen", "replied")
TEXT_CHARS = 4000
CHATTER_LINES = 15
CHATTER_CHARS = 3000

SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_cursor (
    name   TEXT PRIMARY KEY,
    offset INTEGER NOT NULL,
    at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS telegram_chats (
    chat_id    INTEGER PRIMARY KEY,
    type       TEXT NOT NULL,
    title      TEXT,
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS telegram_messages (
    id          TEXT PRIMARY KEY,
    chat_id     INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    sender_id   INTEGER,
    sender      TEXT,
    text        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL,
    note        TEXT,
    hold_id     INTEGER,
    sent_at     REAL,
    seen_at     REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS telegram_messages_chat ON telegram_messages (chat_id, message_id);
"""


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def _screen(text: str, context: Mapping[str, Any]) -> Any:
    """The inbound guard's verdict (imported by name, so a test can stand in)."""
    from ..guard.gate import inbound

    return inbound(text, surface="telegram", context=dict(context))


def _me(bot: client.Bot) -> Mapping[str, Any]:
    me = bot.call("getMe")
    if not isinstance(me, Mapping) or not isinstance(me.get("id"), int):
        raise hand.HandError("Telegram's getMe answered something unreadable")
    return me


def _for_teeny(message: Mapping[str, Any], text: str, me: Mapping[str, Any]) -> bool:
    username = str(me.get("username") or "")
    if username and f"@{username.lower()}" in text.lower():
        return True
    replied = message.get("reply_to_message")
    if isinstance(replied, Mapping) and isinstance(replied.get("from"), Mapping) and replied["from"].get("id") == me["id"]:
        return True
    return re.search(r"\bteeny\b", text, re.IGNORECASE) is not None


def _chat_name(chat: Mapping[str, Any]) -> str:
    return str(chat.get("title") or chat.get("username") or chat.get("id"))


def ingest(limit: int = 100, bot: client.Bot | None = None) -> Mapping[str, Any]:
    """Reads new updates and hands what matters to the mind. Safe to run at
    any time (a lock); never sends anything."""
    from .. import stimuli

    bot = bot or client.bot()
    with hand.lock("telegram-ingest", timeout=60):
        conn = db()
        row = conn.execute("SELECT offset FROM telegram_cursor WHERE name = 'updates'").fetchone()
        offset = row["offset"] if row else 0
        me = _me(bot)
        updates = bot.call("getUpdates", offset=offset, timeout=0, limit=limit, allowed_updates=["message"])
        updates = updates if isinstance(updates, list) else []
        owners, chats_allowed, dms_open = client.owners(), client.allowed_chats(), client.open_dms()
        counts = {s: 0 for s in STATUSES}
        chatter: dict[int, list[Mapping[str, Any]]] = {}
        newest = offset
        for update in updates:
            uid = update.get("update_id") if isinstance(update, Mapping) else None
            if isinstance(uid, int):
                newest = max(newest, uid + 1)
            message = update.get("message") if isinstance(update, Mapping) else None
            if not isinstance(message, Mapping) or not isinstance(message.get("chat"), Mapping):
                continue
            text = message.get("text") or message.get("caption")
            if not isinstance(text, str) or not text.strip():
                continue
            chat, sender = message["chat"], message.get("from") or {}
            chat_id, mid = chat.get("id"), message.get("message_id")
            if not isinstance(chat_id, int) or not isinstance(mid, int):
                continue
            key = f"{chat_id}:{mid}"
            if conn.execute("SELECT 1 FROM telegram_messages WHERE id = ?", (key,)).fetchone():
                continue
            private = chat.get("type") == "private"
            owner = private and sender.get("id") in owners
            kind = "dm" if private else ("mention" if _for_teeny(message, text, me) else "chatter")
            status, note, hold_id = None, None, None
            if sender.get("is_bot"):
                status, note = "skipped", "a bot"
            elif private and not owner and not dms_open:
                status, note = "skipped", "DMs are closed"
            elif not private and chats_allowed is not None and chat_id not in chats_allowed:
                status, note = "skipped", "a group Teeny does not take part in"
            else:
                verdict = _screen(text, {"chat_id": chat_id, "message_id": mid})
                if verdict.kind != "allow":
                    status, note, hold_id = "held", verdict.reason, verdict.hold_id
            if status is None:
                status = "seen" if kind == "chatter" else "offered"
            now = hand.now()
            with hand.transaction(conn):
                if status != "skipped" or private:
                    conn.execute(
                        "INSERT INTO telegram_chats (chat_id, type, title, first_seen, last_seen) VALUES (?,?,?,?,?)"
                        " ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title, last_seen = excluded.last_seen",
                        (chat_id, str(chat.get("type")), _chat_name(chat), now, now))
                conn.execute(
                    "INSERT INTO telegram_messages (id, chat_id, message_id, sender_id, sender, text, kind, status, note,"
                    " hold_id, sent_at, seen_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, chat_id, mid, sender.get("id"), client.name_of(sender), text[:TEXT_CHARS], kind, status, note,
                     hold_id, message.get("date"), now, now))
            counts[status] += 1
            who = client.name_of(sender)
            payload = {"chat_id": chat_id, "message_id": mid, "chat": _chat_name(chat), "chat_type": chat.get("type")}
            if status == "offered":
                if owner:
                    payload["owner"] = True
                where = "in a DM" if private else f"in {_chat_name(chat)}"
                stimuli.add("telegram", kind, key, f"{who} {where}:\n{text[:TEXT_CHARS]}", who=who, payload=payload)
            elif status == "seen":
                chatter.setdefault(chat_id, []).append({**payload, "who": who, "text": text})
        for chat_id, lines in chatter.items():
            lines = lines[-CHATTER_LINES:]
            body = "\n".join(f"- {l['who']}: {l['text'][:400]}" for l in lines)[-CHATTER_CHARS:]
            last = lines[-1]
            stimuli.add("telegram", "chatter", f"{chat_id}:{last['message_id']}",
                        f"The talk in {last['chat']} since you last looked:\n{body}\n"
                        f"Join in only if you have something to add (reply_to one of these message ids, or just say it).",
                        payload={"chat_id": chat_id, "chat": last["chat"], "message_ids": [l["message_id"] for l in lines]})
        if newest != offset:
            conn.execute(
                "INSERT INTO telegram_cursor (name, offset, at) VALUES ('updates', ?, ?)"
                " ON CONFLICT(name) DO UPDATE SET offset = excluded.offset, at = excluded.at", (newest, hand.now()))
        return {"read": len(updates), "new": {s: n for s, n in counts.items() if n}, "chatter": len(chatter),
                "cursor": newest, "bot": me.get("username")}


def chats() -> Mapping[str, Any]:
    rows = db().execute("SELECT chat_id, type, title, last_seen FROM telegram_chats ORDER BY last_seen DESC").fetchall()
    return {"chats": [dict(r) for r in rows]}


def queue(status: str | None = None, limit: int = 50) -> Mapping[str, Any]:
    query, params = "SELECT id, chat_id, message_id, sender, kind, status, note FROM telegram_messages", ()
    if status:
        if status not in STATUSES:
            raise hand.HandError(f"status must be one of {', '.join(STATUSES)}")
        query, params = query + " WHERE status = ?", (status,)
    rows = db().execute(query + " ORDER BY seen_at DESC LIMIT ?", (*params, limit)).fetchall()
    return {"messages": [{k: r[k] for k in r.keys() if r[k] is not None} for r in rows]}
