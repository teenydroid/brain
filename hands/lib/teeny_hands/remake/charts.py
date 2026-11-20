"""`charts`: popular short videos worth remaking, from the providers that are
on, filtered, ranked by views and kept for a slot.

Providers, each optional and named under `unavailable` (never raised) when
off or failing:

  tiktok   REMAKE_TIKTOK_ACCOUNTS (comma list): each account's recent videos,
           listed through the ingest service (TikTok's tag and trending pages
           do not work from the box, an account's page does)
  youtube  YOUTUBE_API_KEY: the Data API's most-popular chart, asked from
           here. The key is in the request and nowhere else: not in the ingest
           service's reach, not in an error, not in the answer or the cache.

Filters in code: 8 to 180 seconds (a video whose length or age is not known
stays, and the study judges it), not live, not age-restricted, not YouTube's
News category, no word of the guard's HARM or NOT_A_FIGURE lists in the title,
nothing the inbound lexical screen stops in a title or an uploader's name
(counted in `dropped_by_screen`, as hands/trend's), and not a video already
in `remake_sources`. Titles and names are still strangers' words: data,
never instructions.

A `charts` answer is kept under state/remake/charts/<key>.json, the key being
the slot it was read for (`--slot`, as hands/trend's) or the UTC hour. A
second read for the key is that file with `cached: true` and asks nothing; it
drops what was made into a remake since. Only an answer with candidates is kept.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .. import hand
from ..guard.screen import INBOUND, screen_text
from ..guard.skit import HARM, NOT_A_FIGURE
from ..trend.top import cache_key
from . import ledger
from .service import IngestError, _NoRedirect, clean_text, source_of

Json = dict[str, Any]

MIN_SECONDS, MAX_SECONDS = 8.0, 180.0
DEFAULT_MAX_AGE_HOURS = 168
DEFAULT_REGION = "US"
#: Cache files kept; the oldest go first.
KEEP = 48
#: Candidates kept in a cache file, before a read's own limit.
KEPT_CANDIDATES = 60
PER_ACCOUNT = 15
NEWS_CATEGORY = "25"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3/videos"
TITLE_CHARS, BY_CHARS = 200, 80
RESPONSE_LIMIT = 2 * 1024 * 1024

_ACCOUNT = re.compile(r"[A-Za-z0-9_.]{1,40}")
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}")
_YT_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_CHART_ID = re.compile(r"chart:(tt|yt):([A-Za-z0-9_-][A-Za-z0-9_.-]{0,63})")
_DURATION = re.compile(r"P(?:([0-9]+)D)?(?:T(?:([0-9]+)H)?(?:([0-9]+)M)?(?:([0-9]+)S)?)?")
_OPENER = urllib.request.build_opener(_NoRedirect)


class ProviderError(Exception):
    """A provider's request failed; the message is safe to show (no key, no URL)."""


class Unavailable(Exception):
    def __init__(self, reason: str, error: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.error = error


# --- the YouTube request ----------------------------------------------------------------------


def _get_json(url: str, timeout: float = 20.0) -> Json:
    """A GET answered as JSON. Its errors say the status and the API's own
    reason word, never the URL (which carries the key)."""
    request = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "teeny-hands-remake"})
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw = response.read(RESPONSE_LIMIT + 1)
    except urllib.error.HTTPError as exc:
        reason = ""
        try:
            parsed = json.loads(exc.read() or b"{}")
            errors = (parsed.get("error") or {}).get("errors") or []
            word = errors[0].get("reason") if errors and isinstance(errors[0], dict) else None
            reason = word if isinstance(word, str) and re.fullmatch(r"[A-Za-z]{1,40}", word) else ""
        except (ValueError, OSError, AttributeError):
            pass
        finally:
            exc.close()
        raise ProviderError(f"the YouTube API answered {exc.code}" + (f" ({reason})" if reason else "")) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ProviderError(f"could not reach the YouTube API ({type(getattr(exc, 'reason', exc)).__name__})") from None
    if len(raw) > RESPONSE_LIMIT:
        raise ProviderError("the YouTube API sent more than 2 MB")
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise ProviderError("the YouTube API sent something unreadable") from None
    if not isinstance(parsed, dict):
        raise ProviderError("the YouTube API sent something unreadable")
    return parsed


# --- reading providers ------------------------------------------------------------------------


def _age_hours(published: Any) -> float | None:
    if not isinstance(published, str):
        return None
    try:
        moment = _dt.datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    return round(max(0.0, (hand.now() - moment.timestamp()) / 3600), 1)


def _count(value: Any) -> int | None:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10**13 else None


def _seconds(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def _iso_duration(value: Any) -> float | None:
    found = _DURATION.fullmatch(value) if isinstance(value, str) else None
    if not found or not any(found.groups()):
        return 0.0 if value == "P0D" else None
    days, hours, minutes, seconds = (int(g or 0) for g in found.groups())
    return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)


