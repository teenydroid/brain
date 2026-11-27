"""Digg's public reads, straight from Python with urllib: why there is an
answer, or why there is none.

Two public, read-only endpoints on digg.com, neither of which wants a login,
a key or a browser's TLS fingerprint:

  top    GET  /api/feed/continuous?topic=T&view=top&range=24h&published=1
         the ranked feed the site's own pages call. It is the one place that
         carries each story's rank and Digg Score per topic, so `top` reads it
         (Digg's MCP `digg_get_feed` lists the same stories but not their
         score, post count or author count).
  posts  POST /mcp, JSON-RPC 2.0 `tools/call` of `digg_get_post` (the id,
         sourceOffset, sourceLimit), answered as an SSE `event: message` /
         `data: {...}` frame or as plain JSON.

This replaces the Go binary `digg-pp-cli` (mvanhorn/printing-press-library,
Apache-2.0) that used to be built into the image: it made exactly these
calls, and a dependency of it vanished from GitHub. The shapes `call()`,
`top()` and `posts()` return are the ones that tool printed, so top.py reads
them unchanged. Nothing is written to disk and no local store is kept.

Each request carries only its body (none for the feed) and a generic
User-Agent: no cookie, no key, nothing from this process's environment. Every
request is bounded by TREND_TIMEOUT_SECS (1..120, default 30). TREND_DIGG_BASE
(secrets/teeny.env, https only, no path) names another host, for tests.

Every failure is a `DiggError` with a `reason` a caller can branch on:

  missing       kept in the vocabulary for callers and the hand's docs; nothing
                raises it now, since there is no binary to be missing
  timeout       no answer within TREND_TIMEOUT_SECS
  rate_limited  HTTP 429, or Digg said to come back later ("temporarily
                unavailable, retry after ...")
  failed        any other HTTP, network or JSON-RPC failure, or an MCP result
                flagged isError
  bad_json      an answer that is not the JSON asked for
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .. import hand

BASE = "https://digg.com"
FEED_PATH = "/api/feed/continuous"
MCP_PATH = "/mcp"
USER_AGENT = "teeny-hands-trend/1.0"
#: The topics `top` reads, in Digg's own names. Digg's leaderboard has
#: seven (ai, tech, world-business, sports, science, gaming, entertainment);
#: the studio wants the news people argue about, not only AI (2026-11-22).
TOPICS = ("ai", "tech", "world-business", "entertainment", "sports", "gaming")
#: Digg's story ids as the posts verb takes them (clusterId, never the short
#: clusterUrlId).
CLUSTER = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_TOPIC = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_RATE = re.compile(r"temporarily unavailable|retry after|rate.?limit|too many requests|\b429\b", re.I)
#: One response body is read to this many bytes.
MAX_BYTES = 8 << 20
#: The feed serves 20 stories a page; a topic is read for at most this many pages.
FEED_PAGES = 10
#: digg_get_post's own limits: sources per page, and the highest offset.
SOURCE_PAGE = 20
SOURCE_OFFSET_MAX = 499


class DiggError(hand.HandError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message, reason=reason)
        self.reason = reason


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Digg's endpoints answer where they are; a redirect is a failure, not a
    hop to somewhere this hand did not choose."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def base() -> str:
    """Digg's origin: https://digg.com, or TREND_DIGG_BASE (https only)."""
    named = hand.setting("TREND_DIGG_BASE")
    if not named:
        return BASE
    parts = urllib.parse.urlsplit(named)
    if parts.scheme != "https" or not parts.hostname or parts.path not in ("", "/") or parts.query or parts.fragment \
            or parts.username or parts.password:
        raise DiggError("failed", "TREND_DIGG_BASE must be an https origin (https://host[:port]), nothing more")
    return f"https://{parts.netloc}"


