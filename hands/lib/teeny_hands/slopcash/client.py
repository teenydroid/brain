"""Where slop.cash, its identity service and GitHub are, and the names that may
reach a path.

The bases are https, except 127.0.0.1 and localhost (the tests' fakes). The
transport is `teeny_hands.rest`, which never follows a redirect, so neither the
session cookie nor the GitHub token can be replayed to wherever a 3xx points.
`GITHUB_TOKEN` is optional here and only ever sent to api.github.com (or a
local fake): it raises GitHub's read rate limit, nothing more.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .. import hand
from ..rest import RestApi, RestError

#: `SLOPCASH_API_BASE`, `SLOPCASH_IDENTITY_BASE` and `GITHUB_API_BASE` move them.
API_BASE = "https://slop.cash"
IDENTITY_BASE = "https://identity.slop.cash"
GITHUB_API_BASE = "https://api.github.com"
DEFAULT_PROJECT = "teeny-s-brain"
DEFAULT_STEWARD = "teenydroid"
SLOPBOT_LOGIN = "slopdotcash[bot]"
SLOPBOT_INSTALL_URL = "https://github.com/apps/slopdotcash/installations/new"
_LOCAL = ("127.0.0.1", "localhost", "::1")
_AGENT = "teeny-hands-slopcash"

_PROJECT = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")


def _base(name: str, default: str) -> str:
    value = (hand.setting(name, default) or default).rstrip("/")
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "https" and (parts.hostname or "") not in _LOCAL:
        raise hand.HandError(f"{name} must be https")
    return value


def api_base() -> str:
    return _base("SLOPCASH_API_BASE", API_BASE)


def identity_base() -> str:
    return _base("SLOPCASH_IDENTITY_BASE", IDENTITY_BASE)


def origin() -> str:
    """The Origin slop.cash's writes must carry: its own (scheme://host[:port])."""
    parts = urllib.parse.urlsplit(api_base())
    return f"{parts.scheme}://{parts.netloc}"


def site() -> RestApi:
    """slop.cash, with no key: the public reads, and the session's transport."""
    return RestApi("slop.cash", api_base(), None, _AGENT)


def identity() -> RestApi:
    return RestApi("slop.cash identity", identity_base(), None, _AGENT)


def github_token() -> str | None:
    """`GITHUB_TOKEN`, only when GitHub's base is GitHub's own (or a local fake)."""
    host = (urllib.parse.urlsplit(_base("GITHUB_API_BASE", GITHUB_API_BASE)).hostname or "").lower()
    if host != "api.github.com" and host not in _LOCAL:
        return None
    return hand.setting("GITHUB_TOKEN")


def github() -> RestApi:
    return RestApi("GitHub", _base("GITHUB_API_BASE", GITHUB_API_BASE), github_token(), _AGENT)


def github_get(path: str, query: list[tuple[str, str]] | None = None, api: RestApi | None = None) -> Any:
    api = api or github()
    return api.call("GET", path, query or [], auth=github_token() is not None,
                    headers={"x-github-api-version": "2022-11-28"})  # fmt: skip


def project_id(value: str | None) -> str:
    """A project id (`SLOPCASH_PROJECT` when none is given), checked, so nothing
    but a project can reach a path."""
    pid = value if value is not None else (hand.setting("SLOPCASH_PROJECT", DEFAULT_PROJECT) or DEFAULT_PROJECT)
    if not isinstance(pid, str) or not _PROJECT.match(pid.strip()):
        raise hand.HandError("a slop.cash project id is lowercase letters, digits and dashes")
    return pid.strip()


def login(value: str | None, what: str = "--steward") -> str:
    name = (value if value is not None else hand.setting("GITHUB_USER", DEFAULT_STEWARD) or DEFAULT_STEWARD).lstrip("@")
    if not _LOGIN.match(name):
        raise hand.HandError(f"{what} must be a GitHub login")
    return name


def repo(value: Any) -> str:
    if not isinstance(value, str) or not _REPO.match(value) or value.split("/")[1] in (".", ".."):
        raise hand.HandError(f"not a repository name: {value!r}")
    return value


def error_code(error: RestError) -> str | None:
    """slop.cash's own error code (`{"error": code, "message": ...}`)."""
    body = error.body
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"]
    return None
