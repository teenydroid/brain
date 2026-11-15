"""The read verbs' answers, each through the read cache. Safe to give a
drafting agent: nothing here writes anywhere but the cache.

Reddit answers in "Things" (`{"kind": "t3", "data": {...}}`) inside
"Listings", with a hundred fields a reader never needs. Each answer is cut
down to what a draft uses, and long text is clipped, since what a reader
gets is model context. Everything in it was written by strangers."""

from __future__ import annotations

from typing import Any, Iterator, Mapping

from .. import hand
from ..readcache import ReadCache
from ..rest import RestError
from . import client

MINUTE = 60.0
HOUR = 3600.0

CACHE = ReadCache(
    "reddit_cache",
    "REDDIT_CLIENT_ID",
    {
        "whoami": 10 * MINUTE,
        "feed": 5 * MINUTE,
        "thread": 2 * MINUTE,
        "info": 2 * MINUTE,
        "search": 10 * MINUTE,
        "user": HOUR,
        "subreddit": HOUR,
        "rules": HOUR,
        "study": 6 * HOUR,
    },
    "REDDIT_CACHE_ENABLED",
)

SORTS = ("hot", "new", "top", "rising")
COMMENT_SORTS = ("confidence", "new", "top", "old", "controversial", "qa")
TIMES = ("hour", "day", "week", "month", "year", "all")
_TEXT_CHARS = 4000
#: How deep a thread's replies are kept.
DEPTH = 6

Json = dict[str, Any]


