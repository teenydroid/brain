"""Shared by the hands/trend tests: a scratch TEENY_ROOT and a fake Digg,
which is urllib's opener patched to answer from fixtures and to log how it
was called. Nothing here talks to the network."""

from __future__ import annotations

import io
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any
from unittest import mock

LIB = Path(__file__).resolve().parents[2] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from teeny_hands import hand  # noqa: E402

T0 = 1_795_000_000.0  # 2026-11-18


def sse(payload: Any) -> bytes:
    """A tools/call answer as Digg frames it: one SSE message."""
    return b"event: message\ndata: " + json.dumps(payload).encode() + b"\n\n"


def tool_result(structured: Any) -> dict:
    return {"result": {"content": [{"type": "text", "text": json.dumps(structured)}], "structuredContent": structured}}


class Reply:
    """What urllib's opener returns: a context manager with read()."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self, n: int = -1) -> bytes:
        return self.body if n < 0 else self.body[:n]

    def __enter__(self) -> "Reply":
        return self

    def __exit__(self, *_: Any) -> None:
        return None


def http_error(url: str, code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, f"HTTP {code}", {}, io.BytesIO(body))  # type: ignore[arg-type]


def cluster(n: int) -> str:
    return f"{n:08x}-0000-4000-8000-{n:012x}"


def row(n: int, topic: str, score: int, title: str | None = None, **more: Any) -> dict:
    """A leaderboard row as digg.top() returns it."""
    return {"rank": n, "clusterId": cluster(n), "clusterUrlId": f"s{n}", "url": f"https://digg.com/{topic}/s{n}",
            "topic": topic, "title": title or f"story {n}", "description": f"what story {n} says",
            "diggScore": score, "postCount": 10, "uniqueAuthors": 8, "publishedAt": "2026-11-18T05:00:00+00:00",
            **more}


def post(name: str, body: str, kind: str = "x", n: int = 1) -> dict:
    """A source as digg.posts() returns it."""
    return {"author": {"display_name": name, "username": name.lower().replace(" ", "")}, "body": body, "kind": kind,
            "post_x_id": str(n), "posted_at": "2026-11-18T05:30:00.000Z", "xUrl": f"https://x.com/u/status/{n}"}


def feed_story(r: dict) -> dict:
    """A row() as digg.com's feed lists it (no topic, no url: the caller knows them)."""
    return {"clusterId": r["clusterId"], "clusterUrlId": r["clusterUrlId"], "rank": r["rank"],
            "diggScore": r["diggScore"], "createdAt": r["publishedAt"], "postCount": r["postCount"],
            "uniqueAuthors": r["uniqueAuthors"], "summary": {"title": r["title"], "description": r["description"]}}


def source(p: dict) -> dict:
    """A post() as digg_get_post lists a source."""
    return {"id": p["post_x_id"], "kind": p["kind"], "author": p["author"]["display_name"], "title": None,
            "text": p["body"], "url": p["xUrl"], "publishedAt": p["posted_at"]}


