"""Signing in to slop.cash, in process, for one operator verb.

slop.cash has no API keys, by design. A write needs a session, and a session
needs the operator to sign in with GitHub in a browser:

1. `POST {identity}/v1/oauth/start` {audience: slop-points-web} answers
   {flowId, authorizationUrl, pollCapability, pollAfterSeconds}.
2. The operator is shown `authorizationUrl` (on stderr; stdout is the hand's
   one JSON object) and signs in there as the project's steward.
3. `POST {identity}/v1/oauth/poll` {flowId, pollCapability, audience}, no
   faster than asked: 202 pending, then one 200 with the assertion; 410 is an
   expired or used flow. `SLOPCASH_SIGNIN_TIMEOUT_SECONDS` (300) gives up.
4. `POST {site}/api/v1/points/join` {assertion, public: true}, with slop.cash's
   Origin, sets the `__Host-slop_points` cookie (an hour).
5. Every call of the session sends that cookie, the Origin and JSON.

The identity contract: flowId, pollCapability, the assertion and the session
token stay in this process's memory. They are never in argv, the environment,
a file, stdout, a log or an error; only the authorization URL is shown.
"""

from __future__ import annotations

import re
import sys
import time
import urllib.parse
from typing import Any, Mapping

from .. import hand
from ..rest import RestError
from . import client

AUDIENCE = "slop-points-web"
COOKIE = "__Host-slop_points"
DEFAULT_WAIT = 2.0
MAX_WAIT = 30.0
_ASSERTION = re.compile(r"^slop_assert_v1_[A-Za-z0-9_-]{40,128}$")
_LOCAL = ("127.0.0.1", "localhost", "::1")

#: How the poll waits (a seam for the tests that count the waits).
_sleep = time.sleep


class Session:
    """A signed-in slop.cash session. Its token is in this object only; its
    repr names the account, never the token."""

    def __init__(self, token: str, login: str | None) -> None:
        self.__token = token
        self.login = login

    def __repr__(self) -> str:
        return f"<slop.cash session for {self.login or 'an account'}>"

    __str__ = __repr__

    def call(self, method: str, path: str, body: Any = None) -> Any:
        """One signed-in request: the cookie, slop.cash's Origin, and JSON."""
        headers = {"cookie": f"{COOKIE}={self.__token}", "origin": client.origin()}
        if body is None and method != "GET":
            body = {}
        return client.site().call(method, path, body=body, auth=False, headers=headers)


def _shown(url: Any) -> str:
    """The authorization URL, only if it is a plain https link to show."""
    if isinstance(url, str) and not any(c.isspace() or ord(c) < 32 for c in url):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme == "https" or (parts.scheme == "http" and (parts.hostname or "") in _LOCAL):
            return url
    raise hand.HandError("slop.cash identity sent no usable sign-in link")


def _seconds(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return min(float(value), MAX_WAIT)
    return DEFAULT_WAIT


def _wait(seconds: float, deadline: float, timeout: int) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise hand.HandError(f"sign-in timed out after {timeout} seconds; run the verb again")
    _sleep(min(seconds, remaining))


def _poll(flow: str, capability: str, first_wait: float, timeout: int) -> str:
    """Polls until the assertion comes back, and returns it."""
    deadline = time.monotonic() + timeout
    api = client.identity()
    wait = first_wait
    while True:
        _wait(wait, deadline, timeout)
        try:
            got = api.call("POST", "/v1/oauth/poll", body={"flowId": flow, "pollCapability": capability, "audience": AUDIENCE},
                           auth=False)  # fmt: skip
        except RestError as error:
            if error.status == 410:
                raise hand.HandError("the sign-in expired or was already used (410); run the verb again") from None
            raise
        got = got if isinstance(got, Mapping) else {}
        if got.get("status") == "complete":
            assertion = got.get("assertion")
            if not isinstance(assertion, str) or not _ASSERTION.match(assertion):
                raise hand.HandError("slop.cash identity sent no usable assertion")
            return assertion
        if got.get("status") != "pending":
            raise hand.HandError("slop.cash identity answered the poll with something unexpected")
        wait = _seconds(got.get("retryAfterSeconds"))


def _session_token(headers: Any) -> str | None:
    for raw in (headers.get_all("Set-Cookie") or []) if headers is not None else []:
        name, _, value = raw.split(";", 1)[0].strip().partition("=")
        if name == COOKIE and value:
            return value
    return None


def sign_in() -> Session:
    """The whole flow; a Session, or a HandError that names no secret."""
    timeout = hand.number("SLOPCASH_SIGNIN_TIMEOUT_SECONDS", 300, 1, 3600)
    started = client.identity().call("POST", "/v1/oauth/start", body={"audience": AUDIENCE}, auth=False)
    started = started if isinstance(started, Mapping) else {}
    flow, capability = started.get("flowId"), started.get("pollCapability")
    if not isinstance(flow, str) or not flow or not isinstance(capability, str) or not capability:
        raise hand.HandError("slop.cash identity did not start a sign-in")
    url = _shown(started.get("authorizationUrl"))
    print(
        "hands/slopcash: sign in to slop.cash with GitHub, as the project's steward, at\n\n"
        f"  {url}\n\nwaiting up to {timeout} seconds...",
        file=sys.stderr,
        flush=True,
    )
    assertion = _poll(flow, capability, _seconds(started.get("pollAfterSeconds")), timeout)
    del flow, capability, started
    try:
        joined, headers = client.site().exchange(
            "POST", "/api/v1/points/join", body={"assertion": assertion, "public": True}, auth=False,
            headers={"origin": client.origin()},
        )  # fmt: skip
    except RestError as error:
        code = client.error_code(error) or f"HTTP {error.status}"
        raise hand.HandError(f"slop.cash refused the sign-in: {code}") from None
    finally:
        del assertion
    token = _session_token(headers)
    if token is None:
        raise hand.HandError("slop.cash did not start a session (no session cookie)")
    login = joined.get("actor", {}).get("login") if isinstance(joined, Mapping) and isinstance(joined.get("actor"), Mapping) else None
    print(f"hands/slopcash: signed in as {login or 'an unnamed account'}", file=sys.stderr, flush=True)
    return Session(token, login if isinstance(login, str) else None)