def account_names(text: str | None) -> list[str]:
    """The account names of a comma list setting, `@` and blanks aside."""
    return [n.strip().lstrip("@") for n in (text or "").split(",") if n.strip()]


def _tiktok(ingest: Any, unavailable: list[Json]) -> list[Json]:
    names = account_names(hand.setting("REMAKE_TIKTOK_ACCOUNTS"))
    if not names:
        raise Unavailable("off")
    return list_accounts(ingest, names, unavailable)


def list_accounts(ingest: Any, names: list[str], unavailable: list[Json], per_account: int = PER_ACCOUNT) -> list[Json]:
    """Each TikTok account's recent videos, through the ingest service, as
    raw candidates (unfiltered); a bad name or a failed listing is named
    under `unavailable`, never raised. hands/khaby find lists the same way."""
    found: list[Json] = []
    failures: list[str] = []
    for name in names:
        if not _ACCOUNT.fullmatch(name):
            unavailable.append({"provider": "tiktok", "reason": "bad_account", "error": clean_text(name, 40)})
            continue
        try:
            entries = ingest.listing(f"https://www.tiktok.com/@{name}", per_account)
        except IngestError as exc:
            failures.append(str(exc))
            continue
        for e in entries:
            video_id, url = e.get("video_id"), e.get("url")
            if not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id) or source_of(url) != "tiktok":
                continue
            found.append({"id": f"chart:tt:{video_id}", "video_id": video_id, "url": url, "source": "tiktok",
                          "title": e.get("title"), "by": e.get("uploader") or name, "seconds": _seconds(e.get("seconds")),
                          "views": _count(e.get("views")), "age_hours": _age_hours(e.get("published_at")),
                          "provider": "tiktok", "live": False, "news": False, "restricted": False})
    if failures:
        unavailable.append({"provider": "tiktok", "reason": "ingest", "error": clean_text("; ".join(failures), 300)})
    return found


