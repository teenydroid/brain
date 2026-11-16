"""The read verbs' answers, each through the read cache. Safe to give a
drafting agent: nothing here writes anywhere but the cache.

Moltbook wraps answers as `{"success": true, ...}`; the wrapper is dropped
and long strings are clipped, since what a reader gets is model context."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .. import hand, peerfile
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
        "peer": 10 * MINUTE,
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


#: How much of a post's body a brief feed keeps.
BRIEF_CHARS = 400


def _brief(post: Mapping[str, Any]) -> Json:
    """A post without the API's bulk (the author's profile, flags, labels)
    and with its body cut: what a reader needs to have a take on it."""
    content = post.get("content")
    out = {
        "id": post.get("id"),
        "title": post.get("title"),
        "content": content[:BRIEF_CHARS] if isinstance(content, str) else None,
        "author": _name(post.get("author")),
        "submolt": _name(post.get("submolt")),
        "author_karma": _author_number(post, "karma"),
        "author_followers": _author_number(post, "followerCount", "follower_count"),
        "score": post.get("score"),
        "comment_count": post.get("comment_count"),
        "created_at": post.get("created_at"),
    }
    return {k: v for k, v in out.items() if v is not None}


def _author_number(post: Mapping[str, Any], *keys: str) -> int | None:
    author = post.get("author")
    if isinstance(author, Mapping):
        for key in keys:
            value = author.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(value)
    return None


def _when(value: Any) -> float | None:
    """An ISO time as Moltbook writes it ("2026-11-15T06:00:22.128Z") in
    epoch seconds; None when it is not one."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def age_hours(post: Mapping[str, Any]) -> float | None:
    """How old a post is, in hours, by its `created_at`; None when unknown."""
    at = _when(post.get("created_at"))
    return None if at is None else max(0.0, (hand.now() - at) / HOUR)


#: Posts a filtered feed reads from each submolt before filtering: the newest
#: 50 of m/general span well under an hour.
FILTER_FETCH = 50


def _teenys(api: RestApi | None) -> tuple[str, set[str]]:
    """Teeny's own name (lowercase) and the posts it has written or already
    commented on, from the ledger (any status the caps count)."""
    from . import rules  # rules is the ledger's owner; imported late, as it imports nothing of ours

    conn = rules.db()
    marks = ",".join("?" * len(rules.COUNTED))
    seen = {r[0] for r in conn.execute(f"SELECT post_id FROM moltbook_comments WHERE status IN ({marks})", rules.COUNTED)}
    seen |= {r[0] for r in conn.execute("SELECT post_id FROM moltbook_posts WHERE post_id IS NOT NULL")}
    return me_name(api).lower(), {str(p) for p in seen if p}


def _kept(posts: list[Any], filters: Mapping[str, Any], api: RestApi | None) -> list[Mapping[str, Any]]:
    """`posts` that pass every filter given. A post whose age or author karma
    is unknown fails a filter on it: nothing unknown gets through."""
    max_age = filters.get("max_age_hours")
    max_karma = filters.get("max_author_karma")
    min_karma = filters.get("min_author_karma")
    max_comments = filters.get("max_comments")
    me, seen = _teenys(api) if filters.get("skip_mine") else ("", set())
    out = []
    for post in posts:
        if not isinstance(post, Mapping):
            continue
        if max_age is not None:
            age = age_hours(post)
            if age is None or age > max_age:
                continue
        karma = _author_number(post, "karma")
        if (max_karma is not None or min_karma is not None) and karma is None:
            continue
        if max_karma is not None and karma > max_karma:
            continue
        if min_karma is not None and karma < min_karma:
            continue
        if max_comments is not None:
            count = post.get("comment_count")
            if not isinstance(count, int) or count > max_comments:
                continue
        if filters.get("skip_mine"):
            author = _name(post.get("author"))
            if (isinstance(author, str) and author.lower() == me) or str(post.get("id")) in seen:
                continue
        out.append(post)
    return out


