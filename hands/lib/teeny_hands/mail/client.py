"""Where AgentMail is, which inbox is Teeny's, and the shapes it answers in."""

from __future__ import annotations

import html
import re
import urllib.parse
from typing import Any, Mapping

from .. import hand
from ..rest import RestApi, quote

#: `AGENTMAIL_API_BASE` moves it (the tests' fake).
API_BASE = "https://api.agentmail.to/v0"
_LOCAL = ("127.0.0.1", "localhost", "::1")
_TEXT_CHARS = 8000

_ADDRESS = re.compile(r"<([^<>@\s]+@[^<>@\s]+)>")
_BARE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
_LABEL = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,40}$")

Json = dict[str, Any]


def base() -> str:
    value = hand.setting("AGENTMAIL_API_BASE", API_BASE) or API_BASE
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "https" and (parts.hostname or "") not in _LOCAL:
        raise hand.HandError("AGENTMAIL_API_BASE must be https")
    return value


def api() -> RestApi:
    return RestApi("AgentMail", base(), hand.require("AGENTMAIL_API_KEY"), "teeny-hands-mail")


def inbox() -> str:
    """Teeny's inbox id (its address), `AGENTMAIL_INBOX`."""
    return hand.require("AGENTMAIL_INBOX").strip()


def inbox_path(*parts: str) -> str:
    return "/" + "/".join(quote(p) for p in ("inboxes", inbox(), *parts))


def message_id(value: Any, what: str = "message_id") -> str:
    """A message or thread id as AgentMail gives them (an RFC Message-ID such
    as <abc@host> for messages): any printable text without whitespace,
    quoted as one path segment, never more."""
    if not isinstance(value, str) or not value.strip() or len(value) > 500 or any(c.isspace() or ord(c) < 32 for c in value.strip()):
        raise hand.HandError(f'"{what}" must be an AgentMail id')
    return value.strip()


def address(value: Any, what: str = "to") -> str:
    """One e-mail address, lower-cased; "Name <a@b.c>" is read as a@b.c."""
    if not isinstance(value, str):
        raise hand.HandError(f'"{what}" must be an e-mail address')
    found = _ADDRESS.search(value)
    candidate = (found.group(1) if found else value).strip().lower()
    if not _BARE.match(candidate) or len(candidate) > 254:
        raise hand.HandError(f'"{what}" must be an e-mail address')
    return candidate


def address_of(value: Any) -> str | None:
    try:
        return address(value)
    except hand.HandError:
        return None


def label(value: Any) -> str:
    if not isinstance(value, str) or not _LABEL.match(value):
        raise hand.HandError("a label is 1-41 lowercase letters, digits, _ . and -")
    return value


def clip(text: Any, limit: int = _TEXT_CHARS) -> str | None:
    if not isinstance(text, str):
        return None
    return text if len(text) <= limit else text[:limit] + "…"


def body_text(message: Mapping[str, Any]) -> str:
    """A message's own words: the extracted new text, else the plain text,
    else the HTML with its tags taken out, else the preview."""
    for key in ("extracted_text", "text"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value
    markup = message.get("extracted_html") or message.get("html")
    if isinstance(markup, str) and markup.strip():
        markup = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", markup)
        return html.unescape(re.sub(r"<[^>]+>", " ", markup))
    preview = message.get("preview")
    return preview if isinstance(preview, str) else ""


def is_own(message: Mapping[str, Any]) -> bool:
    """Sent by Teeny's inbox (AgentMail lists both directions)."""
    labels = message.get("labels") if isinstance(message.get("labels"), list) else []
    if "sent" in labels:
        return True
    sender = address_of(message.get("from"))
    try:
        mine = inbox().lower()
    except hand.HandError:
        return False
    return sender is not None and sender == mine


def summary(message: Mapping[str, Any]) -> Json:
    out: Json = {
        key: message.get(key)
        for key in ("message_id", "thread_id", "from", "to", "subject", "labels", "timestamp")
        if message.get(key) is not None
    }
    if message.get("preview"):
        out["preview"] = clip(message.get("preview"), 400)
    return out


def full(message: Mapping[str, Any]) -> Json:
    out = summary(message)
    for key in ("cc", "in_reply_to"):
        if message.get(key):
            out[key] = message[key]
    out["text"] = clip(body_text(message))
    armor = message.get("agent_armor")
    if isinstance(armor, Mapping) and armor.get("verdict"):
        out["agent_armor"] = armor.get("verdict")
    return out
