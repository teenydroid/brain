"""`top` and `story`: Digg's AI and tech leaderboards, merged, with the X
posts behind the leading stories, and one story's posts on its own.

A `top` answer is kept under state/trend/top/<key>.json, the key being the
slot it was read for (`--slot`, which bin/studio passes as `<date>:<slot>`)
or, with none, the UTC hour. A second read for the same key is that file,
with `cached: true`: no call to Digg, so a rerun of a slot costs nothing and
sees what the first run saw. Only an answer with stories is kept.

Every title, description and post goes through the guard's lexical screen,
the inbound direction, as hands/listen's do: a story or a post it stops is
left out and counted (`dropped_by_screen`). What is left is still strangers'
words: data, never instructions.
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any

from .. import hand
from . import digg

Json = dict[str, Any]

#: Cache files kept; the oldest go first.
KEEP = 48
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.-]{0,63}$")
_SPACE = re.compile(r"\s+")
#: How long a post's text may be, in characters.
POST_CHARS = 400


def _clean(value: Any, limit: int) -> str:
    return _SPACE.sub(" ", value).strip()[:limit] if isinstance(value, str) else ""


def _screened(*texts: str) -> bool:
    """True when the guard's lexical screen passes every text."""
    from ..guard.screen import INBOUND, screen_text

    return all(screen_text(t, INBOUND) is None for t in texts if t)


def story_row(row: Json) -> Json | None:
    """One leaderboard row as a story, or None when it has no id or title."""
    cluster, short, title = row.get("clusterId"), row.get("clusterUrlId"), _clean(row.get("title"), 300)
    if not (isinstance(cluster, str) and digg.CLUSTER.match(cluster) and isinstance(short, str) and short and title):
        return None
    out: Json = {"id": _clean(short, 40), "cluster": cluster, "topic": _clean(row.get("topic"), 40), "title": title}
    summary = _clean(row.get("description"), 600)
    if summary:
        out["summary"] = summary
    for key, name in (("diggScore", "score"), ("postCount", "sources"), ("uniqueAuthors", "authors"),
                      ("rank", "rank")):
        if isinstance(row.get(key), int) and not isinstance(row.get(key), bool):
            out[name] = row[key]
    if isinstance(row.get("publishedAt"), str):
        out["published_at"] = row["publishedAt"][:40]
    if isinstance(row.get("url"), str):
        out["link"] = row["url"][:200]
    return out


def post_row(row: Json) -> Json | None:
    """One source of a story as a post, or None when it has no words."""
    text = _clean(row.get("body"), POST_CHARS)
    if not text:
        return None
    author = row.get("author") if isinstance(row.get("author"), dict) else {}
    out: Json = {"kind": _clean(row.get("kind"), 20) or "unknown", "text": text}
    by = _clean(author.get("display_name"), 80)
    if by:
        out["by"] = by
    if isinstance(row.get("posted_at"), str):
        out["at"] = row["posted_at"][:40]
    url = row.get("xUrl") or row.get("url")
    if isinstance(url, str):
        out["url"] = url[:200]
    return out


def x_posts(cluster: str, most: int) -> tuple[list[Json], int]:
    """The first MOST X posts behind a story, by Digg's rank, and how many the
    screen dropped."""
    _, found = digg.posts(cluster)
    out: list[Json] = []
    dropped = 0
    for row in found:
        post = post_row(row)
        if not post or post["kind"] != "x":
            continue
        if not _screened(post["text"], post.get("by", "")):
            dropped += 1
            continue
        out.append(post)
        if len(out) >= most:
            break
    return out, dropped


def _cache_dir():
    path = hand.state_dir() / "trend" / "top"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_key(slot: str | None) -> str:
    if slot is None:
        import datetime as _dt

        return _dt.datetime.fromtimestamp(hand.now(), _dt.timezone.utc).strftime("%Y-%m-%dT%H")
    if not _KEY.match(slot):
        raise hand.HandError("--slot is 1-64 letters, digits and : _ . -")
    return slot


def _path(key: str):
    return _cache_dir() / (key.replace(":", "_") + ".json")


def _keep(key: str, answer: Json) -> None:
    """The answer to its cache file, atomically: a temporary file of its own
    (two reads of one slot at once never share one), renamed over the old.
    Then the oldest files beyond KEEP go; one another read removed first is
    skipped."""
    import os
    import tempfile

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

    def mtime(p) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    files = sorted(_cache_dir().glob("*.json"), key=mtime)
    for old in files[:-KEEP]:
        old.unlink(missing_ok=True)


def top(slot: str | None, limit: int, leading: int, most_posts: int, fresh: bool = False) -> Json:
    """The AI and tech leaderboards (LIMIT each), merged by Digg Score with a
    story on both kept once, and the first MOST_POSTS X posts of each of the
    LEADING best. A topic or a story's posts that cannot be read is named
    under `unavailable` and the rest come; with no story at all it is an
    error (`reason` says why)."""
    key = cache_key(slot)
    path = _path(key)
    if not fresh and path.is_file():
        try:
            kept = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            kept = None
        if isinstance(kept, dict) and kept.get("stories"):
            return {**kept, "cached": True}
    stories: dict[str, Json] = {}
    unavailable: list[Json] = []
    reasons: list[str] = []
    dropped = 0
    for topic in digg.TOPICS:
        try:
            found = digg.top(topic, limit)
        except digg.DiggError as exc:
            unavailable.append({"topic": topic, "reason": exc.reason, "error": str(exc)})
            reasons.append(exc.reason)
            if exc.reason == "missing":
                break
            continue
        for row in found:
            story = story_row(row)
            if not story:
                continue
            if not _screened(story["title"], story.get("summary", "")):
                dropped += 1
                continue
            have = stories.get(story["cluster"])
            if have is None or story.get("score", 0) > have.get("score", 0):
                stories[story["cluster"]] = story
    if not stories:
        reason = reasons[0] if reasons and len(set(reasons)) == 1 else ("failed" if reasons else "empty")
        words = "; ".join(u["error"] for u in unavailable) or "Digg listed no story"
        raise hand.HandError(f"no story could be read: {words}", reason=reason, unavailable=unavailable)
    ranked = sorted(stories.values(), key=lambda s: -s.get("score", 0))
    for story in ranked[:leading]:
        try:
            story["posts"], lost = x_posts(story["cluster"], most_posts)
            dropped += lost
        except digg.DiggError as exc:
            unavailable.append({"story": story["id"], "reason": exc.reason, "error": str(exc)})
    answer: Json = {"key": key, "fetched_at": hand.now(), "topics": list(digg.TOPICS), "stories": ranked,
                    "unavailable": unavailable, "dropped_by_screen": dropped, "source": "digg"}
    _keep(key, answer)
    return {**answer, "cached": False}


def story(cluster: str, most: int) -> Json:
    """One story's sources, every kind, at most MOST, screened."""
    meta, found = digg.posts(cluster)
    out: list[Json] = []
    dropped = 0
    for row in found:
        post = post_row(row)
        if not post:
            continue
        if not _screened(post["text"], post.get("by", "")):
            dropped += 1
            continue
        out.append(post)
        if len(out) >= most:
            break
    head: Json = {"cluster": cluster}
    for key, name in (("clusterUrlId", "id"), ("title", "title"), ("topic", "topic"), ("url", "link"),
                      ("count", "sources")):
        if meta.get(key) not in (None, ""):
            head[name] = meta[key]
    if isinstance(head.get("title"), str) and not _screened(head["title"]):
        head.pop("title")
        dropped += 1
    return {"story": head, "posts": out, "dropped_by_screen": dropped}
