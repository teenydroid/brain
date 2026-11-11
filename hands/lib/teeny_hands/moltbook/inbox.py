"""What people said back to Teeny on Moltbook, into Teeny's mind
(docs/hands.md, "moltbook inbox").

Moltbook has no mentions timeline, so `scan` walks the comment trees of the
posts Teeny is part of: its own recent posts (every comment on them is said
to Teeny) and the posts it commented on (only replies under Teeny's own
comments are). Each one by someone else is screened by the inbound guard and,
when allowed, added to the stimuli inbox once: `moltbook · comment`, ref the
comment id, with the post and parent ids `comment_moltbook` needs to answer.

Reads only; it answers nobody. Re-running it is free: the inbox ignores a
comment it already has.
"""

from __future__ import annotations

from typing import Any, Mapping

from .. import hand, stimuli
from ..rest import RestApi
from . import reads, rules
from .actions import _author, _walk

Json = dict[str, Any]


def _screen(text: str, context: Mapping[str, Any]) -> Any:
    """The inbound guard's verdict. Imported by name, so a test can stand in
    for the other hand's gate."""
    from ..guard.gate import inbound

    return inbound(text, surface="moltbook", context=dict(context))


def _posts(conn, since: float, max_posts: int) -> dict[str, bool]:
    """post id -> whether it is Teeny's own, newest first."""
    counted = ",".join("?" * len(rules.COUNTED))
    own = conn.execute(
        f"SELECT post_id FROM moltbook_posts WHERE post_id IS NOT NULL AND at >= ? AND status IN ({counted})"
        " ORDER BY at DESC LIMIT ?",
        (since, *rules.COUNTED, max_posts),
    ).fetchall()
    joined = conn.execute(
        f"SELECT post_id, MAX(at) AS at FROM moltbook_comments WHERE at >= ? AND status IN ({counted})"
        " GROUP BY post_id ORDER BY at DESC LIMIT ?",
        (since, *rules.COUNTED, max_posts),
    ).fetchall()
    out: dict[str, bool] = {str(r["post_id"]): True for r in own}
    for row in joined:
        out.setdefault(str(row["post_id"]), False)
    return out


def scan(days: int = 7, max_posts: int = 10, api: RestApi | None = None) -> Json:
    conn = rules.db()
    me = reads.me_name(api).lower()
    posts = _posts(conn, hand.now() - days * 86400, max_posts)
    added = held = seen = 0
    errors: list[str] = []
    for post_id, own in posts.items():
        try:
            tree = list(_walk(reads.comments(post_id, "new", 100, api, fresh=True).get("comments")))
        except hand.HandError as exc:
            errors.append(f"{post_id}: {exc}")
            continue
        mine = {str(n.get("id")) for n, _ in tree if (_author(n) or "").lower() == me}
        for node, parent in tree:
            author = _author(node) or ""
            cid = node.get("id")
            text = node.get("content")
            if cid is None or not isinstance(text, str) or not text.strip() or author.lower() == me:
                continue
            said_to_teeny = (own and parent is None) or (parent is not None and parent in mine)
            if not said_to_teeny:
                continue
            seen += 1
            verdict = _screen(text, {"post_id": post_id, "comment_id": str(cid), "author": author})
            if verdict.kind != "allow":
                held += 1
                continue
            payload = {"post_id": post_id, "on_own_post": own}
            if parent is not None:
                payload["parent_id"] = parent
                payload["answering_teeny"] = parent in mine
            if stimuli.add("moltbook", "comment", str(cid), text, who=author or None, payload=payload):
                added += 1
    out: Json = {"posts": len(posts), "seen": seen, "added": added, "held": held}
    if errors:
        out["errors"] = errors
    return out


def release_held(context: Mapping[str, Any], text: str) -> bool:
    """A comment the guard held, into the inbox as `scan` would have added it,
    once the guard's re-screen found it clean. False when the inbox already
    has it (a later scan let it through, or a second hold of it cleared)."""
    post_id = str(context["post_id"])
    own = rules.db().execute(
        f"SELECT 1 FROM moltbook_posts WHERE post_id = ? AND status IN ({','.join('?' * len(rules.COUNTED))})",
        (post_id, *rules.COUNTED),
    ).fetchone()
    payload: Json = {"post_id": post_id, "on_own_post": own is not None}
    return stimuli.add("moltbook", "comment", str(context["comment_id"]), text, who=context.get("author") or None,
                       payload=payload)
