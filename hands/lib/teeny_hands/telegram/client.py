"""Telegram's Bot API, through the hands' REST client.

The token is part of every URL Telegram takes (`/bot<token>/<method>`), so
it lives only here: `RestApi` never names a URL in an error, and nothing
else in the hands builds one. Every method is a POST with a JSON body.
"""

from __future__ import annotations

from typing import Any, Mapping

from .. import hand
from ..rest import RestApi, RestError

DEFAULT_BASE = "https://api.telegram.org"


class Bot:
    def __init__(self, token: str, base: str | None = None) -> None:
        self._token = token
        self._api = RestApi("Telegram", base or DEFAULT_BASE, None, "teeny-hands/telegram")

    def __repr__(self) -> str:
        return "Bot(telegram)"

    def call(self, method: str, **params: Any) -> Any:
        """One method; Telegram's `result`. A refusal from Telegram
        (`ok: false`) is a RestError with its description, never the URL."""
        try:
            got = self._api.call("POST", f"/bot{self._token}/{method}", body=params, auth=False)
        except RestError as error:
            body = error.body if isinstance(error.body, Mapping) else {}
            raise RestError(f"Telegram refused {method}: {str(body.get('description') or error)[:200]}",
                            error.status, _retry(body), body) from None
        if not isinstance(got, Mapping) or not got.get("ok"):
            raise RestError(f"Telegram refused {method}")
        return got.get("result")


def _retry(body: Mapping[str, Any]) -> float | None:
    params = body.get("parameters")
    if isinstance(params, Mapping) and isinstance(params.get("retry_after"), (int, float)):
        return float(params["retry_after"])
    return None


def bot() -> Bot:
    return Bot(hand.require("TELEGRAM_BOT_TOKEN"), hand.setting("TELEGRAM_API_BASE"))


def owners() -> set[int]:
    """The people Teeny's makers speak to it as (TELEGRAM_OWNERS: user ids,
    comma separated). Their DMs always reach the mind, even at night."""
    out = set()
    for part in (hand.setting("TELEGRAM_OWNERS") or "").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


def open_dms() -> bool:
    """Whether a stranger's DM reaches the mind (TELEGRAM_OPEN_DMS, off)."""
    return hand.flag("TELEGRAM_OPEN_DMS", False)


def allowed_chats() -> set[int] | None:
    """The groups Teeny takes part in (TELEGRAM_CHATS: chat ids). Unset: every
    group it was added to; someone had to add it."""
    raw = (hand.setting("TELEGRAM_CHATS") or "").strip()
    if not raw or raw == "*":
        return None
    return {int(p) for p in (x.strip() for x in raw.split(",")) if p.lstrip("-").isdigit()}


def name_of(user: Mapping[str, Any] | None) -> str:
    if not isinstance(user, Mapping):
        return "someone"
    if user.get("username"):
        return "@" + str(user["username"])
    return " ".join(str(user.get(k)) for k in ("first_name", "last_name") if user.get(k)) or f"user {user.get('id')}"