def _clip(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= _TEXT_CHARS else value[:_TEXT_CHARS] + "…"
    return value


def _api(api: client.Reddit | None) -> client.Reddit:
    return api or client.api()


def _children(listing: Any) -> list[Mapping[str, Any]]:
    """The Things in a Listing, as (kind, data) mappings."""
    data = listing.get("data") if isinstance(listing, Mapping) else None
    kids = data.get("children") if isinstance(data, Mapping) else None
    return [k for k in kids if isinstance(k, Mapping)] if isinstance(kids, list) else []


def post_view(data: Mapping[str, Any]) -> Json:
    """A post (`t3`), as a draft reads it."""
    out: Json = {
        "id": data.get("id"),
        "subreddit": data.get("subreddit"),
        "title": _clip(data.get("title")),
        "author": data.get("author"),
        "created_utc": data.get("created_utc"),
        "score": data.get("score"),
        "num_comments": data.get("num_comments"),
        "permalink": data.get("permalink"),
        "flair": data.get("link_flair_text"),
    }
    if data.get("is_self"):
        out["body"] = _clip(data.get("selftext") or "")
    else:
        out["url"] = data.get("url")
    for flag in ("locked", "archived", "over_18", "stickied"):
        if data.get(flag):
            out[flag] = True
    return out


def comment_view(data: Mapping[str, Any], depth: int = 0) -> Json:
    """A comment (`t1`) and, down to DEPTH, its replies."""
    out: Json = {
        "id": data.get("id"),
        "author": data.get("author"),
        "body": _clip(data.get("body")),
        "parent_id": data.get("parent_id"),
        "score": data.get("score"),
        "created_utc": data.get("created_utc"),
    }
    replies = data.get("replies")
    if depth < DEPTH and isinstance(replies, Mapping):
        kids = [comment_view(k["data"], depth + 1) for k in _children(replies) if k.get("kind") == "t1" and isinstance(k.get("data"), Mapping)]
        if kids:
            out["replies"] = kids
    return out


def _posts(listing: Any) -> list[Json]:
    return [post_view(k["data"]) for k in _children(listing) if k.get("kind") == "t3" and isinstance(k.get("data"), Mapping)]


def _account_trouble(signal: str, reason: str) -> None:
    """A suspended account, or a 403 on its own read, stops every write
    (rules, back-offs) until the operator clears it."""
    from . import rules

    rules.signal(rules.ACCOUNT, signal, reason)


def whoami(api: client.Reddit | None = None, fresh: bool = False) -> Json:
    """The account. A fresh read that finds it suspended, or answers 403,
    starts the account's back-off; a cached answer starts nothing again."""

    def fetch() -> Json:
        try:
            got = _api(api).call("GET", "/api/v1/me")
        except RestError as error:
            if error.status == 403:
                _account_trouble("forbidden", "Reddit answered the account's own read with 403")
            raise
        got = got if isinstance(got, Mapping) else {}
        me = {
            key: got.get(key)
            for key in ("name", "link_karma", "comment_karma", "total_karma", "created_utc", "is_suspended", "has_verified_email")
            if key in got
        }
        if me.get("is_suspended"):
            _account_trouble("suspended", "Reddit says the account is suspended")
        return me

    return {"me": fetch() if fresh else CACHE.cached("whoami", (), fetch)}


def feed(subreddit: str | None, sort: str, max_results: int, time: str = "day", api: client.Reddit | None = None) -> Json:
    """One subreddit's posts or, with none named, every subreddit on the
    allowlist at once (Reddit's `r/a+b` multireddit)."""
    from . import rules

    if sort not in SORTS:
        raise hand.HandError(f"--sort is one of {', '.join(SORTS)}")
    if time not in TIMES:
        raise hand.HandError(f"--time is one of {', '.join(TIMES)}")
    if subreddit:
        names = [client.subreddit_name(subreddit, "SUBREDDIT")]
    else:
        names = [client.subreddit_name(n, "REDDIT_SUBREDDITS") for n in rules.allowed_subreddits()]
        if not names:
            raise hand.HandError("name a SUBREDDIT: REDDIT_SUBREDDITS is empty")
    name = "+".join(names)
    query = [("limit", str(max_results))]
    if sort == "top":
        query.append(("t", time))
    path = f"/r/{'+'.join(client.quote(n) for n in names)}/{sort}"
    got = CACHE.cached("feed", (name.lower(), sort, max_results, time), lambda: _posts(_api(api).call("GET", path, query)))
    return {"subreddit": name, "sort": sort, "posts": got}


def thread(post_id: str, sort: str = "confidence", max_results: int = 50, api: client.Reddit | None = None, fresh: bool = False) -> Json:
    """One post and its comment tree."""
    pid = client.base_id(post_id, "ID", "t3")
    if sort not in COMMENT_SORTS:
        raise hand.HandError(f"--sort is one of {', '.join(COMMENT_SORTS)}")

    def fetch() -> Json:
        got = _api(api).call("GET", f"/comments/{client.quote(pid)}", [("sort", sort), ("limit", str(max_results)), ("depth", str(DEPTH))])
        if not isinstance(got, list) or len(got) < 2:
            raise hand.HandError("Reddit sent a thread this hand cannot read")
        posts = _posts(got[0])
        comments = [comment_view(k["data"]) for k in _children(got[1]) if k.get("kind") == "t1" and isinstance(k.get("data"), Mapping)]
        return {"post": posts[0] if posts else None, "comments": comments}

    got = fetch() if fresh else CACHE.cached("thread", (pid, sort, max_results), fetch)
    return {"post_id": pid, **got}


def info(fullname: str, api: client.Reddit | None = None, fresh: bool = True) -> Mapping[str, Any] | None:
    """One post or comment by fullname (`t3_…`, `t1_…`), Reddit's own data
    trimmed by `post_view`/`comment_view` plus what the rules read
    (`link_id`, `subreddit`, `locked`, `archived`). None when Reddit has no
    such thing. Fresh by default: a rule must not decide on a stale read."""

    def fetch() -> Any:
        kids = _children(_api(api).call("GET", "/api/info", [("id", fullname)]))
        if not kids or not isinstance(kids[0].get("data"), Mapping):
            return None
        data = kids[0]["data"]
        view = post_view(data) if kids[0].get("kind") == "t3" else comment_view(data, DEPTH)
        view.update({k: data.get(k) for k in ("link_id", "subreddit", "locked", "archived", "over_18", "name") if k in data})
        return view

    return fetch() if fresh else CACHE.cached("info", (fullname,), fetch)


def search(query: str, subreddit: str | None, sort: str, max_results: int, api: client.Reddit | None = None) -> Json:
    if not isinstance(query, str) or not query.strip() or len(query) > 512:
        raise hand.HandError("a search is 1 to 512 characters")
    if sort not in ("relevance", "new", "top", "comments"):
        raise hand.HandError("--sort is one of relevance, new, top, comments")
    name = client.subreddit_name(subreddit, "--subreddit") if subreddit else None
    params = [("q", query), ("sort", sort), ("limit", str(max_results)), ("type", "link")]
    if name:
        params.append(("restrict_sr", "1"))
    path = f"/r/{client.quote(name)}/search" if name else "/search"
    got = CACHE.cached("search", (query, name and name.lower(), sort, max_results), lambda: _posts(_api(api).call("GET", path, params)))
    return {"query": query, "subreddit": name, "posts": got}


def user(name: str, api: client.Reddit | None = None) -> Json:
    who = client.user_name(name, "NAME")

    def fetch() -> Json:
        got = _api(api).call("GET", f"/user/{client.quote(who)}/about")
        data = got.get("data") if isinstance(got, Mapping) and isinstance(got.get("data"), Mapping) else {}
        return {k: data.get(k) for k in ("name", "link_karma", "comment_karma", "created_utc", "is_suspended") if k in data}

    return {"user": CACHE.cached("user", (who.lower(),), fetch)}


def subreddit(name: str, api: client.Reddit | None = None, fresh: bool = False) -> Json | None:
    """One subreddit's about page and its rules; None when Reddit has none
    by that name (or it is private or banned, which reads the same)."""
    who = client.subreddit_name(name, "NAME")

    def about() -> Any:
        try:
            got = _api(api).call("GET", f"/r/{client.quote(who)}/about")
        except RestError as error:
            if error.status in (403, 404):
                return None
            raise
        data = got.get("data") if isinstance(got, Mapping) and isinstance(got.get("data"), Mapping) else None
        if data is None:
            return None
        keys = ("display_name", "title", "public_description", "subscribers", "over18", "subreddit_type",
                "submission_type", "user_is_banned", "user_is_subscriber", "restrict_posting", "restrict_commenting")
        return {k: _clip(data.get(k)) for k in keys if k in data}

    def rules() -> Any:
        try:
            got = _api(api).call("GET", f"/r/{client.quote(who)}/about/rules")
        except RestError:
            return []
        items = got.get("rules") if isinstance(got, Mapping) else None
        return [
            {"name": r.get("short_name"), "applies_to": r.get("kind"), "description": _clip(r.get("description"))}
            for r in (items or [])
            if isinstance(r, Mapping)
        ]

    got = about() if fresh else CACHE.cached("subreddit", (who.lower(),), about)
    if got is None:
        return None
    return {**got, "rules": CACHE.cached("rules", (who.lower(),), rules)}


def inbox_items(max_results: int, api: client.Reddit | None = None) -> Iterator[Mapping[str, Any]]:
    """The account's inbox, newest first, uncached: comment replies, post
    replies, username mentions (`t1`) and private messages (`t4`)."""
    got = _api(api).call("GET", "/message/inbox", [("limit", str(max_results))])
    for kid in _children(got):
        if isinstance(kid.get("data"), Mapping):
            yield {"kind": kid.get("kind"), **kid["data"]}


#: What `things` keeps of each: enough to score a write and tell whether it
#: is still there to see.
_THING_KEYS = ("id", "name", "subreddit", "author", "score", "num_comments", "removed_by_category", "banned_by",
               "removed", "spam", "selftext", "body", "created_utc")


def things(fullnames: list[str], api: client.Reddit | None = None) -> list[Json]:
    """Several posts and comments by fullname in one uncached read (Reddit
    takes up to 100), each `{"kind": "t3"|"t1", ...}` trimmed to _THING_KEYS.
    Reddit leaves out what it no longer has."""
    if not fullnames:
        return []
    if len(fullnames) > 100:
        raise hand.HandError("at most 100 things a read")
    got = _api(api).call("GET", "/api/info", [("id", ",".join(fullnames))])
    return [
        {"kind": kid.get("kind"), **{k: kid["data"].get(k) for k in _THING_KEYS if k in kid["data"]}}
        for kid in _children(got)
        if isinstance(kid.get("data"), Mapping)
    ]