def _said(text: str) -> str:
    """The last words of an answer, as one short line."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:300]


def _fail(reason_text: str, what: str, said: str) -> DiggError:
    return DiggError("rate_limited" if _RATE.search(said) else "failed", f"{what}: {reason_text}: {_said(said)}")


def _send(url: str, body: bytes | None, what: str) -> bytes:
    """One request: GET, or POST of a JSON-RPC body. The answer's bytes, or a DiggError."""
    secs = hand.number("TREND_TIMEOUT_SECS", 30, 1, 120)
    headers = {"User-Agent": USER_AGENT}
    if body is None:
        headers["Accept"] = "application/json"
    else:
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json, text/event-stream"
    request = urllib.request.Request(url, data=body, headers=headers, method="GET" if body is None else "POST")
    try:
        with _OPENER.open(request, timeout=secs) as got:
            return got.read(MAX_BYTES + 1)[:MAX_BYTES]
    except urllib.error.HTTPError as exc:
        try:
            said = exc.read(2000).decode("utf-8", "replace")
        except (OSError, ValueError):
            said = ""
        finally:
            exc.close()
        if exc.code == 429:
            raise DiggError("rate_limited", f"{what}: Digg answered HTTP 429: {_said(said)}") from None
        # A JSON-RPC error body explains more than the status does.
        if body is not None:
            try:
                rpc = json.loads(strip_sse(said.encode()))
            except ValueError:
                rpc = None
            if isinstance(rpc, dict) and rpc.get("error"):
                try:
                    unwrap(what, said.encode())
                except DiggError as inner:
                    raise DiggError(inner.reason, f"{inner} (HTTP {exc.code})") from None
        raise _fail(f"Digg answered HTTP {exc.code}", what, said) from None
    except (TimeoutError, socket.timeout):
        raise DiggError("timeout", f"{what} gave no answer within {secs} s") from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise DiggError("timeout", f"{what} gave no answer within {secs} s") from None
        raise DiggError("failed", f"{what}: could not reach Digg: {exc.reason}") from None
    except OSError as exc:
        raise DiggError("failed", f"{what}: could not reach Digg: {exc.strerror or exc}") from None


def strip_sse(raw: bytes) -> str:
    """The JSON of a single-event text/event-stream body (its `data:` lines,
    joined); a plain JSON body as it is."""
    text = raw.decode("utf-8", "replace").strip()
    if not (text.startswith(("data:", "event:")) or "\ndata:" in text):
        return text
    data = "".join(line.strip()[5:].strip() for line in text.splitlines() if line.strip().startswith("data:"))
    return data or text


def unwrap(what: str, raw: bytes) -> Any:
    """A `tools/call` answer's result: structuredContent when there is one,
    else the first text content parsed as JSON. A JSON-RPC error or an
    isError result is a failed (or rate_limited) DiggError."""
    payload = strip_sse(raw)
    try:
        env = json.loads(payload)
    except ValueError:
        raise DiggError("bad_json", f"{what} did not answer JSON: {_said(payload)!r}") from None
    if not isinstance(env, dict):
        raise DiggError("bad_json", f"{what} did not answer a JSON-RPC response: {_said(payload)!r}")
    error = env.get("error")
    if error:
        message = str(error.get("message", "")) if isinstance(error, dict) else str(error)
        code = error.get("code", "") if isinstance(error, dict) else ""
        raise _fail(f"JSON-RPC error {code}", what, message)
    result = env.get("result")
    if not isinstance(result, dict):
        raise DiggError("bad_json", f"{what}: the MCP answer has no result or error")
    text = ""
    for part in result.get("content") or []:
        if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
            text = str(part["text"]).strip()
            break
    if result.get("isError"):
        raise _fail("the tool reported an error", what, text or "no message")
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    try:
        return json.loads(text)
    except ValueError:
        raise DiggError("bad_json", f"{what}: the MCP result has no structured content") from None


def call(tool: str, **arguments: Any) -> Any:
    """One MCP tool on Digg, its structured result, or a DiggError."""
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    raw = _send(base() + MCP_PATH, json.dumps(request).encode(), tool)
    return unwrap(tool, raw)


def _feed(topic: str, cursor: str | None) -> dict:
    query = {"topic": topic, "view": "top", "range": "24h", "published": "1"}
    if cursor:
        query["cursor"] = cursor
    raw = _send(f"{base()}{FEED_PATH}?{urllib.parse.urlencode(query)}", None, f"feed {topic}")
    try:
        got = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise DiggError("bad_json", f"feed {topic} did not answer JSON: {_said(raw.decode('utf-8', 'replace'))!r}") from None
    if not isinstance(got, dict) or not isinstance(got.get("posts"), list):
        raise DiggError("bad_json", f"feed {topic} answered with no list of posts")
    return got


