"""What Teeny's ingest service (deploy/ingest/serve.py) allows, as plain code
with nothing outside the standard library, so hands/tests/ingest tests it as
it runs.

- `check_url(url)`: an https URL on the allowlist, or a `RequestError`
  (answered 400). Nothing else ever reaches yt-dlp.
- `probe_argv`, `listing_argv`, `download_argv`: yt-dlp's command lines. The
  URL is the last word after `--`, there is no shell, and the options that
  keep yt-dlp from running anything or reading a config are always there.
- `refusal(info)`: why a probed video is not taken (live, age limit, length).
- `probe_fields(info, source, url)`, `listing_entry(entry)`: yt-dlp's JSON as
  the service's own answers.
- `check_probe`, `check_listing`, `check_fetch`: the bodies, bounded.
- `route(method, path)`: the only routes there are, and the only file names
  a fetch serves (matched as they arrive, before any normalising).
- `stale(entries, now, max_age)`: which leftovers the sweep removes.
"""

from __future__ import annotations

import datetime as _dt
import re
import secrets
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

#: host -> source. https only, these hosts only, no userinfo, no port.
HOSTS = {
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "m.youtube.com": "youtube",
    "youtu.be": "youtube",
    "tiktok.com": "tiktok",
    "www.tiktok.com": "tiktok",
    "vm.tiktok.com": "tiktok",
    "instagram.com": "instagram",
    "www.instagram.com": "instagram",
}

MAX_URL = 500
MAX_BODY_BYTES = 8 * 1024
#: A video longer than this (seconds) is refused at probe time.
MAX_VIDEO_SECONDS = 600
#: yt-dlp's --max-filesize for one download.
MAX_DOWNLOAD = "150M"
SOCKET_TIMEOUT = "20"
MAX_TEXT = 300

FETCH_ID = re.compile(r"[0-9a-f]{32}")
#: The only files a fetch serves.
FILE = re.compile(r"source\.mp4|audio\.m4a|shot-[1-9]\.jpg")
VIDEO_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_PROXY = re.compile(r"(https?|socks5h?)://[^\s/@]+(:[^\s/@]*)?@?[^\s/]*(:[0-9]{1,5})?")
_PROXY_URL = re.compile(r"(?:https?|socks5h?)://[A-Za-z0-9._~:%@!$&'()*+,;=\[\]-]+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁠-⁤﻿]")
_BAD_URL_CHARS = re.compile(r"[\s\x00-\x1f\x7f\\<>\"'`^{}|]")

#: Bounds of a fetch's body: name -> (default, low, high, whole number).
FETCH_BOUNDS = {
    "start": (0.0, 0.0, 600.0, False),
    "max_seconds": (30.0, 1.0, 60.0, False),
    "max_height": (720, 240, 1080, True),
    "max_shots": (6, 1, 9, True),
    "min_shot": (1.0, 0.25, 10.0, False),
    "max_shot": (10.0, 1.0, 30.0, False),
}
LISTING_MAX = (10, 1, 50)


class RequestError(ValueError):
    """A request the service will not take: answered 400."""


def _number(body: dict, name: str, default: Any, low: float, high: float, whole: bool) -> Any:
    value = body.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int,) if whole else (int, float)):
        raise RequestError(f"{name} is a {'whole ' if whole else ''}number")
    if not low <= value <= high:
        raise RequestError(f"{name} is {low:g} to {high:g}")
    return value if whole else float(value)


# --- urls -----------------------------------------------------------------------------------


def check_url(url: Any) -> dict[str, str]:
    """{url, source, host}: the URL as it is passed on (no fragment), or a
    RequestError. https, a host on the list as written (ASCII, no trailing
    dot), no userinfo, no port, no IP literal, no whitespace or controls."""
    if not isinstance(url, str) or not url.strip() or len(url) > MAX_URL:
        raise RequestError(f"url is an https address of at most {MAX_URL} characters")
    if not url.isascii() or _BAD_URL_CHARS.search(url):
        raise RequestError("url has a character that is not allowed")
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        raise RequestError("url is not an address") from None
    if parts.scheme.lower() != "https":
        raise RequestError("url is https")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise RequestError("url has no user or password")
    if port is not None or ":" in parts.netloc:
        raise RequestError("url has no port")
    source = HOSTS.get(host.lower())
    if source is None:
        raise RequestError("url is a YouTube, TikTok or Instagram address")
    clean = urlunsplit(("https", host.lower(), parts.path, parts.query, ""))
    return {"url": clean, "source": source, "host": host.lower()}


# --- yt-dlp ---------------------------------------------------------------------------------


