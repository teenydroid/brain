"""Reddit's OAuth API: the token, the transport, and the names that may reach
a path.

Reddit wants three things of a script (https://github.com/reddit-archive/reddit/wiki/OAuth2,
https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki):

- an OAuth token, from `https://www.reddit.com/api/v1/access_token` with
  the app's client id and secret as basic auth. Two grants are taken: a
  refresh token (`REDDIT_REFRESH_TOKEN`, from the code flow, which works with
  two-factor auth), else the script app's password grant
  (`REDDIT_USERNAME` and `REDDIT_PASSWORD`; with 2FA on, that grant fails).
- every API call on `https://oauth.reddit.com` with that token as a bearer;
- a unique, descriptive User-Agent naming the account. Reddit throttles and
  bans generic ones.

A token lives an hour, and a hand is one short process, so it is fetched
once per process and held in memory only: no token is ever written down.

What the transport promises, as `teeny_hands.rest` does: nothing that leaves
it names a URL, a header, the token or a secret; it never follows a
redirect (a redirect would carry the bearer elsewhere); a 429 is a
`RestError` with `retry_after`, read from Reddit's `X-Ratelimit-Reset`, and
is never retried. Writes are form-encoded with `api_type=json`, which is
how Reddit's write endpoints take them, and every read asks for
`raw_json=1` so text comes back unescaped.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, Sequence

from .. import hand
from ..rest import RestError, quote  # noqa: F401 - `quote` is the readers' too

#: `REDDIT_AUTH_BASE` and `REDDIT_API_BASE` move them (the tests' fake).
AUTH_BASE = "https://www.reddit.com"
API_BASE = "https://oauth.reddit.com"
TIMEOUT_SECONDS = 30.0
_LOCAL = ("127.0.0.1", "localhost", "::1")
_MESSAGE_CHARS = 200

_SUBREDDIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]{1,20}$")
_USER = re.compile(r"^[A-Za-z0-9_-]{3,20}$")
_ID = re.compile(r"^[a-z0-9]{1,13}$")
_FULLNAME = re.compile(r"^(t[1-6])_([a-z0-9]{1,13})$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401 - urllib's hook
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _base(name: str, default: str) -> str:
    value = hand.setting(name, default) or default
    parts = urllib.parse.urlsplit(value)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" and host not in _LOCAL:
        raise hand.HandError(f"{name} must be https")
    return value.rstrip("/")


def username() -> str:
    """The account Teeny is on Reddit: `REDDIT_USERNAME`, without `u/`."""
    return user_name(hand.require("REDDIT_USERNAME"), "REDDIT_USERNAME")


def user_agent() -> str:
    """`REDDIT_USER_AGENT`, else Reddit's recommended shape with the account."""
    configured = hand.setting("REDDIT_USER_AGENT")
    if configured:
        return configured
    return f"linux:ai.tinyhumans.teeny:v1 (by /u/{username()})"


