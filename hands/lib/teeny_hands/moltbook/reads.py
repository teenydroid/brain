"""The read verbs' answers, each through the read cache. Safe to give a
drafting agent: nothing here writes anywhere but the cache.

Moltbook wraps answers as `{"success": true, ...}`; the wrapper is dropped
and long strings are clipped, since what a reader gets is model context."""

from __future__ import annotations

from typing import Any, Mapping

from .. import hand
from ..readcache import ReadCache
from ..rest import RestApi, RestError, quote
from . import client

MINUTE = 60.0
HOUR = 3600.0

CACHE = ReadCache(
    "moltbook_cache",
    "MOLTBOOK_API_KEY",
    {
        "whoami": 10 * MINUTE,
        "status": MINUTE,
        "home": MINUTE,
        "feed": 5 * MINUTE,
        "post": 5 * MINUTE,
        "comments": 2 * MINUTE,
        "search": 10 * MINUTE,
        "profile": HOUR,
        "submolts": HOUR,
        "submolt": HOUR,
    },
    "MOLTBOOK_CACHE_ENABLED",
)

SORTS = ("hot", "new", "top", "rising")
COMMENT_SORTS = ("best", "new", "old")
SEARCH_TYPES = ("posts", "comments", "all")
_TEXT_CHARS = 4000

Json = dict[str, Any]


def _clip(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= _TEXT_CHARS else value[:_TEXT_CHARS] + "…"
    if isinstance(value, list):
        return [_clip(v) for v in value]
    if isinstance(value, Mapping):
        return {k: _clip(v) for k, v in value.items() if k != "success"}
    return value


def _api(api: RestApi | None) -> RestApi:
    return api or client.api()


def _get(api: RestApi | None, path: str, query: list[tuple[str, str]] | None = None) -> Any:
    return _clip(_api(api).call("GET", path, query or []))


def _pick(got: Any, *keys: str) -> Any:
    if isinstance(got, Mapping):
        for key in keys:
            if key in got:
                return got[key]
    return got


def whoami(api: RestApi | None = None) -> Json:
    got = CACHE.cached("whoami", (), lambda: _get(api, "/agents/me"))
    return {"agent": _pick(got, "agent", "data")}


def me_name(api: RestApi | None = None) -> str:
    """Teeny's own agent name: `MOLTBOOK_AGENT_NAME`, else /agents/me."""
    configured = hand.setting("MOLTBOOK_AGENT_NAME")
    if configured:
        return configured.strip().removeprefix("u/")
    agent = whoami(api)["agent"]
    name = agent.get("name") if isinstance(agent, Mapping) else None
    if not isinstance(name, str) or not name:
        raise hand.HandError("Moltbook did not say what Teeny's agent name is (set MOLTBOOK_AGENT_NAME)")
    return name


def status(api: RestApi | None = None) -> Json:
    got = CACHE.cached("status", (), lambda: _get(api, "/agents/status"))
    return got if isinstance(got, dict) else {"status": got}


def home(api: RestApi | None = None) -> Json:
    got = CACHE.cached("home", (), lambda: _get(api, "/home"))
    return {"home": got}


def feed(submolt: str | None, sort: str, max_results: int, api: RestApi | None = None) -> Json:
    if sort not in SORTS:
        raise hand.HandError(f"--sort is one of {', '.join(SORTS)}")
    name = client.submolt_name(submolt, "--submolt") if submolt else None
    query = [("sort", sort), ("limit", str(max_results))]
    path = f"/submolts/{quote(name)}/feed" if name else "/posts"
    got = CACHE.cached("feed", (name, sort, max_results), lambda: _get(api, path, query))
    out: Json = {"posts": _pick(got, "posts", "data")}
    if isinstance(got, Mapping) and got.get("next_cursor"):
        out["next_cursor"] = got["next_cursor"]
    if name:
        out["submolt"] = name
    return out


def post(post_id: str, api: RestApi | None = None, fresh: bool = False) -> Json:
    pid = client.object_id(post_id, "ID")
    fetch = lambda: _get(api, f"/posts/{quote(pid)}")  # noqa: E731
    got = fetch() if fresh else CACHE.cached("post", (pid,), fetch)
    return {"post": _pick(got, "post", "data")}


def comments(post_id: str, sort: str = "new", max_results: int = 50, api: RestApi | None = None, fresh: bool = False) -> Json:
    pid = client.object_id(post_id, "ID")
    if sort not in COMMENT_SORTS:
        raise hand.HandError(f"--sort is one of {', '.join(COMMENT_SORTS)}")
    query = [("sort", sort), ("limit", str(max_results))]
    fetch = lambda: _get(api, f"/posts/{quote(pid)}/comments", query)  # noqa: E731
    got = fetch() if fresh else CACHE.cached("comments", (pid, sort, max_results), fetch)
    return {"post_id": pid, "comments": _pick(got, "comments", "data")}


def search(query: str, kind: str, max_results: int, api: RestApi | None = None) -> Json:
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise hand.HandError("a search is 1 to 500 characters")
    if kind not in SEARCH_TYPES:
        raise hand.HandError(f"--type is one of {', '.join(SEARCH_TYPES)}")
    params = [("q", query), ("type", kind), ("limit", str(max_results))]
    got = CACHE.cached("search", (query, kind, max_results), lambda: _get(api, "/search", params))
    return {"query": query, "results": _pick(got, "results", "data")}


def profile(name: str, api: RestApi | None = None) -> Json:
    who = client.agent_name(name, "NAME")
    got = CACHE.cached("profile", (who.lower(),), lambda: _get(api, "/agents/profile", [("name", who)]))
    out: Json = {"agent": _pick(got, "agent", "data")}
    for key in ("recentPosts", "recentComments"):
        if isinstance(got, Mapping) and key in got:
            out[key] = got[key]
    return out


def submolts(api: RestApi | None = None) -> Json:
    got = CACHE.cached("submolts", (), lambda: _get(api, "/submolts"))
    return {"submolts": _pick(got, "submolts", "data")}


def submolt(name: str, api: RestApi | None = None) -> Json | None:
    """One submolt's info (`allow_crypto` is what the crypto rule reads);
    None when Moltbook has no such submolt."""
    who = client.submolt_name(name)

    def fetch() -> Any:
        try:
            got = _get(api, f"/submolts/{quote(who)}")
        except RestError as error:
            if error.status == 404:
                return None
            raise
        return _pick(got, "submolt", "data")

    return CACHE.cached("submolt", (who,), fetch)