def _proxy_args(proxy: Any) -> list[str]:
    if isinstance(proxy, str) and _PROXY_URL.fullmatch(proxy.strip()):
        return ["--proxy", proxy.strip()]
    return []


def _common(proxy: Any) -> list[str]:
    return ["yt-dlp", "--ignore-config", "--no-exec", "--no-cache-dir", "--no-warnings", "--no-color",
            "--socket-timeout", SOCKET_TIMEOUT, "--retries", "2", *_proxy_args(proxy)]


def probe_argv(url: str, proxy: Any = None) -> list[str]:
    """yt-dlp for one video's metadata as one JSON object on stdout."""
    url = check_url(url)["url"]
    return [*_common(proxy), "--no-playlist", "--skip-download", "--dump-single-json", "--", url]


def listing_argv(url: str, limit: int, proxy: Any = None) -> list[str]:
    """yt-dlp for an account or playlist page, flat: one JSON object whose
    `entries` are its first `limit` videos."""
    url = check_url(url)["url"]
    if isinstance(limit, bool) or not isinstance(limit, int) or not LISTING_MAX[1] <= limit <= LISTING_MAX[2]:
        raise RequestError("a listing is 1 to 50 entries")
    return [*_common(proxy), "--flat-playlist", "--skip-download", "--dump-single-json", "--playlist-end",
            str(limit), "--", url]


def download_argv(url: str, template: str, max_height: int, proxy: Any = None) -> list[str]:
    """yt-dlp for one video into `template` (its -o), best quality at most
    `max_height` high, merged into an mp4, at most MAX_DOWNLOAD."""
    url = check_url(url)["url"]
    if isinstance(max_height, bool) or not isinstance(max_height, int) or not 144 <= max_height <= 2160:
        raise RequestError("max_height is a whole number of pixels")
    fmt = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b"
    return [*_common(proxy), "--no-playlist", "--max-filesize", MAX_DOWNLOAD, "-f", fmt, "-S", f"res:{max_height}",
            "--merge-output-format", "mp4", "--no-part", "--no-progress", "-o", template, "--", url]


def scrub(text: Any, *secrets_to_hide: Any) -> str:
    """Text for an error message: one line, no controls, the proxy (and any
    other address with a user or a password in it) taken out, at most MAX_TEXT
    characters."""
    out = str(text)
    for secret in secrets_to_hide:
        if isinstance(secret, str) and secret:
            out = out.replace(secret, "[proxy]")
    out = _PROXY.sub("[proxy]", out)
    out = _CONTROL.sub(" ", out)
    return " ".join(out.split())[:MAX_TEXT]


# --- what a video must be -------------------------------------------------------------------


def refusal(info: Any) -> str | None:
    """Why yt-dlp's `info` is not a video to take, or None."""
    if not isinstance(info, dict):
        return "no video information"
    if info.get("is_live") is True or info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        return "a live stream"
    age = info.get("age_limit")
    if age not in (None, 0):
        return "age-restricted"
    seconds = info.get("duration")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
        return "no known length"
    if seconds > MAX_VIDEO_SECONDS:
        return f"longer than {MAX_VIDEO_SECONDS} seconds"
    return None


def _text(value: Any, limit: int = MAX_TEXT) -> str | None:
    if not isinstance(value, str):
        return None
    out = " ".join(_CONTROL.sub("", value).split())[:limit]
    return out or None


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10**13 else None