def _parse(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None


def _message(parsed: Any) -> str | None:
    if not isinstance(parsed, Mapping):
        return None
    for key in ("message", "error_description", "explanation", "reason", "error"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:_MESSAGE_CHARS]
    return None


def _retry_after(headers: Any) -> float | None:
    if headers is None:
        return None
    for name in ("Retry-After", "X-Ratelimit-Reset"):
        raw = headers.get(name)
        if raw:
            try:
                return max(0.0, float(raw))
            except ValueError:
                continue
    return None


def _send(request: urllib.request.Request, what: str) -> Any:
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            text = response.read()
    except urllib.error.HTTPError as exc:
        try:
            parsed = _parse(exc.read())
        except OSError:
            parsed = None
        if 300 <= exc.code < 400:
            raise RestError(f"{what} answered a redirect ({exc.code}); not followed with the token", exc.code) from None
        message = _message(parsed)
        raise RestError(
            f"{what} answered {exc.code}: {message}" if message else f"{what} answered {exc.code}",
            exc.code,
            _retry_after(exc.headers) if exc.code == 429 else None,
            parsed,
        ) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RestError(f"could not reach {what} ({type(getattr(exc, 'reason', exc)).__name__})") from None
    if not text:
        return {}
    parsed = _parse(text)
    if parsed is None:
        raise RestError(f"{what} sent something unreadable")
    return parsed


class Reddit:
    """One account's API. The secrets stay inside; `repr` names nothing."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._agent = user_agent()

    def __repr__(self) -> str:
        return "Reddit()"

    def _fetch_token(self) -> str:
        client_id = hand.require("REDDIT_CLIENT_ID")
        secret = hand.setting("REDDIT_CLIENT_SECRET") or ""
        refresh = hand.setting("REDDIT_REFRESH_TOKEN")
        if refresh:
            form = {"grant_type": "refresh_token", "refresh_token": refresh}
        else:
            form = {
                "grant_type": "password",
                "username": username(),
                "password": hand.require("REDDIT_PASSWORD"),
            }
        basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        request = urllib.request.Request(
            _base("REDDIT_AUTH_BASE", AUTH_BASE) + "/api/v1/access_token",
            data=urllib.parse.urlencode(form).encode(),
            headers={
                "authorization": f"Basic {basic}",
                "user-agent": self._agent,
                "content-type": "application/x-www-form-urlencoded",
                "accept": "application/json",
            },
            method="POST",
        )
        got = _send(request, "Reddit's token endpoint")
        token = got.get("access_token") if isinstance(got, Mapping) else None
        if not isinstance(token, str) or not token:
            # Reddit answers a bad password with 200 and {"error": "invalid_grant"}.
            why = got.get("error") if isinstance(got, Mapping) and isinstance(got.get("error"), str) else "no token"
            raise hand.HandError(
                f"Reddit gave no token ({why[:60]}): check REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET and the"
                " password or refresh token (a password grant fails when the account has two-factor auth)"
            )
        return token

    def call(
        self,
        method: str,
        path: str,
        query: Sequence[tuple[str, str]] = (),
        form: Mapping[str, str] | None = None,
    ) -> Any:
        """One request; its JSON. A GET asks for `raw_json=1`; a POST sends
        `form` form-encoded with `api_type=json`."""
        if self._token is None:
            self._token = self._fetch_token()
        params = list(query)
        if method == "GET":
            params.append(("raw_json", "1"))
        url = _base("REDDIT_API_BASE", API_BASE) + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"authorization": f"Bearer {self._token}", "user-agent": self._agent, "accept": "application/json"}
        data = None
        if method != "GET":
            data = urllib.parse.urlencode({"api_type": "json", **dict(form or {})}).encode()
            headers["content-type"] = "application/x-www-form-urlencoded"
        return _send(urllib.request.Request(url, data=data, headers=headers, method=method), "Reddit")


_SHARED: Reddit | None = None


def api() -> Reddit:
    """The client secrets/teeny.env describes, one per process."""
    global _SHARED
    if _SHARED is None:
        _SHARED = Reddit()
    return _SHARED


def reset() -> None:
    """Forget the process's client and its token (the tests change settings)."""
    global _SHARED
    _SHARED = None


# --- names -----------------------------------------------------------------------------------


def subreddit_name(value: object, what: str = "subreddit") -> str:
    """A subreddit's name, without `r/`, as Reddit spells it (case kept)."""
    if not isinstance(value, str):
        raise hand.HandError(f'"{what}" must be a subreddit name')
    name = value.strip().removeprefix("/").removeprefix("r/")
    if not _SUBREDDIT.match(name):
        raise hand.HandError(f'"{what}" must be a subreddit name (2-21 letters, digits and _)')
    return name


def user_name(value: object, what: str = "name") -> str:
    if not isinstance(value, str):
        raise hand.HandError(f'"{what}" must be a Reddit username')
    name = value.strip().removeprefix("/").removeprefix("u/")
    if not _USER.match(name):
        raise hand.HandError(f'"{what}" must be a Reddit username (3-20 letters, digits, _ and -)')
    return name


def base_id(value: object, what: str, kind: str) -> str:
    """A post's (`t3`) or a comment's (`t1`) id without its prefix. Either
    form is taken: `abc12` or `t3_abc12`; a fullname of another kind is not."""
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        raise hand.HandError(f'"{what}" must be a Reddit id')
    text = value.strip().lower()
    full = _FULLNAME.match(text)
    if full:
        if full.group(1) != kind:
            raise hand.HandError(f'"{what}" must be a {"post" if kind == "t3" else "comment"} id ({kind}_...)')
        return full.group(2)
    if not _ID.match(text):
        raise hand.HandError(f'"{what}" must be a Reddit id')
    return text
