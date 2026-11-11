"""Surplus Intelligence (api.surplusintelligence.ai), the marketplace the
image and video hands buy from with SURPLUS_API_KEY.

Like hands/lib/teeny_hands/rest.py, nothing that leaves here names a URL, a
header or the key: an error says the status and Surplus's own short message.
Two differences: generations take longer than rest.py's 30 s, and a finished
video's artifact URL answers a redirect to short-lived storage, which is
followed here once, without the key (the key never goes to another host).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .. import hand

BASE = "https://api.surplusintelligence.ai"
AGENT = "teeny-hands/1 (+https://github.com/teenydroid/brain)"
_MESSAGE_CHARS = 200


class SurplusError(hand.HandError):
    def __init__(self, message: str, status: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401 - urllib's hook
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _message(raw: bytes) -> tuple[str | None, Any]:
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None, None
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error, dict):
        error = error.get("message") or error.get("code") or error.get("type")
    if isinstance(error, str):
        return error.split(" Unusual bug?")[0][:_MESSAGE_CHARS], parsed
    return None, parsed


class Surplus:
    def __init__(self, key: str | None = None, base: str | None = None) -> None:
        self._key = key if key is not None else hand.setting("SURPLUS_API_KEY")
        self._base = (base or hand.setting("SURPLUS_BASE_URL") or BASE).rstrip("/")

    def __repr__(self) -> str:
        return "Surplus()"

    def _request(self, method: str, path: str, body: Any = None, timeout: float = 60.0, headers: dict | None = None):
        if not self._key:
            raise hand.HandError("SURPLUS_API_KEY is not set in secrets/teeny.env")
        all_headers = {"accept": "application/json", "user-agent": AGENT, "authorization": f"Bearer {self._key}"}
        all_headers.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            all_headers["content-type"] = "application/json"
        return urllib.request.Request(self._base + path, data=data, headers=all_headers, method=method), timeout

    def call(self, method: str, path: str, body: Any = None, timeout: float = 60.0, headers: dict | None = None) -> Any:
        request, timeout = self._request(method, path, body, timeout, headers)
        try:
            with _OPENER.open(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read()
            except OSError:
                raw = b""
            message, parsed = _message(raw)
            raise SurplusError(
                f"Surplus answered {exc.code}: {message}" if message else f"Surplus answered {exc.code}", exc.code, parsed
            ) from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SurplusError(f"could not reach Surplus ({type(getattr(exc, 'reason', exc)).__name__})") from None
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            raise SurplusError("Surplus sent something unreadable") from None

    def download(self, path_or_url: str, limit: int, timeout: float = 120.0) -> bytes:
        """An artifact: on Surplus's host with the key; a redirect it answers
        is followed once, to wherever it points, without the key."""
        parsed = urllib.parse.urlparse(path_or_url)
        own = urllib.parse.urlparse(self._base)
        if parsed.scheme and parsed.netloc and parsed.netloc != own.netloc:
            return _fetch(urllib.request.Request(path_or_url, headers={"user-agent": AGENT}), limit, timeout)
        path = parsed.path if parsed.scheme else path_or_url
        request, timeout = self._request("GET", path, None, timeout, {"accept": "*/*"})
        try:
            return _fetch(request, limit, timeout)
        except _Redirect as moved:
            if not moved.location.startswith("https://"):
                raise SurplusError("Surplus redirected the download somewhere that is not https") from None
            return _fetch(urllib.request.Request(moved.location, headers={"user-agent": AGENT}), limit, timeout)


class _Redirect(Exception):
    def __init__(self, location: str) -> None:
        super().__init__("redirect")
        self.location = location


def _fetch(request: urllib.request.Request, limit: int, timeout: float) -> bytes:
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            data = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400 and exc.headers.get("location"):
            raise _Redirect(exc.headers["location"]) from None
        raise SurplusError(f"the download answered {exc.code}", exc.code) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SurplusError(f"could not download ({type(getattr(exc, 'reason', exc)).__name__})") from None
    if len(data) > limit:
        raise SurplusError(f"the download is over {limit // (1024 * 1024)} MB")
    return data
