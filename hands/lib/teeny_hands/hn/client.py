"""Where Hacker News's two APIs are, and the ids and names that may reach a
path.

The official API (https://hacker-news.firebaseio.com/v0) serves stories,
comments and users as JSON, one item per request; Algolia's
(https://hn.algolia.com/api/v1) serves search. Neither takes a key, so every
call is made with `auth=False` and the transport (`teeny_hands.rest`) still
never follows a redirect or names a URL in an error.
"""

from __future__ import annotations

import re
import urllib.parse

from .. import hand
from ..rest import RestApi

#: `HN_API_BASE` and `HN_SEARCH_BASE` move them (the tests' fake).
API_BASE = "https://hacker-news.firebaseio.com/v0"
SEARCH_BASE = "https://hn.algolia.com/api/v1"
_LOCAL = ("127.0.0.1", "localhost", "::1")

_USER = re.compile(r"^[A-Za-z0-9_-]{2,15}$")
#: HN's ids are positive and, for now, eight digits.
_MAX_ID = 10**10


def _base(name: str, default: str) -> str:
    value = hand.setting(name, default) or default
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "https" and (parts.hostname or "").lower() not in _LOCAL:
        raise hand.HandError(f"{name} must be https")
    return value


def api() -> RestApi:
    """The official API."""
    return RestApi("Hacker News", _base("HN_API_BASE", API_BASE), None, "teeny-hands-hn")


def search_api() -> RestApi:
    """Algolia's search over Hacker News."""
    return RestApi("HN search", _base("HN_SEARCH_BASE", SEARCH_BASE), None, "teeny-hands-hn")


def item_id(value: object, what: str = "ID") -> int:
    if isinstance(value, bool):
        raise hand.HandError(f'"{what}" must be a Hacker News item id')
    try:
        number = int(str(value).strip())
    except ValueError:
        raise hand.HandError(f'"{what}" must be a Hacker News item id') from None
    if not 0 < number < _MAX_ID:
        raise hand.HandError(f'"{what}" must be a Hacker News item id')
    return number


def user_name(value: object, what: str = "NAME") -> str:
    if not isinstance(value, str) or not _USER.match(value.strip()):
        raise hand.HandError(f'"{what}" must be a Hacker News user name (2-15 letters, digits, _ and -)')
    return value.strip()
