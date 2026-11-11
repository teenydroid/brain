"""The only way hands/telegram writes. `send`, in this one invocation: the
body's shape, the guard on every word (surface telegram), the rules,
DRY_RUN, Telegram, and the ledger (`telegram_sent`).

The rules:
- Only to a chat Teeny has seen (`telegram_chats`): a group it was added
  to, or a DM someone opened. Never a cold message. A stranger's DM only
  while TELEGRAM_OPEN_DMS is on; an owner's always.
- `reply_to`, when given, is a message Teeny read in that chat, never one
  the inbound guard held.
- At most TELEGRAM_MAX_SENDS_PER_DAY (60, UTC day) and
  TELEGRAM_MAX_PER_CHAT_HOUR (8) in any one chat in the last hour, so Teeny
  never floods a group.
- Under DRY_RUN nothing is written anywhere; the answer says what would
  have been sent (`would_send`), after every rule ran.
"""

from __future__ import annotations

import sqlite3
import sys
from typing import Any, Mapping

from .. import hand
from ..rest import RestError
from . import client, inbox

try:
    from ..guard.gate import check_fields
except ImportError:  # the guard hand is not installed: nothing is sent

    def check_fields(fields: Mapping[str, str], **_: Any) -> Any:
        raise hand.HandError("the content guard (hands/guard) is not installed, so nothing can be sent")


DEFAULT_MAX_PER_DAY = 60
DEFAULT_MAX_PER_CHAT_HOUR = 8

SCHEMA = inbox.SCHEMA + """
CREATE TABLE IF NOT EXISTS telegram_sent (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         REAL NOT NULL,
    chat_id    INTEGER NOT NULL,
    message_id INTEGER,
    reply_to   INTEGER
);
CREATE INDEX IF NOT EXISTS telegram_sent_at ON telegram_sent (at);
"""

Json = dict[str, Any]


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def _int(body: Mapping[str, Any], key: str, required: bool = True) -> int | None:
    value = body.get(key)
    if value in (None, "") and not required:
        return None
    if isinstance(value, str) and value.lstrip("-").isdigit():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool):
        raise hand.HandError(f'"{key}" must be a number')
    return value


def guarded(text: str, kind: str, context: Mapping[str, Any]) -> str:
    verdict = check_fields({"text": text}, surface="telegram", kind=kind, context=dict(context))
    if verdict.kind == "allow" and isinstance(verdict.fields, dict) and set(verdict.fields) == {"text"}:
        return verdict.fields["text"]
    if verdict.kind == "hold":
        raise hand.Refused("held", category=verdict.category, hold_id=verdict.hold_id, field=verdict.field)
    if verdict.kind == "refuse":
        raise hand.Refused(verdict.reason, rewrite=True, field=verdict.field)
    raise hand.HandError(f"the guard answered {verdict.kind!r}, which hands/telegram does not send on")


def send(body: Any, bot: client.Bot | None = None) -> Json:
    if not isinstance(body, Mapping):
        raise hand.HandError("stdin must be a JSON object with chat_id and text")
    unknown = sorted(set(body) - {"chat_id", "text", "reply_to"})
    if unknown:
        raise hand.HandError(f"unknown field(s) in the body: {', '.join(unknown)}")
    chat_id, reply_to = _int(body, "chat_id"), _int(body, "reply_to", required=False)
    raw = body.get("text")
    if not isinstance(raw, str) or not raw.strip():
        raise hand.HandError('"text" must be a non-empty string')
    conn = db()
    chat = conn.execute("SELECT * FROM telegram_chats WHERE chat_id = ?", (chat_id,)).fetchone()
    if chat is None:
        raise hand.Refused("Teeny only writes in chats it is in or was written to from; never cold", rule="unknown_chat")
    if chat["type"] == "private" and chat_id not in client.owners() and not client.open_dms():
        raise hand.Refused("DMs with people other than the owners are closed (TELEGRAM_OPEN_DMS)", rule="closed_dms")
    allowed = client.allowed_chats()
    if chat["type"] != "private" and allowed is not None and chat_id not in allowed:
        raise hand.Refused("Teeny does not take part in that group (TELEGRAM_CHATS)", rule="chat_not_allowed")
    if reply_to is not None:
        row = conn.execute("SELECT status FROM telegram_messages WHERE chat_id = ? AND message_id = ?",
                           (chat_id, reply_to)).fetchone()
        if row is None:
            raise hand.Refused("reply_to is not a message Teeny read in that chat", rule="unknown_message")
        if row["status"] == "held":
            raise hand.Refused("the inbound guard held that message; nothing answers it", rule="held")
    text = guarded(raw, "reply" if reply_to is not None else "message", {"chat_id": chat_id, "reply_to": reply_to})
    with hand.lock("telegram-send"):
        cap = hand.number("TELEGRAM_MAX_SENDS_PER_DAY", DEFAULT_MAX_PER_DAY, 0, 500)
        if conn.execute("SELECT count(*) FROM telegram_sent WHERE at >= ?", (hand.day_start(),)).fetchone()[0] >= cap:
            raise hand.Refused(f"today's {cap} Telegram message(s) are spent (UTC)", rule="daily_cap")
        per_chat = hand.number("TELEGRAM_MAX_PER_CHAT_HOUR", DEFAULT_MAX_PER_CHAT_HOUR, 0, 60)
        recent = conn.execute("SELECT count(*) FROM telegram_sent WHERE chat_id = ? AND at >= ?",
                              (chat_id, hand.now() - 3600)).fetchone()[0]
        if recent >= per_chat:
            raise hand.Refused(f"already {recent} message(s) in that chat this hour; let others talk", rule="chat_rate")
        if hand.dry_run():
            return {"dry_run": True, "would_send": {"chat_id": chat_id, "reply_to": reply_to, "text": text}}
        bot = bot or client.bot()
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to is not None:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        try:
            try:
                bot.call("sendChatAction", chat_id=chat_id, action="typing")
            except RestError:
                pass  # only a nicety
            got = bot.call("sendMessage", **params)
        except RestError as error:
            if error.status == 429:
                detail = {"wait_seconds": int(error.retry_after) + 1} if error.retry_after is not None else {}
                raise hand.Refused("Telegram says slow down (429); nothing was retried", rule="rate_limit", **detail) from None
            raise
        sent_id = got.get("message_id") if isinstance(got, Mapping) else None
        try:
            with hand.transaction(conn):
                conn.execute("INSERT INTO telegram_sent (at, chat_id, message_id, reply_to) VALUES (?,?,?,?)",
                             (hand.now(), chat_id, sent_id, reply_to))
                if reply_to is not None:
                    conn.execute("UPDATE telegram_messages SET status = 'replied', updated_at = ? WHERE chat_id = ? AND message_id = ?",
                                 (hand.now(), chat_id, reply_to))
        except Exception as exc:  # noqa: BLE001 - the message went; say so
            print(f"hands/telegram: sent but could not record it: {type(exc).__name__}", file=sys.stderr)
    return {"sent": chat_id, "message_id": sent_id, "reply_to": reply_to}
