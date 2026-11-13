"""What a beat needs to answer an X mention without contradicting itself
(bin/lib/tick.py, `render`): the post a mention answers, what Teeny last said
to that person, and what it said on X lately, replies included.

Everything is read from the hands' own ledgers in state/hands.db (x_posts,
x_mentions, x_cache), read-only, and never from X: a paid lookup per mention
would cost more than the reply. What is not there is left out. Before this, a
mention carried only bare ids, and "what you said lately" in the prompt held
posts but not replies, which are most of what Teeny says on X; replies on one
afternoon said "two days old", "day two" and "day three".
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from .. import hand

#: How far back "what you said on X lately" looks.
LATELY_SECS = 86400
#: The most lines it lists.
LATELY_MAX = 10
#: How much of any one post is shown.
CLIP = 220


def _clip(text: Any) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= CLIP else text[: CLIP - 3].rstrip() + "..."


def _ago(at: float) -> str:
    secs = max(0, int(hand.now() - at))
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{secs // 60} min ago"
    if secs < 172800:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} days ago"


def _connect() -> sqlite3.Connection | None:
    path = hand.state_dir() / "hands.db"
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    return conn


def _one(conn: sqlite3.Connection, sql: str, params: tuple) -> sqlite3.Row | None:
    try:
        return conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:  # that table was never written
        return None


def _rows(conn: sqlite3.Connection, sql: str, params: tuple) -> list[sqlite3.Row]:
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def _post(conn: sqlite3.Connection, post_id: str) -> tuple[str, str] | None:
    """(how to say whose it is, its text) for a post id the ledgers know."""
    own = _one(conn, "SELECT kind, text, created_at FROM x_posts WHERE id = ?", (post_id,))
    if own:
        whose = "your post" if own["kind"] != "reply" else "your reply"
        return f"{whose} ({_ago(own['created_at'])})", own["text"]
    seen = _one(conn, "SELECT author_username, text FROM x_mentions WHERE id = ?", (post_id,))
    if seen:
        return f"@{seen['author_username']}", seen["text"]
    cached = _one(conn, "SELECT value FROM x_cache WHERE key = ?", (f"post:{post_id}",))
    if cached:
        try:
            post = json.loads(cached["value"])
        except (TypeError, ValueError):
            return None
        if isinstance(post, dict) and post.get("text"):
            author = post.get("authorUsername")
            return (f"@{author}" if author else "a post"), post["text"]
    return None


def mention_context(mention_id: str, who: str | None, payload: Mapping[str, Any]) -> list[str]:
    """Lines under a mention in a beat: what it answers (its parent, or the
    thread's first post when the parent is unknown) and what Teeny last said
    to its author. [] when the ledgers know neither."""
    conn = _connect()
    if conn is None:
        return []
    lines: list[str] = []
    try:
        parent = str(payload.get("in_reply_to") or "")
        root = str(payload.get("conversation_id") or "")
        found = _post(conn, parent) if parent else None
        if found:
            lines.append(f"in reply to {found[0]}: {_clip(found[1])}")
        elif root and root != str(mention_id):
            found = _post(conn, root)
            if found:
                lines.append(f"in a thread started by {found[0]}: {_clip(found[1])}")
        if who:
            last = _one(conn, "SELECT p.text, p.created_at FROM x_posts p JOIN x_mentions m ON p.in_reply_to = m.id"
                              " WHERE p.kind = 'reply' AND lower(m.author_username) = lower(?)"
                              " ORDER BY p.created_at DESC LIMIT 1", (who,))
            if last:
                lines.append(f"you last said to @{who} ({_ago(last['created_at'])}): {_clip(last['text'])}")
    finally:
        conn.close()
    return lines


def said_lately() -> list[str]:
    """"What you said on X lately": Teeny's own posts and replies over the
    last day, newest first, each with when and to whom, so a reply knows what
    it has already said today. [] when there is nothing to list."""
    conn = _connect()
    if conn is None:
        return []
    try:
        rows = _rows(conn, "SELECT p.kind, p.text, p.created_at, m.author_username AS who FROM x_posts p"
                           " LEFT JOIN x_mentions m ON p.in_reply_to = m.id"
                           " WHERE p.created_at >= ? ORDER BY p.created_at DESC LIMIT ?",
                     (hand.now() - LATELY_SECS, LATELY_MAX))
    finally:
        conn.close()
    if not rows:
        return []
    lines = ["## what you said on X lately"]
    for r in rows:
        to = f"to @{r['who']}" if r["kind"] == "reply" and r["who"] else "a reply" if r["kind"] == "reply" else f"a {r['kind']}"
        lines.append(f"- {_ago(r['created_at'])}, {to}: {_clip(r['text'])}")
    return lines