class Scratch(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = str(T0)
        for key in ("DRY_RUN", "TEENY_HANDS_DEBUG"):
            os.environ.pop(key, None)
        #: "feed:<topic>" -> a feed page or a list of pages; "post:<cluster>" -> a story and its sources
        self.fixtures: dict[str, Any] = {}
        #: "top:<topic>" or "posts:<cluster>" -> how Digg answers: ok, rate (HTTP 429), fail (an isError
        #: result), error (a JSON-RPC error), garbage, sleep (a timeout), urlsleep (a timeout as URLError),
        #: http500, down, redirect, plain (JSON, not an SSE frame)
        self.how: dict[str, str] = {}
        self.sent: list[dict] = []
        patch = mock.patch("urllib.request.OpenerDirector.open", autospec=True, side_effect=self._open)
        patch.start()
        self.addCleanup(patch.stop)
        self.configure()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **overrides: str | None) -> None:
        values: dict[str, str | None] = {"TREND_TIMEOUT_SECS": "5"}
        values.update(overrides)
        text = "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    # --- the fake Digg -------------------------------------------------------------
    def _open(self, _opener: Any, request: Any, timeout: float | None = None) -> Reply:
        url = request.full_url
        parts = urllib.parse.urlsplit(url)
        body = request.data
        entry: dict[str, Any] = {"url": url, "method": request.get_method(), "headers": dict(request.header_items()),
                                 "body": json.loads(body) if body else None, "timeout": timeout}
        self.sent.append(entry)
        if parts.path == "/api/feed/continuous":
            query = dict(urllib.parse.parse_qsl(parts.query))
            entry["query"] = query
            page = self.fixtures.get(f"feed:{query['topic']}", {"posts": []})
            pages = page if isinstance(page, list) else [page]
            at = int(query["cursor"]) if "cursor" in query else 0
            got = {**pages[at], "nextCursor": str(at + 1) if at + 1 < len(pages) else None}
            return self._answer(f"top:{query['topic']}", url, json.dumps(got).encode(), framed=False)
        if parts.path == "/mcp":
            args = json.loads(body)["params"]["arguments"]
            found = self.fixtures.get(f"post:{args['id']}")
            if found is None:
                return self._answer(f"posts:{args['id']}", url,
                                    sse({"error": {"code": -32602, "message": "Post not found."}}))
            offset, size = args["sourceOffset"], args["sourceLimit"]
            more = offset + size < len(found["sources"])
            result = {"story": found["story"], "sources": found["sources"][offset:offset + size], "follows": None,
                      "newer": None, "nextSourceOffset": offset + size if more else None, "sourceLimitReached": False}
            return self._answer(f"posts:{args['id']}", url, sse(tool_result(result)))
        raise AssertionError(f"unexpected request {url}")

    def _answer(self, key: str, url: str, good: bytes, framed: bool = True) -> Reply:
        how = self.how.get(key, "ok")
        if how == "rate":
            raise http_error(url, 429, b"temporarily unavailable, retry after 60s")
        if how == "fail":
            return Reply(sse({"result": {"isError": True,
                                         "content": [{"type": "text", "text": "digg_get_post: Post not found."}]}}))
        if how == "error":
            return Reply(sse({"error": {"code": -32000, "message": "Post not found."}}))
        if how == "garbage":
            return Reply(b"<html>not json</html>")
        if how == "sleep":
            raise TimeoutError("timed out")
        if how == "urlsleep":
            raise urllib.error.URLError(socket.timeout("timed out"))
        if how == "http500":
            raise http_error(url, 500, b"boom")
        if how == "down":
            raise urllib.error.URLError(OSError("name resolution failed"))
        if how == "redirect":
            raise http_error(url, 308, b"")
        if how == "plain" and framed:
            return Reply(good.split(b"data: ", 1)[1].strip())
        return Reply(good)

    # --- fixtures ------------------------------------------------------------------
    def board(self, topic: str, *rows: dict) -> None:
        self.fixtures[f"feed:{topic}"] = {"posts": [feed_story(r) for r in rows]}

    def pages(self, topic: str, *boards: tuple[dict, ...]) -> None:
        self.fixtures[f"feed:{topic}"] = [{"posts": [feed_story(r) for r in b]} for b in boards]

    def sources(self, n: int, *posts: dict) -> None:
        self.fixtures[f"post:{cluster(n)}"] = {
            "story": {"id": cluster(n), "url": f"https://digg.com/ai/s{n}", "topic": "ai", "title": f"story {n}"},
            "sources": [source(p) for p in posts]}

    def behave_keys(self, mapping: dict[str, str]) -> None:
        self.how = dict(mapping)

    def calls(self) -> list[dict]:
        return self.sent

    def mcp_calls(self) -> list[dict]:
        return [c for c in self.sent if c["body"]]
