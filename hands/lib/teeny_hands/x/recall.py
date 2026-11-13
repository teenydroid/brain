"""What a beat needs to answer an X mention without contradicting itself
(bin/lib/tick.py, `render`): the post a mention answers, what Teeny last said
to that person, what it said on X lately, replies included, and the words it
has worn out in its replies.

Everything is read from the hands' own ledgers in state/hands.db (x_posts,
x_mentions, x_cache), read-only, and never from X: a paid lookup per mention
would cost more than the reply. What is not there is left out. Before this, a
mention carried only bare ids, and "what you said lately" in the prompt held
posts but not replies, which are most of what Teeny says on X; replies on one
afternoon said "two days old", "day two" and "day three".
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from typing import Any, Mapping

from .. import hand

#: How far back "what you said on X lately" looks.
LATELY_SECS = 86400
#: The most lines it lists.
LATELY_MAX = 10
#: How much of any one post is shown.
CLIP = 220
#: How many of Teeny's latest replies `worn_phrases` reads.
WORN_REPLIES = 30
#: A word or a two-word phrase in this many of them is worn.
WORN_MIN = 3
#: The most it lists.
WORN_MAX = 8

#: Handles, cashtags and links: never words of Teeny's own.
_NOT_WORDS = re.compile(r"https?://\S+|[@$#]\w+")
_WORD = re.compile(r"[a-z][a-z']*[a-z]")
#: Words every reply needs. A phrase made only of these is not a habit.
_COMMON = frozenset("""
about after again all also and any are aren't because been before being but can can't could did didn't does doesn't
doing don't down each even ever every for from get gets got had has have haven't her here him his how i'm i've into
isn't it's its just like more most much not now off one only other our out over own same she should some still such
than that that's the their them then there these they this those though through too under until very was wasn't
were what when where which while who why will with won't would yet you you're your yours
""".split())


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


def _words(text: str) -> list[str]:
    return _WORD.findall(_NOT_WORDS.sub(" ", str(text or "").lower()))


def worn_phrases() -> list[str]:
    """The words and two-word phrases Teeny keeps reaching for: in at least
    WORN_MIN of its last WORN_REPLIES replies, the most used first, at most
    WORN_MAX. A reply counts once however often it says the word. A word that
    is worn only as part of a worn phrase is listed as the phrase.

    hands/x's repeat rule compares whole posts and skips replies, so nothing
    else notices a reply that is new as a sentence and old in every word."""
    conn = _connect()
    if conn is None:
        return []
    try:
        rows = _rows(conn, "SELECT text FROM x_posts WHERE kind = 'reply' ORDER BY created_at DESC LIMIT ?",
                     (WORN_REPLIES,))
    finally:
        conn.close()
    singles: Counter[str] = Counter()
    pairs: Counter[tuple[str, str]] = Counter()
    for r in rows:
        words = _words(r["text"])
        singles.update({w for w in words if len(w) >= 3 and w not in _COMMON})
        pairs.update({(a, b) for a, b in zip(words, words[1:]) if a not in _COMMON and b not in _COMMON})
    worn = {" ".join(pair): n for pair, n in pairs.items() if n >= WORN_MIN}
    for word, n in singles.items():
        if n < WORN_MIN:
            continue
        inside = max((m for pair, m in pairs.items() if m >= WORN_MIN and word in pair), default=0)
        if inside * 4 < n * 3:  # mostly said outside the phrase: the word itself is the habit
            worn[word] = n
    return [phrase for phrase, _ in sorted(worn.items(), key=lambda kv: (-kv[1], kv[0]))[:WORN_MAX]]
