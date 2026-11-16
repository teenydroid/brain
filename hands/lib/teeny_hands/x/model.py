"""What the hand returns: plain JSON values, compact, absent fields omitted.

X v2 sends snake_case with everything optional; the step from that to these
shapes is here (crates/teeny-x/src/wire.rs). The shapes are the legacy
`XPost`'s (crates/teeny-x/src/model.rs: camelCase), so rows the old cache
wrote read back unchanged and a model sees what it always saw.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

Json = dict[str, Any]


@dataclass(frozen=True)
class SearchOptions:
    """The knobs on a recent search. Times are ISO 8601, within the last 7
    days. `max_results` is clamped to X's 10..=100 when sent: asking for fewer
    still bills for 10."""

    max_results: int | None = None
    since_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    #: `recency` or `relevancy`.
    sort_order: str | None = None
    #: A `nextToken` from an earlier page.
    pagination_token: str | None = None


def _put(target: Json, key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _metrics(post_id: str, raw: Mapping[str, Any]) -> Json:
    """X's public counters. X has called a repost both things; either spelling
    is read. Impressions are the author's to see, so on somebody else's post
    they are absent."""
    reposts = raw.get("repost_count", raw.get("retweet_count"))
    metrics: Json = {
        "id": post_id,
        "likes": _int(raw.get("like_count")) or 0,
        "replies": _int(raw.get("reply_count")) or 0,
        "reposts": _int(reposts) or 0,
        "quotes": _int(raw.get("quote_count")) or 0,
    }
    _put(metrics, "impressions", _int(raw.get("impression_count")))
    _put(metrics, "bookmarks", _int(raw.get("bookmark_count")))
    return metrics


def to_posts(data: Any, includes: Mapping[str, Any] | None) -> list[Json]:
    """Posts X returned, with authors and attachments joined in from
    `includes`. A post without an id or text is dropped."""
    includes = includes if isinstance(includes, Mapping) else {}
    usernames = {
        user.get("id"): user.get("username")
        for user in includes.get("users") or []
        if isinstance(user, Mapping) and user.get("id") and user.get("username")
    }
    attachments = {
        item.get("media_key"): item
        for item in includes.get("media") or []
        if isinstance(item, Mapping) and item.get("media_key")
    }
    posts: list[Json] = []
    for raw in data if isinstance(data, list) else []:
        if not isinstance(raw, Mapping):
            continue
        post_id, text = raw.get("id"), raw.get("text")
        if not isinstance(post_id, str) or not isinstance(text, str) or not post_id or not text:
            continue
        post: Json = {"id": post_id, "text": text}
        author = raw.get("author_id")
        _put(post, "authorId", author)
        _put(post, "authorUsername", usernames.get(author))
        _put(post, "conversationId", raw.get("conversation_id"))
        _put(post, "createdAt", raw.get("created_at"))
        _put(post, "inReplyToUserId", raw.get("in_reply_to_user_id"))
        media = []
        for key in (raw.get("attachments") or {}).get("media_keys") or []:
            item = attachments.get(key)
            if not item or not item.get("type"):
                continue
            attached: Json = {"type": item["type"]}
            _put(attached, "url", item.get("url"))
            _put(attached, "previewUrl", item.get("preview_image_url"))
            _put(attached, "altText", item.get("alt_text"))
            media.append(attached)
        if media:
            post["media"] = media
        if isinstance(raw.get("public_metrics"), Mapping):
            post["metrics"] = _metrics(post_id, raw["public_metrics"])
        posts.append(post)
    return posts


def to_user(raw: Any, handle: str) -> Json | None:
    """An account looked up by its handle; None when X has no such account
    (suspended, deactivated, never existed)."""
    if not isinstance(raw, Mapping) or not raw.get("id"):
        return None
    metrics = raw.get("public_metrics") if isinstance(raw.get("public_metrics"), Mapping) else {}
    user: Json = {"id": raw["id"], "username": raw.get("username") or handle}
    _put(user, "name", raw.get("name"))
    _put(user, "description", raw.get("description"))
    _put(user, "createdAt", raw.get("created_at"))
    _put(user, "verified", raw.get("verified") if isinstance(raw.get("verified"), bool) else None)
    _put(user, "followers", _int(metrics.get("followers_count")))
    _put(user, "following", _int(metrics.get("following_count")))
    _put(user, "posts", _int(metrics.get("tweet_count")))
    return user


def to_looked_up_user(raw: Any) -> Json | None:
    """An account from a lookup by ids (`GET /2/users`): its age, followers,
    and whether it follows the account the lookup signed as
    (`connection_status` holds `followed_by`)."""
    if not isinstance(raw, Mapping) or not raw.get("id"):
        return None
    metrics = raw.get("public_metrics") if isinstance(raw.get("public_metrics"), Mapping) else {}
    status = raw.get("connection_status") if isinstance(raw.get("connection_status"), list) else []
    user: Json = {"id": str(raw["id"])}
    _put(user, "username", raw.get("username"))
    _put(user, "createdAt", raw.get("created_at"))
    _put(user, "followers", _int(metrics.get("followers_count")))
    user["followsMe"] = "followed_by" in status
    return user


def to_identity(raw: Any, fallback_username: str) -> Json | None:
    """The account the credentials act as, read from the token."""
    if not isinstance(raw, Mapping) or not raw.get("id"):
        return None
    metrics = raw.get("public_metrics") if isinstance(raw.get("public_metrics"), Mapping) else {}
    identity: Json = {"id": raw["id"], "username": raw.get("username") or fallback_username}
    _put(identity, "followers", _int(metrics.get("followers_count")))
    return identity


def utf16_length(text: str) -> int:
    """A post's length as the legacy service measured it: UTF-16 code units
    (JavaScript's `.length`), so an emoji counts two, as X counts it."""
    return len(text.encode("utf-16-le")) // 2
