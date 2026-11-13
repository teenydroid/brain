"""The read verbs' answers, each through the read cache. Safe to give a
drafting agent: nothing here writes anywhere but the cache.

HN's text fields are HTML (entities, <p> between paragraphs, <a> links,
<i>, <pre><code>); every one reaches a reader as plain text (`plain`)."""

from __future__ import annotations

import html.parser
from typing import Any, Callable, Mapping

from .. import hand
from ..readcache import ReadCache
from ..rest import RestApi
from . import client

MINUTE = 60.0
HOUR = 3600.0

#: No key scopes it; the API base does (the tests' fake is not HN).
CACHE = ReadCache(
    "hn_cache",
    "HN_API_BASE",
    {"feed": 5 * MINUTE, "item": 2 * MINUTE, "user": HOUR, "search": 10 * MINUTE},
    "HN_CACHE_ENABLED",
)

#: The story lists, by verb: `top` is /topstories.json.
FEEDS = ("top", "new", "best", "ask", "show")
SEARCH_TAGS = ("story", "comment")
#: Where an item is read by a person.
ITEM_PAGE = "https://news.ycombinator.com/item?id="
_TEXT_CHARS = 4000

Json = dict[str, Any]


class _Text(html.parser.HTMLParser):
    """HN's HTML as text: a paragraph is a blank line, a link is its
    address (or its words and then the address, when they differ), every
    other tag is dropped and every entity decoded."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._href: str | None = None
        self._label: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "p":
            self.parts.append("\n\n")
        elif tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._label = []

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        label, href = "".join(self._label).strip(), self._href
        # HN shortens a long address to its start and "...".
        if not href or label == href or (label.endswith("...") and href.startswith(label[:-3])):
            self.parts.append(href or label)
        else:
            self.parts.append(f"{label} ({href})")
        self._href = None

    def handle_data(self, data: str) -> None:
        (self._label if self._href is not None else self.parts).append(data)


def plain(value: Any) -> str:
    """HN's HTML as plain text; "" for anything that is not a string."""
    if not isinstance(value, str):
        return ""
    parser = _Text()
    parser.feed(value)
    parser.close()
    if parser._href is not None:  # an <a> never closed
        parser.handle_endtag("a")
    return "".join(parser.parts).strip()


def _clip(text: str) -> str:
    return text if len(text) <= _TEXT_CHARS else text[:_TEXT_CHARS] + "…"


def _get(api: RestApi | None, path: str) -> Any:
    return (api or client.api()).call("GET", path, auth=False)


def raw_item(item_id: int, api: RestApi | None = None, fresh: bool = False) -> Any:
    """One item as HN sends it (None when there is none), through the cache
    unless `fresh`."""
    fetch = lambda: _get(api, f"/item/{item_id}.json")  # noqa: E731
    return fetch() if fresh else CACHE.cached("item", (item_id,), fetch)


def alive(item: Any) -> bool:
    """An item to read: there, and neither deleted nor dead (flagged or
    killed by HN's moderators)."""
    return isinstance(item, Mapping) and not item.get("deleted") and not item.get("dead")


def shape(item: Mapping[str, Any]) -> Json:
    """An item for a reader: its text as plain text, and the page it is on."""
    out: Json = {}
    for key in ("id", "type", "by", "time", "title", "url", "score", "descendants", "parent", "kids"):
        if item.get(key) not in (None, ""):
            out[key] = item[key]
    if item.get("text"):
        out["text"] = _clip(plain(item["text"]))
    if isinstance(item.get("title"), str):
        out["title"] = plain(item["title"])
    out["link"] = f"{ITEM_PAGE}{item.get('id')}"
    return out


def item(value: Any, api: RestApi | None = None) -> Json:
    item_id = client.item_id(value)
    got = raw_item(item_id, api)
    if not alive(got):
        raise hand.HandError(f"no Hacker News item {item_id} (missing, deleted or dead)")
    return {"item": shape(got)}


_STORY_KEYS = ("id", "title", "url", "by", "score", "descendants", "time")


def stories(feed: str, max_results: int, api: RestApi | None = None) -> Json:
    """A feed's first stories, hydrated. At most twice `max_results` items
    are read, so a run of dead ones cannot turn one read into hundreds."""
    if feed not in FEEDS:
        raise hand.HandError(f"a feed is one of {', '.join(FEEDS)}")
    ids = CACHE.cached("feed", (feed,), lambda: _get(api, f"/{feed}stories.json"))
    out: list[Json] = []
    for item_id in (ids if isinstance(ids, list) else [])[: 2 * max_results]:
        if len(out) >= max_results:
            break
        got = raw_item(client.item_id(item_id), api)
        if alive(got):
            shaped = shape(got)
            out.append({k: shaped[k] for k in _STORY_KEYS if k in shaped})
    return {"feed": feed, "stories": out}


