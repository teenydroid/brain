"""What people said to Teeny on Reddit, into Teeny's mind (docs/hands.md,
"reddit inbox").

Reddit keeps an inbox for the account: replies to its posts and comments,
and mentions of u/<Teeny> anywhere. `scan` reads the newest of it and adds
each comment by someone else from the last N days to the stimuli inbox
once: `reddit · comment`, ref the comment's fullname (`t1_…`), with the
post and comment ids `comment_reddit` needs to answer and whether the
subreddit is one Teeny may write in at all (`can_answer`). Each is screened
by the inbound guard first, and a held one is not added.

Private messages are counted and left alone: hands/reddit sends none, so
the mind is not shown something it cannot answer. Nothing is marked read:
that would be a write, and the inbox's own (surface, kind, ref) uniqueness
already makes a re-scan free.
"""

from __future__ import annotations

from typing import Any, Mapping

from .. import hand, stimuli
from . import client, reads, rules

Json = dict[str, Any]
_POST_IN_PERMALINK = re.compile(r"/comments/([a-z0-9]{1,13})/")


def _screen(text: str, context: Mapping[str, Any]) -> Any:
    """The inbound guard's verdict. Imported by name, so a test can stand in
    for the other hand's gate."""
    from ..guard.gate import inbound

    return inbound(text, surface="reddit", context=dict(context))


def _payload(item: Mapping[str, Any]) -> Json:
    sub = str(item.get("subreddit") or "")
    # A comment in the inbox names its post in `link_id` when Reddit sends
    # one, and always in its `context` permalink (/r/x/comments/<post>/...).
    link = str(item.get("link_id") or "")
    found = _POST_IN_PERMALINK.search(str(item.get("context") or ""))
    post_id = link[3:] if link.startswith("t3_") else found.group(1) if found else None
    return {
        "post_id": post_id,
        "comment_id": str(item.get("id")),
        "subreddit": sub,
        "type": item.get("type"),
        "post_title": item.get("link_title"),
        "can_answer": bool(sub) and sub.lower() in rules.allowed_subreddits(),
    }


def scan(days: int = 7, max_items: int = 50, api: client.Reddit | None = None) -> Json:
    me = client.username().lower()
    since = hand.now() - days * 86400
    added = held = seen = messages = 0
    for item in reads.inbox_items(max_items, api):
        if item.get("kind") == "t4":
            messages += 1
            continue
        if item.get("kind") != "t1":
            continue
        author = str(item.get("author") or "")
        text = item.get("body")
        created = item.get("created_utc")
        if (
            not item.get("id")
            or not isinstance(text, str)
            or not text.strip()
            or author.lower() in (me, "[deleted]", "automoderator")
            or (isinstance(created, (int, float)) and created < since)
        ):
            continue
        seen += 1
        payload = _payload(item)
        context = {"post_id": payload["post_id"], "comment_id": payload["comment_id"], "author": author,
                   "subreddit": payload["subreddit"]}
        verdict = _screen(text, context)
        if verdict.kind != "allow":
            held += 1
            continue
        if stimuli.add("reddit", "comment", f"t1_{payload['comment_id']}", text, who=author or None, payload=payload):
            added += 1
    return {"seen": seen, "added": added, "held": held, "messages_left": messages}


def release_held(context: Mapping[str, Any], text: str) -> bool:
    """A comment the guard held, into the inbox as `scan` would have added it,
    once the guard's re-screen found it clean. False when the inbox already
    has it."""
    sub = str(context.get("subreddit") or "")
    payload: Json = {
        "post_id": context.get("post_id"),
        "comment_id": str(context["comment_id"]),
        "subreddit": sub,
        "can_answer": bool(sub) and sub.lower() in rules.allowed_subreddits(),
    }
    return stimuli.add("reddit", "comment", f"t1_{context['comment_id']}", text, who=context.get("author") or None,
                       payload=payload)
