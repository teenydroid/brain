"""Where EQVPS is, Teeny's token, and the shapes it answers in."""

from __future__ import annotations

import urllib.parse
from typing import Any, Mapping

from .. import hand
from ..rest import RestApi

#: `EQVPS_API_BASE` moves it (the tests' fake).
API_BASE = "https://api.eqvps.com/api/v1/eqvps"
_LOCAL = ("127.0.0.1", "localhost", "::1")

#: Keys whose values never leave this hand: a one-time root password, a
#: fresh token, anything that unlocks a server or the account.
SECRET_KEYS = frozenset({"password", "root_password", "token", "api_token", "access_token", "private_key"})


def base() -> str:
    value = hand.setting("EQVPS_API_BASE", API_BASE) or API_BASE
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "https" and (parts.hostname or "") not in _LOCAL:
        raise hand.HandError("EQVPS_API_BASE must be https")
    return value


def api(auth: bool = True) -> RestApi:
    key = hand.require("EQVPS_API_TOKEN") if auth else None
    return RestApi("EQVPS", base(), key, "teeny-hands-vps")


def unwrap(answer: Any) -> Any:
    """EQVPS answers some calls as {"data": ...} and some bare."""
    if isinstance(answer, Mapping) and set(answer) >= {"data"} and len(answer) <= 3:
        return answer["data"]
    return answer


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