class Budget:
    """How many items one call may still read: a thread is one request per
    comment, so every walk is bounded by it."""

    def __init__(self, left: int) -> None:
        self.left = left


def walk(root: Mapping[str, Any], max_depth: int, budget: Budget,
         fetch: Callable[[int], Any]) -> tuple[list[tuple[Json, int]], bool]:
    """The comments under `root` in the order HN's page shows them, each
    with its depth (a reply to `root` is 1), down to `max_depth`, reading at
    most `budget` items. A dead or deleted comment is not listed; the replies
    under it are judged on their own. True as well when the budget ran out
    with comments still unread."""
    out: list[tuple[Json, int]] = []
    stack = [(kid, 1) for kid in reversed(root.get("kids") or [])]
    while stack:
        if budget.left <= 0:
            return out, True
        kid, depth = stack.pop()
        budget.left -= 1
        got = fetch(client.item_id(kid))
        if not isinstance(got, Mapping):
            continue
        if alive(got):
            out.append((dict(got), depth))
        if depth < max_depth:
            stack.extend((k, depth + 1) for k in reversed(got.get("kids") or []))
    return out, False


def thread(value: Any, depth: int = 3, max_items: int = 30, api: RestApi | None = None) -> Json:
    """An item and the comments under it, as `walk` reads them."""
    root = item(value, api)["item"]
    found, truncated = walk(root, depth, Budget(max_items), lambda i: raw_item(i, api))
    comments = []
    for got, level in found:
        entry: Json = {k: got[k] for k in ("id", "by", "time", "parent") if k in got}
        entry["depth"] = level
        entry["text"] = _clip(plain(got.get("text")))
        comments.append(entry)
    return {"item": root, "comments": comments, "truncated": truncated}


#: Submitted ids `user` lists, newest first.
USER_RECENT = 10


def raw_user(name: str, api: RestApi | None = None) -> Any:
    who = client.user_name(name)
    return CACHE.cached("user", (who,), lambda: _get(api, f"/user/{who}.json"))


def user(name: str, api: RestApi | None = None) -> Json:
    got = raw_user(name, api)
    if not isinstance(got, Mapping):
        raise hand.HandError(f"no Hacker News user {name}")
    submitted = got.get("submitted") if isinstance(got.get("submitted"), list) else []
    out: Json = {k: got[k] for k in ("id", "created", "karma") if k in got}
    out["submitted"] = len(submitted)
    out["recent"] = sorted(submitted, reverse=True)[:USER_RECENT]
    if got.get("about"):
        out["about"] = _clip(plain(got["about"]))
    return {"user": out}


def _hit(hit: Mapping[str, Any]) -> Json:
    tags = hit.get("_tags") if isinstance(hit.get("_tags"), list) else []
    out: Json = {"id": int(hit["objectID"]) if str(hit.get("objectID", "")).isdigit() else hit.get("objectID")}
    kind = next((t for t in tags if t in ("story", "comment", "poll", "job")), None)
    if kind:
        out["type"] = kind
    for key, name in (("title", "title"), ("url", "url"), ("author", "by"), ("points", "points"),
                      ("num_comments", "num_comments"), ("created_at_i", "time")):
        if hit.get(key) not in (None, ""):
            out[name] = hit[key]
    text = plain(hit.get("story_text") or hit.get("comment_text"))
    if text:
        out["text"] = _clip(text)
    for key in ("story_id", "parent_id", "story_title"):
        if hit.get(key) not in (None, ""):
            out[key] = hit[key]
    out["link"] = f"{ITEM_PAGE}{out['id']}"
    return out


def search(query: str, tags: str | None, max_results: int, api: RestApi | None = None) -> Json:
    """Algolia's search over HN, by relevance: stories, comments, or both."""
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise hand.HandError("a search is 1 to 500 characters")
    if tags is not None and tags not in SEARCH_TAGS:
        raise hand.HandError(f"--tags is one of {', '.join(SEARCH_TAGS)}")
    params = [("query", query.strip()), ("hitsPerPage", str(max_results))]
    if tags:
        params.append(("tags", tags))
    got = CACHE.cached("search", (query.strip(), tags, max_results),
                       lambda: (api or client.search_api()).call("GET", "/search", params, auth=False))
    hits = got.get("hits") if isinstance(got, Mapping) and isinstance(got.get("hits"), list) else []
    return {"query": query.strip(), "results": [_hit(h) for h in hits if isinstance(h, Mapping) and h.get("objectID")]}
