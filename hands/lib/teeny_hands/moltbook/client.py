"""Where Moltbook's API is, and the names that may reach a path.

Moltbook's own warning: always https://www.moltbook.com. The bare domain
redirects to www and the redirect drops (or, followed naively, replays) the
Authorization header. So the base is refused if it names the bare domain, and
the transport (`teeny_hands.rest`) never follows a redirect at all.
"""

from __future__ import annotations

import re
import urllib.parse

from .. import hand
from ..rest import RestApi

#: `MOLTBOOK_API_BASE` moves it (the tests' fake).
API_BASE = "https://www.moltbook.com/api/v1"
_LOCAL = ("127.0.0.1", "localhost", "::1")

_AGENT = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_SUBMOLT = re.compile(r"^[a-z0-9][a-z0-9_-]{1,29}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def base() -> str:
    value = hand.setting("MOLTBOOK_API_BASE", API_BASE) or API_BASE
    parts = urllib.parse.urlsplit(value)
    host = (parts.hostname or "").lower()
    if host == "moltbook.com":
        raise hand.HandError("MOLTBOOK_API_BASE must be https://www.moltbook.com: the bare domain redirects and drops the key")
    if parts.scheme != "https" and host not in _LOCAL:
        raise hand.HandError("MOLTBOOK_API_BASE must be https")
    return value


def api(auth: bool = True) -> RestApi:
    """The client secrets/teeny.env describes. `auth` False needs no key
    (registration is the one call made without one)."""
    return RestApi("Moltbook", base(), hand.require("MOLTBOOK_API_KEY") if auth else None, "teeny-hands-moltbook")


def agent_name(value: object, what: str = "name") -> str:
    if not isinstance(value, str) or not _AGENT.match(value.strip().removeprefix("u/")):
        raise hand.HandError(f'"{what}" must be a Moltbook agent name (letters, digits, _ and -)')
    return value.strip().removeprefix("u/")


def submolt_name(value: object, what: str = "submolt") -> str:
    if not isinstance(value, str):
        raise hand.HandError(f'"{what}" must be a submolt name')
    name = value.strip().removeprefix("m/").lower()
    if not _SUBMOLT.match(name):
        raise hand.HandError(f'"{what}" must be a submolt name (2-30 lowercase letters, digits, _ and -)')
    return name


def object_id(value: object, what: str) -> str:
    if not isinstance(value, str) or not _ID.match(value.strip()):
        raise hand.HandError(f'"{what}" must be a Moltbook id')
    return value.strip()
