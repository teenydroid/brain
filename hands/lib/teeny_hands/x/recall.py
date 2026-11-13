"""What a beat needs to answer an X mention without contradicting itself
(bin/lib/tick.py, `render`): the post a mention answers, that person's
history with Teeny (their earlier words and the conversation so far), and
what it said on X lately, replies included.

Everything is read from the hands' own ledgers in state/hands.db (x_posts,
x_mentions, x_cache), read-only, and never from X: a paid lookup per mention
would cost more than the reply. What is not there is left out. Before this, a
mention carried only bare ids, and "what you said lately" in the prompt held
posts but not replies, which are most of what Teeny says on X; replies on one
afternoon said "two days old", "day two" and "day three".
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Any, Collection, Mapping

from .. import hand

#: How far back "what you said on X lately" looks.
LATELY_SECS = 86400
#: The most lines it lists.
LATELY_MAX = 10
#: How much of any one post is shown.
CLIP = 220
#: A person's history in a beat: the most of their messages Teeny let go,
#: and the most exchanges (their message, Teeny's reply) it shows.
LET_GO = 3
EXCHANGES = 4


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


def answering(mention_id: str, who: str | None, payload: Mapping[str, Any]) -> str | None:
    """The line saying what a mention answers: its parent, or the thread's
    first post when the parent is unknown. None when the ledgers know neither."""
    conn = _connect()
    if conn is None:
        return None
    them = f"@{who}" if who else "they"
    try:
        parent = str(payload.get("in_reply_to") or "")
        root = str(payload.get("conversation_id") or "")
        found = _post(conn, parent) if parent else None
        if found:
            return f'{them} is answering {found[0]}: "{_clip(found[1])}"'
        if root and root != str(mention_id):
            found = _post(conn, root)
            if found:
                return f'{them} is writing in a thread started by {found[0]}: "{_clip(found[1])}"'
    finally:
        conn.close()
    return None


def _at(row: Mapping[str, Any]) -> float:
    """When a mention was written: X's created_at, else when it was seen."""
    try:
        return dt.datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return float(row["seen_at"])


def _times(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def history(who: str, exclude: Collection[str] = ()) -> list[str]:
    """One person's history with Teeny on X, for the turn addressed to them:
    how long they have been writing, the last few of their messages Teeny let
    go, and the last few exchanges (their words, Teeny's reply), oldest
    first. The mentions in `exclude` (the ones being answered now) and what
    the guard held or has not screened are not history. Bounded however
    loud the account. [] when the ledgers cannot be read."""
    conn = _connect()
    if conn is None:
        return []
    try:
        if _one(conn, "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'x_mentions'", ()) is None:
            return []
        rows = _rows(conn, "SELECT id, text, created_at, seen_at FROM x_mentions WHERE lower(author_username) = lower(?)"
                           " AND status NOT IN ('held', 'unscreened', 'pending')", (who,))
        replies = {r["in_reply_to"]: r for r in _rows(
            conn, "SELECT p.text, p.created_at, p.in_reply_to FROM x_posts p JOIN x_mentions m ON p.in_reply_to = m.id"
                  " WHERE p.kind = 'reply' AND lower(m.author_username) = lower(?) ORDER BY p.created_at", (who,))}
    finally:
        conn.close()
    skip = set(exclude)
    earlier = sorted((r for r in rows if r["id"] not in skip), key=_at)
    head = f"@{who} is someone on X."
    if not earlier:
        return [f"{head} You have never talked with @{who} before."]
    days = {dt.datetime.fromtimestamp(_at(r), dt.timezone.utc).date() for r in earlier}
    answered = [r for r in earlier if r["id"] in replies]
    let_go = [r for r in earlier if r["id"] not in replies][-LET_GO:]
    lines = [f"{head} They have written to you {_times(len(earlier), 'time')} over {_times(len(days), 'day')}"
             f" (first {_ago(_at(earlier[0]))}, last {_ago(_at(earlier[-1]))}); you answered {len(answered)}."]
    if let_go:
        lines.append(f"Earlier messages from @{who} that you let go (oldest first, last {LET_GO}):")
        lines += [f'- "{_clip(r["text"])}" ({_ago(_at(r))})' for r in let_go]
    if answered:
        lines.append(f"Your conversation with @{who} so far (oldest first, last {EXCHANGES} exchanges):")
        for r in answered[-EXCHANGES:]:
            lines += [f'- @{who} ({_ago(_at(r))}): "{_clip(r["text"])}"', f'  you: "{_clip(replies[r["id"]]["text"])}"']
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