def rows(got: Any, what: str) -> list[dict]:
    """The rows of an answer, which is a bare list or a `{meta, results}`
    envelope."""
    if isinstance(got, dict):
        got = got.get("results")
    if not isinstance(got, list):
        raise DiggError("bad_json", f"{what} answered with no list of results")
    return [row for row in got if isinstance(row, dict)]


def _row(topic: str, story: dict) -> dict:
    summary = story.get("summary") if isinstance(story.get("summary"), dict) else {}
    short = story.get("clusterUrlId")
    return {"rank": story.get("rank"), "clusterId": story.get("clusterId"), "clusterUrlId": short,
            "url": f"https://digg.com/{topic}/{short}", "topic": topic, "title": summary.get("title", ""),
            "description": summary.get("description", ""), "diggScore": story.get("diggScore"),
            "postCount": story.get("postCount"), "uniqueAuthors": story.get("uniqueAuthors"),
            "publishedAt": story.get("createdAt")}


def top(topic: str, limit: int) -> list[dict]:
    """One topic's leaderboard, live, in Digg's rank order, at most LIMIT."""
    if not _TOPIC.match(topic):
        raise hand.HandError("a topic is one of Digg's slugs, such as ai")
    found: list[dict] = []
    cursor: str | None = None
    for _ in range(FEED_PAGES):
        page = _feed(topic, cursor)
        found += [_row(topic, s) for s in page["posts"] if isinstance(s, dict)]
        cursor = page.get("nextCursor")
        # An empty page with a cursor would otherwise loop for ever.
        if len(found) >= limit or not isinstance(cursor, str) or not cursor or not page["posts"]:
            break
    return found[:limit]


def _x_handle(url: str) -> tuple[str, str]:
    """The handle and post id of https://x.com/<handle>/status/<id>; Digg
    gives the handle nowhere else."""
    parts = [p for p in urllib.parse.urlsplit(url).path.split("/") if p]
    if len(parts) < 3 or parts[1] != "status" or parts[0] == "i":
        return "", ""
    return parts[0], parts[2]


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _post_row(source: dict) -> dict:
    kind, url = _text(source.get("kind")), _text(source.get("url"))
    author = {"display_name": _text(source.get("author"))}
    row: dict[str, Any] = {"kind": kind, "author": author, "body": source.get("text") if isinstance(source.get("text"), str) else None}
    if kind == "x":
        author["username"], post_id = _x_handle(url)
        if post_id:
            row["post_x_id"] = post_id
    if _text(source.get("publishedAt")):
        row["posted_at"] = source["publishedAt"]
    if url:
        row["xUrl"] = url
    if _text(source.get("title")):
        row["title"] = source["title"]
    for name in ("username", "display_name"):
        if not author.get(name):
            author.pop(name, None)
    return row


def posts(cluster: str) -> tuple[dict, list[dict]]:
    """One story's sources, live: (its meta, its rows in Digg's order)."""
    if not CLUSTER.match(cluster):
        raise hand.HandError("a story is named by its clusterId, a UUID")
    story: dict = {}
    sources: list[dict] = []
    truncated = False
    offset = 0
    while True:
        page = call("digg_get_post", id=cluster, sourceOffset=offset, sourceLimit=SOURCE_PAGE)
        # A result without its story or its sources is a changed answer, not
        # a story with no sources.
        if not isinstance(page, dict) or not isinstance(page.get("story"), dict) or not isinstance(page.get("sources"), list):
            raise DiggError("bad_json", "digg_get_post: the answer has no story object or no sources array")
        story = page["story"]
        got = [s for s in page["sources"] if isinstance(s, dict)]
        sources += got
        truncated = bool(page.get("sourceLimitReached"))
        nxt = page.get("nextSourceOffset")
        # An empty page or an offset that does not advance would loop for ever.
        if not isinstance(nxt, int) or isinstance(nxt, bool) or not page["sources"] or nxt <= offset:
            break
        if nxt > SOURCE_OFFSET_MAX:
            truncated = True
            break
        offset = nxt
    link = _text(story.get("url"))
    short = link.split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1]
    out = [_post_row(s) for s in sources]
    meta = {"clusterId": story.get("id"), "clusterUrlId": short, "topic": story.get("topic"), "url": link,
            "title": story.get("title"), "source": "live", "by": "rank", "count": len(out),
            "sourcesTruncated": truncated}
    return meta, out
