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
        "study": 6 * HOUR,
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


def home(api: RestApi | None = None, expand: bool = False) -> Json:
    got = CACHE.cached("home", (), lambda: _get(api, "/home"))
    if expand and isinstance(got, Mapping) and isinstance(got.get("activity_on_your_posts"), list):
        got = {**got, "activity_on_your_posts": _expand_mentions(got["activity_on_your_posts"], api)}
    return {"home": got}


#: Posts `home --expand` fetches per call, at most.
EXPAND_MAX = 5
#: Latest comments attached to an expanded post.
EXPAND_COMMENTS = 5
#: Activity Moltbook says is on Teeny's own post: its comments reach Teeny
#: through `inbox`, so it is never fetched.
_OWN_POST_PREVIEW = "commented on your post"


def _name(value: Any) -> Any:
    return value.get("name") if isinstance(value, Mapping) else value


def _expand_mentions(items: list[Any], api: RestApi | None) -> list[Any]:
    """`items` with each mention of Teeny in someone else's post carrying
    that post under `post` (untrusted content, like `feed`'s). A post is a
    mention when its author is not Teeny; a fetch that fails leaves its item
    as Moltbook sent it, and so does not knowing Teeny's own name."""
    try:
        me = me_name(api).lower()
    except (RestError, hand.HandError):
        return items
    out: list[Any] = []
    fetched = 0
    for item in items:
        if (
            fetched >= EXPAND_MAX
            or not isinstance(item, Mapping)
            or not item.get("post_id")
            or _OWN_POST_PREVIEW in str(item.get("preview", "")).lower()
        ):
            out.append(item)
            continue
        fetched += 1
        try:
            got = post(str(item["post_id"]), api)["post"]
        except (RestError, hand.HandError):
            got = None
        author = _name(got.get("author")) if isinstance(got, Mapping) else None
        if not isinstance(author, str) or author.lower() == me:
            out.append(item)
            continue
        expanded: Json = {
            "id": got.get("id"),
            "title": got.get("title"),
            "content": got.get("content"),
            "author": author,
            "submolt": _name(got.get("submolt")),
            "created_at": got.get("created_at"),
        }
        try:
            latest = comments(str(item["post_id"]), "new", EXPAND_COMMENTS, api)["comments"]
        except (RestError, hand.HandError):
            latest = None
        if isinstance(latest, list):
            expanded["comments"] = [
                {"id": c.get("id"), "author": _name(c.get("author")), "content": c.get("content"), "created_at": c.get("created_at")}
                for c in latest[:EXPAND_COMMENTS]
                if isinstance(c, Mapping)
            ]
        out.append({**item, "post": expanded})
    return out


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
