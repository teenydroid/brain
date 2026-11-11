"""A JSON-over-HTTPS client with a bearer key, for the hands whose service is
a plain REST API (hands/moltbook, hands/mail).

What it promises, as hands/gh's client does:

- Nothing that leaves it names a URL, a header or the key. An error says
  which status the service answered and the service's own short message,
  because a URL can carry a query the model wrote and a header carries the
  key. urllib's exceptions are not chained: their repr names the URL.
- It never follows a redirect. urllib would replay the Authorization header
  to wherever a 3xx points (Moltbook's bare domain redirects to www and the
  key would go with it), so a redirect is an error naming its status only.
- A 429 is a `RestError` with `retry_after` (seconds) read from the
  `Retry-After` header or the body; the hand turns it into a refusal with the
  wait, and never retries a write itself.
- A service that authenticates some other way (slop.cash's session cookie)
  calls with `auth=False` and its own `headers`; `exchange` also hands back
  the response headers (a `Set-Cookie`). Neither ever reaches a message.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, Sequence

from . import hand

TIMEOUT_SECONDS = 30.0
_MESSAGE_CHARS = 200


class RestError(hand.HandError):
    """A failed call. `status` is the HTTP status, or None when the service
    was not reached or answered something unreadable; `body` is its parsed
    JSON error body when it sent one (never echoed whole to a caller)."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.body = body


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401 - urllib's hook
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _parse(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None


def _message(parsed: Any) -> str | None:
    if not isinstance(parsed, Mapping):
        return None
    for key in ("error", "message", "detail"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:_MESSAGE_CHARS]
    return None


def _retry_after(headers: Any, parsed: Any) -> float | None:
    raw = headers.get("Retry-After") if headers is not None else None
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    if isinstance(parsed, Mapping):
        for key, scale in (("retry_after_seconds", 1.0), ("retry_after_minutes", 60.0)):
            value = parsed.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return max(0.0, float(value) * scale)
    return None


class RestApi:
    """One service: `name` for messages, `base` (no trailing slash), the key."""

    def __init__(self, name: str, base: str, key: str | None, agent: str) -> None:
        self.name = name
        self._base = base.rstrip("/")
        self._key = key
        self._agent = agent

    def __repr__(self) -> str:
        return f"RestApi({self.name})"

    def call(
        self,
        method: str,
        path: str,
        query: Sequence[tuple[str, str]] = (),
        body: Any = None,
        auth: bool = True,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """One request; its JSON (an empty answer is {}). `auth` False sends
        no key at all (Moltbook's registration); `headers` are sent as well."""
        return self.exchange(method, path, query, body, auth, headers)[0]

    def exchange(
        self,
        method: str,
        path: str,
        query: Sequence[tuple[str, str]] = (),
        body: Any = None,
        auth: bool = True,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[Any, Any]:
        """`call`, and the response's headers (an `email.message.Message`)."""
        url = self._base + path
        if query:
            url += "?" + urllib.parse.urlencode(list(query))
        sent = {"accept": "application/json", "user-agent": self._agent}
        sent.update({name.lower(): value for name, value in (headers or {}).items()})
        if auth:
            if not self._key:
                raise hand.HandError(f"{self.name}: no API key")
            sent["authorization"] = f"Bearer {self._key}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            sent["content-type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=sent, method=method)
        failure: RestError | None = None
        text = b""
        received: Any = None
        try:
            with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
                text = response.read()
                received = response.headers
        except urllib.error.HTTPError as exc:
            try:
                error_body = exc.read()
            except OSError:
                error_body = b""
            parsed = _parse(error_body)
            if 300 <= exc.code < 400:
                failure = RestError(f"{self.name} answered a redirect ({exc.code}); not followed with the key", exc.code)
            else:
                message = _message(parsed)
                failure = RestError(
                    f"{self.name} answered {exc.code}: {message}" if message else f"{self.name} answered {exc.code}",
                    exc.code,
                    _retry_after(exc.headers, parsed) if exc.code == 429 else None,
                    parsed,
                )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            failure = RestError(f"could not reach {self.name} ({type(getattr(exc, 'reason', exc)).__name__})")
        if failure is not None:
            raise failure
        if not text:
            return {}, received
        parsed = _parse(text)
        if parsed is None:
            raise RestError(f"{self.name} sent something unreadable")
        return parsed, received


def quote(part: str) -> str:
    """One path segment, so nothing a caller passes can add a segment."""
    return urllib.parse.quote(str(part), safe="")