def _published(info: dict) -> str | None:
    stamp = info.get("timestamp")
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and 0 < stamp < 4_102_444_800:
        return _dt.datetime.fromtimestamp(stamp, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    day = info.get("upload_date")
    if isinstance(day, str) and re.fullmatch(r"20[0-9]{2}[01][0-9][0-3][0-9]", day):
        return f"{day[:4]}-{day[4:6]}-{day[6:]}T00:00:00Z"
    return None


def _video_id(value: Any) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not VIDEO_ID.fullmatch(str(value)) or str(value) in (".", ".."):
        raise RequestError("the video has no usable id")
    return str(value)


def _own_url(info: dict, fallback: str) -> str:
    """The page URL yt-dlp reports, when it is on the list; else the one asked for."""
    for key in ("webpage_url", "original_url"):
        try:
            if isinstance(info.get(key), str):
                return check_url(info[key])["url"]
        except RequestError:
            continue
    return fallback


def probe_fields(info: dict, source: str, url: str) -> dict[str, Any]:
    """The probe answer from yt-dlp's `info`."""
    categories = info.get("categories")
    category = _text(categories[0], 60) if isinstance(categories, list) and categories else _text(info.get("category"), 60)
    seconds = info.get("duration")
    dims = {k: (info.get(k) if isinstance(info.get(k), int) and not isinstance(info.get(k), bool) and 0 < info.get(k) < 20000 else None)
            for k in ("width", "height")}
    return {
        "source": source,
        "video_id": _video_id(info.get("id")),
        "url": _own_url(info, url),
        "title": _text(info.get("title")),
        "uploader": _text(info.get("uploader") or info.get("channel") or info.get("creator"), 100),
        "seconds": round(float(seconds), 3) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else None,
        "views": _count(info.get("view_count")),
        "age_limit": info.get("age_limit") if isinstance(info.get("age_limit"), int) and not isinstance(info.get("age_limit"), bool) else 0,
        "live": bool(info.get("is_live")) or info.get("live_status") in ("is_live", "is_upcoming", "post_live"),
        "category": category,
        "published_at": _published(info),
        "width": dims["width"],
        "height": dims["height"],
    }


def listing_entry(entry: Any) -> dict[str, Any] | None:
    """One flat-playlist entry as a listing entry, or None when it is not a
    video on the allowlist with an id."""
    if not isinstance(entry, dict):
        return None
    url = None
    for key in ("url", "webpage_url"):
        try:
            if isinstance(entry.get(key), str):
                url = check_url(entry[key])["url"]
                break
        except RequestError:
            continue
    try:
        video_id = _video_id(entry.get("id"))
    except RequestError:
        return None
    if url is None:
        return None
    seconds = entry.get("duration")
    return {
        "url": url,
        "video_id": video_id,
        "title": _text(entry.get("title")),
        "uploader": _text(entry.get("uploader") or entry.get("channel") or entry.get("creator"), 100),
        "seconds": round(float(seconds), 3) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else None,
        "views": _count(entry.get("view_count")),
        "published_at": _published(entry),
    }


# --- bodies ---------------------------------------------------------------------------------


def _object(body: Any, allowed: set[str]) -> dict:
    if not isinstance(body, dict):
        raise RequestError("the body is a JSON object")
    extra = sorted(set(body) - allowed)
    if extra:
        raise RequestError(f"not taken here: {', '.join(extra)}")
    return body


def check_probe(body: Any) -> dict[str, str]:
    return check_url(_object(body, {"url"}).get("url"))


def check_listing(body: Any) -> dict[str, Any]:
    body = _object(body, {"url", "max"})
    checked = check_url(body.get("url"))
    default, low, high = LISTING_MAX
    value = body.get("max", default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise RequestError(f"max is a whole number, {low} to {high}")
    return {**checked, "max": value}


def check_fetch(body: Any) -> dict[str, Any]:
    """{url, source, host, start, max_seconds, max_height, max_shots,
    min_shot, max_shot}, bounded, defaults filled."""
    body = _object(body, {"url", *FETCH_BOUNDS})
    out: dict[str, Any] = dict(check_url(body.get("url")))
    for name, (default, low, high, whole) in FETCH_BOUNDS.items():
        out[name] = _number(body, name, default, low, high, whole)
    if out["max_shot"] < 2 * out["min_shot"]:
        raise RequestError("max_shot is at least twice min_shot")
    return out


# --- routes ---------------------------------------------------------------------------------

_ROUTE_FETCH = re.compile(r"/teeny/v1/fetches/([0-9a-f]{32})(?:/([^/]+))?")


def route(method: str, path: str) -> tuple[str, dict[str, str]] | None:
    """(name, arguments) of the route a request is, or None. `path` is
    matched as it arrives (a query, an encoded slash or a trailing slash
    matches nothing)."""
    if method == "GET" and path == "/ping":
        return "ping", {}
    if method == "POST":
        names = {"/teeny/v1/probes": "probe", "/teeny/v1/listings": "listing", "/teeny/v1/fetches": "fetch"}
        return (names[path], {}) if path in names else None
    found = _ROUTE_FETCH.fullmatch(path)
    if found is None:
        return None
    fetch_id, name = found.groups()
    if method == "DELETE" and name is None:
        return "delete", {"fetch_id": fetch_id}
    if method == "GET" and name is not None and FILE.fullmatch(name):
        return "file", {"fetch_id": fetch_id, "name": name}
    return None


def new_fetch_id() -> str:
    return secrets.token_hex(16)


def stale(entries: Iterable[tuple[str, float]], now: float, max_age: float) -> list[str]:
    """Of (name, mtime) pairs, the names older than `max_age` seconds."""
    return [name for name, mtime in entries if now - mtime > max_age]
