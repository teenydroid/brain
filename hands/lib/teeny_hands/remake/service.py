"""The `ingest` service over HTTP: yt-dlp and ffmpeg behind Teeny's own small
service (deploy/ingest/serve.py), on the compose network at INGEST_URL.

These calls, and it answers 404 to anything else:

  GET    /ping
  POST   /teeny/v1/probes   {url}                  one video's facts
  POST   /teeny/v1/listings {url, max}             an account page's recent videos
  POST   /teeny/v1/fetches  {url, start, ...}      an excerpt, its shots and frames
  GET    /teeny/v1/fetches/{id}/{source.mp4|audio.m4a|shot-N.jpg}
  DELETE /teeny/v1/fetches/{id}

No key: the service binds the compose network only, and holds none of
Teeny's (its proxy, if any, is its own). Redirects are not followed, and an
error says the status and the service's short message.

`source_of(url)` is the hand's copy of the service's address allowlist
(deploy/ingest/policy.py is the one that holds): https, a YouTube, TikTok or
Instagram host as written, no user, no port.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from .. import hand

DEFAULT_URL = "http://ingest:8090"
_FETCH_ID = re.compile(r"[0-9a-f]{32}")
_FILE = re.compile(r"source\.mp4|audio\.m4a|shot-[1-9]\.jpg")
_MESSAGE_CHARS = 300
_BAD_URL = re.compile(r"[\s\x00-\x1f\x7f\\<>\"'`^{}|]")

HOSTS = {
    "youtube.com": "youtube", "www.youtube.com": "youtube", "m.youtube.com": "youtube", "youtu.be": "youtube",
    "tiktok.com": "tiktok", "www.tiktok.com": "tiktok", "vm.tiktok.com": "tiktok",
    "instagram.com": "instagram", "www.instagram.com": "instagram",
}


class IngestError(hand.HandError):
    """The ingest service did not do it. `status` is its HTTP status, or None
    when it could not be reached."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def source_of(url: Any) -> str | None:
    """"youtube", "tiktok" or "instagram" for an https URL on the allowlist
    (no user, no port, plain ASCII), else None."""
    if not isinstance(url, str) or not url.strip() or len(url) > 500 or not url.isascii() or _BAD_URL.search(url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or "@" in parts.netloc or port is not None or ":" in parts.netloc:
        return None
    return HOSTS.get(host.lower())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401 - urllib's hook
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _fetch_id(value: Any) -> str:
    if not isinstance(value, str) or not _FETCH_ID.fullmatch(value):
        raise IngestError("the ingest service answered a fetch id that is not one")
    return value


class Ingest:
    def __init__(self, base: str | None = None) -> None:
        self._base = (base or hand.setting("INGEST_URL") or DEFAULT_URL).rstrip("/")

    def _open(self, method: str, path: str, body: Any = None, timeout: float = 30.0, limit: int = 1 << 20) -> bytes:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"accept": "application/json", "user-agent": "teeny-hands-remake"}
        if data is not None:
            headers["content-type"] = "application/json"
        request = urllib.request.Request(self._base + path, data=data, headers=headers, method=method)
        try:
            with _OPENER.open(request, timeout=timeout) as response:
                raw = response.read(limit + 1)
        except urllib.error.HTTPError as exc:
            try:
                parsed = json.loads(exc.read() or b"{}")
                message = parsed.get("message") if isinstance(parsed, dict) else None
            except (ValueError, OSError):
                message = None
            text = f"the ingest service answered {exc.code}"
            raise IngestError(f"{text}: {str(message)[:_MESSAGE_CHARS]}" if message else text, exc.code) from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise IngestError(f"could not reach the ingest service ({type(getattr(exc, 'reason', exc)).__name__})") from None
        if len(raw) > limit:
            size = f"{limit // (1024 * 1024)} MB" if limit >= 1 << 20 else f"{limit // 1024} KB"
            raise IngestError(f"the ingest service sent more than {size}")
        return raw

    def _data(self, method: str, path: str, body: Any = None, timeout: float = 30.0) -> dict:
        raw = self._open(method, path, body, timeout=timeout)
        try:
            parsed = json.loads(raw or b"{}")
        except ValueError:
            raise IngestError("the ingest service sent something unreadable") from None
        data = parsed.get("data") if isinstance(parsed, dict) else None
        if not isinstance(data, dict):
            raise IngestError("the ingest service sent something unreadable")
        return data

    def ping(self) -> None:
        """Nothing when the service answers; IngestError when it does not."""
        self._open("GET", "/ping", timeout=10.0)

    def probe(self, url: str) -> dict:
        """One video's facts, or IngestError (422: live, age-restricted, too long, could not read)."""
        return self._data("POST", "/teeny/v1/probes", {"url": url}, timeout=120.0)

    def listing(self, url: str, max: int = 10) -> list[dict]:
        """An account or playlist page's recent videos."""
        entries = self._data("POST", "/teeny/v1/listings", {"url": url, "max": max}, timeout=120.0).get("entries")
        if not isinstance(entries, list):
            raise IngestError("the ingest service sent a listing with no entries")
        return [e for e in entries if isinstance(e, dict)]

    def fetch(self, body: dict, timeout: float = 600.0) -> dict:
        """An excerpt of a video, made before the answer comes: {fetch_id,
        source, excerpt, aspect, fps, shots, files}."""
        data = self._data("POST", "/teeny/v1/fetches", body, timeout=timeout)
        _fetch_id(data.get("fetch_id"))
        return data

    def file(self, fetch_id: str, name: str, limit: int) -> bytes:
        if not _FILE.fullmatch(name or ""):
            raise IngestError("the ingest service named a file a fetch does not have")
        return self._open("GET", f"/teeny/v1/fetches/{_fetch_id(fetch_id)}/{name}", timeout=120.0, limit=limit)

    def delete(self, fetch_id: str) -> None:
        self._open("DELETE", f"/teeny/v1/fetches/{_fetch_id(fetch_id)}")
