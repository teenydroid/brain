"""Where Vultr is, Teeny's key, and what is kept out of its answers."""

from __future__ import annotations

import urllib.parse
from typing import Any, Mapping

from .. import hand
from ..rest import RestApi

#: `VULTR_API_BASE` moves it (the tests' fake).
API_BASE = "https://api.vultr.com/v2"
_LOCAL = ("127.0.0.1", "localhost", "::1")

#: Keys whose values never leave this hand: the root password Vultr shows
#: once at creation, and anything else that unlocks a server or the account.
SECRET_KEYS = frozenset({"default_password", "password", "root_password", "api_key", "token", "private_key"})


def base() -> str:
    value = hand.setting("VULTR_API_BASE", API_BASE) or API_BASE
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "https" and (parts.hostname or "") not in _LOCAL:
        raise hand.HandError("VULTR_API_BASE must be https")
    return value


def api(auth: bool = True) -> RestApi:
    key = hand.require("VULTR_API_KEY") if auth else None
    return RestApi("Vultr", base(), key, "teeny-hands-vps")


def scrub(value: Any) -> tuple[Any, dict[str, str]]:
    """`value` with every secret key taken out, and what was taken (by dotted
    path), for the caller to store rather than print."""
    taken: dict[str, str] = {}

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, Mapping):
            out = {}
            for key, item in node.items():
                where = f"{path}.{key}" if path else str(key)
                if str(key).lower() in SECRET_KEYS and isinstance(item, str) and item:
                    taken[where] = item
                    continue
                out[key] = walk(item, where)
            return out
        if isinstance(node, list):
            return [walk(item, f"{path}[{i}]") for i, item in enumerate(node)]
        return node

    return walk(value, ""), taken