def _newest_first(posts: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(posts, key=lambda p: _when(p.get("created_at")) or 0.0, reverse=True)


FILTERS = ("max_age_hours", "max_author_karma", "min_author_karma", "max_comments", "skip_mine")


def feed(
    submolt: str | list[str] | None,
    sort: str,
    max_results: int,
    api: RestApi | None = None,
    brief: bool = False,
    **filters: Any,
) -> Json:
    """A feed as Moltbook sends it. `brief` keeps at most `max_results`
    posts, each cut down (`_brief`), small enough to be a playbook's input
    or ride in a nudge; Moltbook's own answer runs to kilobytes a post.

    `submolt` may name several submolts: each is read (one request, cached
    like any feed) and the posts are merged, newest first. `filters` (any of
    FILTERS) drop posts before the cut: older than `max_age_hours`, by an
    author above `max_author_karma` or below `min_author_karma`, with more
    than `max_comments` comments, and (`skip_mine`) Teeny's own posts and
    those it already commented on, by the ledger. With a filter, each submolt
    is read FILTER_FETCH deep and at most `max_results` posts are kept."""
    if sort not in SORTS:
        raise hand.HandError(f"--sort is one of {', '.join(SORTS)}")
    unknown = set(filters) - set(FILTERS)
    if unknown:
        raise TypeError(f"feed() got unknown filters {sorted(unknown)}")
    filters = {k: v for k, v in filters.items() if v is not None and v is not False}
    asked = submolt if isinstance(submolt, list) else [submolt] if submolt else []
    names = list(dict.fromkeys(client.submolt_name(s, "--submolt") for s in asked))
    if len(names) > 1 or filters:
        return _merged(names, sort, max_results, api, brief, filters)
    name = names[0] if names else None
    query = [("sort", sort), ("limit", str(max_results))]
    path = f"/submolts/{quote(name)}/feed" if name else "/posts"
    got = CACHE.cached("feed", (name, sort, max_results), lambda: _get(api, path, query))
    out: Json = {"posts": _pick(got, "posts", "data")}
    if brief:
        posts = out["posts"] if isinstance(out["posts"], list) else []
        out["posts"] = [_brief(_in(p, name)) for p in posts[:max_results] if isinstance(p, Mapping)]
    elif isinstance(got, Mapping) and got.get("next_cursor"):
        out["next_cursor"] = got["next_cursor"]
    if name:
        out["submolt"] = name
    return out


def _in(post: Mapping[str, Any], name: str | None) -> Mapping[str, Any]:
    """A submolt's own feed leaves the submolt off each post; it is put back."""
    return {**post, "submolt": name} if name and post.get("submolt") is None else post


def _merged(names: list[str], sort: str, max_results: int, api: RestApi | None, brief: bool, filters: Mapping[str, Any]) -> Json:
    fetch = FILTER_FETCH if filters else max_results
    posts: list[Any] = []
    errors: list[Json] = []
    for name in names or [None]:
        query = [("sort", sort), ("limit", str(fetch))]
        path = f"/submolts/{quote(name)}/feed" if name else "/posts"
        try:
            got = CACHE.cached("feed", (name, sort, fetch), lambda path=path, query=query: _get(api, path, query))
        except RestError as error:
            errors.append({"submolt": name, "error": str(error)})
            if error.status == 429:
                break
            continue
        listed = _pick(got, "posts", "data")
        posts += [_in(p, name) for p in listed if isinstance(p, Mapping)] if isinstance(listed, list) else []
    if errors and not posts:
        raise hand.HandError("; ".join(f"{e['submolt'] or 'all'}: {e['error']}" for e in errors))
    first: dict[str, Mapping[str, Any]] = {}
    for p in posts:
        first.setdefault(str(p.get("id")), p)
    unique = list(first.values())
    kept = _newest_first(_kept(unique, filters, api) if filters else unique)[:max_results]
    out: Json = {"posts": [_brief(p) for p in kept] if brief else kept, "seen": len(unique)}
    if names:
        out["submolts"] = names
    if filters:
        out["filters"] = dict(filters)
    if errors:
        out["errors"] = errors
    return out


#: Teeny's peers: one Moltbook agent name a line, `#` starts a comment. Teeny
#: may edit it (it is under brain/, not frozen).
PEERS_FILE = "brain/moltbook-peers.txt"
#: At most this many peers are read a call (one request each), so a long
#: list cannot run past Moltbook's request limits.
PEERS_MAX = 20


def peer_names() -> tuple[list[str], list[str]]:
    """The peers file's names, in order and once each, and the lines that
    are not agent names (left out, and said so)."""
    return peerfile.names(PEERS_FILE, client.agent_name)


def peers(per_peer: int = 2, api: RestApi | None = None, brief: bool = True, **filters: Any) -> Json:
    """Each peer's newest `per_peer` posts (`/posts?author=NAME&sort=new`,
    cached 10 minutes), through the same `filters` as `feed`, newest first.
    A post Moltbook returns that is not by that peer is dropped, so the read
    never turns into the global feed if the author filter is ignored."""
    unknown = set(filters) - set(FILTERS)
    if unknown:
        raise TypeError(f"peers() got unknown filters {sorted(unknown)}")
    filters = {k: v for k, v in filters.items() if v is not None and v is not False}
    names, skipped = peer_names()
    out: Json = {"peers": names[:PEERS_MAX]}
    if len(names) > PEERS_MAX:
        out["not_read"] = names[PEERS_MAX:]
    if skipped:
        out["skipped"] = skipped
    posts: list[Mapping[str, Any]] = []
    errors: list[Json] = []
    for name in names[:PEERS_MAX]:
        query = [("author", name), ("sort", "new"), ("limit", str(per_peer))]
        try:
            got = CACHE.cached("peer", (name.lower(), per_peer), lambda query=query: _get(api, "/posts", query))
        except RestError as error:
            errors.append({"peer": name, "error": str(error)})
            if error.status == 429:
                break
            continue
        listed = _pick(got, "posts", "data")
        for post in listed if isinstance(listed, list) else []:
            author = _name(post.get("author")) if isinstance(post, Mapping) else None
            if isinstance(author, str) and author.lower() == name.lower():
                posts.append(post)
    kept = _newest_first(_kept(posts, filters, api) if filters else posts)
    out["posts"] = [_brief(p) for p in kept] if brief else kept
    if filters:
        out["filters"] = dict(filters)
    if errors:
        out["errors"] = errors
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