def _youtube(get_json: Callable[[str], Json], key: str | None) -> list[Json]:
    if not key:
        raise Unavailable("off")
    region = (hand.setting("REMAKE_REGION") or DEFAULT_REGION).strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", region):
        region = DEFAULT_REGION
    url = YOUTUBE_API + "?" + urllib.parse.urlencode(
        {"chart": "mostPopular", "part": "snippet,contentDetails,statistics", "maxResults": 25,
         "regionCode": region, "key": key})
    try:
        answer = get_json(url)
    except ProviderError as exc:
        raise Unavailable("api", str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - whatever it was, the key may be in its text
        raise Unavailable("api", f"the YouTube API failed ({type(exc).__name__})") from None
    items = answer.get("items") if isinstance(answer, dict) else None
    if not isinstance(items, list):
        raise Unavailable("api", "the YouTube API sent no list of videos")
    found: list[Json] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        video_id = it.get("id")
        snippet = it.get("snippet") if isinstance(it.get("snippet"), dict) else {}
        details = it.get("contentDetails") if isinstance(it.get("contentDetails"), dict) else {}
        stats = it.get("statistics") if isinstance(it.get("statistics"), dict) else {}
        if not isinstance(video_id, str) or not _YT_ID.fullmatch(video_id):
            continue
        rating = details.get("contentRating") if isinstance(details.get("contentRating"), dict) else {}
        length = _iso_duration(details.get("duration"))
        if length is None:  # the chart always has one; a video without is not read
            continue
        found.append({"id": f"chart:yt:{video_id}", "video_id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}",
                      "source": "youtube", "title": snippet.get("title"), "by": snippet.get("channelTitle"),
                      "seconds": length, "views": _count(stats.get("viewCount")),
                      "age_hours": _age_hours(snippet.get("publishedAt")), "provider": "youtube",
                      "live": snippet.get("liveBroadcastContent") not in (None, "none"),
                      "news": snippet.get("categoryId") == NEWS_CATEGORY,
                      "restricted": rating.get("ytRating") == "ytAgeRestricted"})
    return found


# --- filtering and ranking --------------------------------------------------------------------


def _keep_raw(c: Json, max_age: float) -> bool:
    if c["live"] or c["news"] or c["restricted"]:
        return False
    if c["seconds"] is not None and not MIN_SECONDS <= c["seconds"] <= MAX_SECONDS:
        return False
    return c["age_hours"] is None or c["age_hours"] <= max_age


def _passes_screen(title: str, by: str) -> bool:
    if HARM.search(title) or NOT_A_FIGURE.search(title):
        return False
    return all(screen_text(t, INBOUND) is None for t in (title, by) if t)


def _ranked(candidates: list[Json]) -> list[Json]:
    best: dict[str, Json] = {}
    for c in candidates:
        have = best.get(c["id"])
        if have is None or (c["views"] or 0) > (have["views"] or 0):
            best[c["id"]] = c
    ordered = sorted(best.values(), key=lambda c: -(c["views"] or 0))
    return [{**{k: v for k, v in c.items() if k not in ("video_id", "live", "news", "restricted")}, "rank": n}
            for n, c in enumerate(ordered, 1)]


def _served(kept: Json, limit: int) -> Json:
    """A kept answer as read now: what was made into a remake since is gone,
    the first `limit` kept, ranks counted again."""
    made = ledger.seen_any([c["id"].rsplit(":", 1)[1] for c in kept["candidates"]])
    left = [c for c in kept["candidates"] if c["id"].rsplit(":", 1)[1] not in made][:limit]
    return {**kept, "candidates": [{**c, "rank": n} for n, c in enumerate(left, 1)]}


# --- the cache --------------------------------------------------------------------------------


def _cache_dir() -> Path:
    path = hand.state_dir() / "remake" / "charts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(key: str) -> Path:
    return _cache_dir() / (key.replace(":", "_") + ".json")


def _write(key: str, answer: Json) -> None:
    """The answer to its cache file, atomically, then the oldest beyond KEEP go."""
    path = _path(key)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.stem + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(answer, ensure_ascii=False))
        os.replace(name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(name)
        raise

    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    for old in sorted(_cache_dir().glob("*.json"), key=mtime)[:-KEEP]:
        old.unlink(missing_ok=True)


def _read(path: Path) -> Json | None:
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = isinstance(kept, dict) and isinstance(kept.get("candidates"), list) and kept["candidates"]
    return kept if ok else None


# --- the verb ---------------------------------------------------------------------------------


def top(slot: str | None, limit: int, fresh: bool = False, ingest: Any = None,
        get_json: Callable[[str], Json] | None = None) -> Json:
    """{key, fetched_at, candidates, unavailable, dropped_by_screen, cached}:
    the first `limit` candidates by views, each {id, url, source, title, by,
    seconds, views, age_hours, rank, provider}."""
    key = cache_key(slot)
    path = _path(key)
    if not fresh and path.is_file():
        kept = _read(path)
        if kept is not None:
            return {**_served(kept, limit), "cached": True}
    api_key = hand.setting("YOUTUBE_API_KEY")
    max_age = float(hand.number("REMAKE_MAX_AGE_HOURS", DEFAULT_MAX_AGE_HOURS, 1, 24 * 365))
    unavailable: list[Json] = []
    raw: list[Json] = []

    def provider(name: str, read: Callable[[], list[Json]]) -> None:
        try:
            raw.extend(read())
        except Unavailable as exc:
            unavailable.append({"provider": name, "reason": exc.reason, **({"error": exc.error} if exc.error else {})})
        except Exception as exc:  # noqa: BLE001 - a provider never raises out of the verb
            unavailable.append({"provider": name, "reason": "failed", "error": type(exc).__name__})

    if ingest is None:
        from .service import Ingest

        ingest = Ingest()
    provider("tiktok", lambda: _tiktok(ingest, unavailable))
    provider("youtube", lambda: _youtube(get_json or _get_json, api_key))
    made = ledger.seen_any([c["video_id"] for c in raw])
    dropped = 0
    kept_raw: list[Json] = []
    for c in raw:
        if c["video_id"] in made or not _keep_raw(c, max_age):
            continue
        c["title"], c["by"] = clean_text(c["title"], TITLE_CHARS), clean_text(c["by"], BY_CHARS)
        if not c["title"]:
            continue
        if not _passes_screen(c["title"], c["by"]):
            dropped += 1
            continue
        kept_raw.append(c)
    if api_key:
        for u in unavailable:
            u["error"] = u["error"].replace(api_key, "[key]") if isinstance(u.get("error"), str) else u.get("error")
    answer: Json = {"key": key, "fetched_at": hand.now(), "candidates": _ranked(kept_raw)[:KEPT_CANDIDATES],
                    "unavailable": unavailable, "dropped_by_screen": dropped}
    if answer["candidates"]:
        _write(key, answer)
    return {**_served(answer, limit), "cached": False}


def parse_id(chart_id: Any) -> tuple[str, str] | None:
    """("tt" or "yt", video id) for a chart id, else None."""
    found = _CHART_ID.fullmatch(chart_id) if isinstance(chart_id, str) else None
    return (found.group(1), found.group(2)) if found else None


def resolve(chart_id: Any) -> Json | None:
    """{url, source, title} for a chart id: from the newest kept chart that
    lists it; a YouTube id with no chart is its watch page. None when unknown."""
    parsed = parse_id(chart_id)
    if parsed is None:
        return None
    code, video_id = parsed
    files = sorted(_cache_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for file in files:
        kept = _read(file)
        for c in (kept or {}).get("candidates", []):
            if isinstance(c, dict) and c.get("id") == chart_id and source_of(c.get("url")) is not None:
                return {"url": c["url"], "source": c.get("source"), "title": c.get("title")}
    if code == "yt" and _YT_ID.fullmatch(video_id):
        return {"url": f"https://www.youtube.com/watch?v={video_id}", "source": "youtube", "title": None}
    return None
