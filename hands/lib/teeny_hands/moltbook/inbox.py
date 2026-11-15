"""What people said back to Teeny on Moltbook, into Teeny's mind
(docs/hands.md, "moltbook inbox").

Moltbook has no mentions timeline, so `scan` walks the comment trees of the
posts Teeny is part of: its own recent posts (every comment on them is said
to Teeny) and the posts it commented on (only replies under Teeny's own
comments are). Each one by someone else is screened by the inbound guard and,
when allowed, added to the stimuli inbox once: `moltbook · comment`, ref the
comment id, with the post and parent ids `comment_moltbook` needs to answer,
its `depth` in the tree and whether it `can_answer` (Moltbook draws five levels).
A comment the guard held is kept in `moltbook_held`, as X keeps a held
mention, and left for the hold's review: the guard's re-screen returns it
to the inbox (`release_held`) once it finds it clean.

Reads only; it answers nobody. Re-running it is free: a comment already in
the inbox, or already held, is passed over before the guard sees it again
(2026-11-13: one held comment was screened and held anew on every run, a
dozen pending holds for one comment).

A comment on Teeny's own post is also owed an answer (owed.py): the scan
keeps its `moltbook_owed` row (waiting, held, answered when Teeny's answer is
under it in the thread, declined at the last drawn level or once gone), and
offers a waiting one the mind already read and left back to the mind after
its back-off (`again` counts them). Teeny's own posts are walked up to
`max_own` (30) of the last `days`, the posts it commented on up to `max_posts`.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .. import hand, stimuli
from ..rest import RestApi
from . import owed, reads, rules
from .actions import _author, _depths, _walk

Json = dict[str, Any]

#: Teeny's own posts a scan walks, newest first. Its comments on others'
#: posts are walked up to `max_posts`; its own posts are where it owes
#: answers, so more of them are read (eight posts a day is three days).
MAX_OWN = 30
#: A post's comments as one read returns them; a thread this full may hold
#: more, so its missing comments are not taken as gone.
PAGE = 100


def _screen(text: str, context: Mapping[str, Any]) -> Any:
    """The inbound guard's verdict. Imported by name, so a test can stand in
    for the other hand's gate."""
    from ..guard.gate import inbound

    return inbound(text, surface="moltbook", context=dict(context))


def _posts(conn, since: float, max_posts: int, max_own: int = MAX_OWN) -> dict[str, bool]:
    """post id -> whether it is Teeny's own, newest first."""
    counted = ",".join("?" * len(rules.COUNTED))
    own = conn.execute(
        f"SELECT post_id FROM moltbook_posts WHERE post_id IS NOT NULL AND at >= ? AND status IN ({counted})"
        " ORDER BY at DESC LIMIT ?",
        (since, *rules.COUNTED, max_own),
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


def _known(conn, inbox, cid: str) -> bool:
    """Whether a comment was settled by an earlier scan: in the inbox, or held."""
    if conn.execute("SELECT 1 FROM moltbook_held WHERE comment_id = ?", (cid,)).fetchone():
        return True
    return inbox.execute("SELECT 1 FROM stimuli WHERE id = ?", (f"moltbook:comment:{cid}",)).fetchone() is not None


def _held_only(conn, inbox, cid: str) -> bool:
    """Held, and not (yet) released into the inbox."""
    return (conn.execute("SELECT 1 FROM moltbook_held WHERE comment_id = ?", (cid,)).fetchone() is not None
            and inbox.execute("SELECT 1 FROM stimuli WHERE id = ?", (f"moltbook:comment:{cid}",)).fetchone() is None)


def _keep_held(conn, cid: str, post_id: str, hold_id: int | None, payload: Json) -> None:
    with hand.transaction(conn):
        conn.execute("INSERT OR IGNORE INTO moltbook_held (comment_id, post_id, hold_id, at, payload) VALUES (?, ?, ?, ?, ?)",
                     (cid, post_id, hold_id, hand.now(), json.dumps(payload, ensure_ascii=False)))


def scan(days: int = 7, max_posts: int = 10, api: RestApi | None = None, max_own: int = MAX_OWN) -> Json:
    conn = rules.db()
    known = stimuli.db()
    me = reads.me_name(api).lower()
    posts = _posts(conn, hand.now() - days * 86400, max_posts, max_own)
    added = held = seen = again = 0
    errors: list[str] = []
    for post_id, own in posts.items():
        try:
            nodes = reads.comments(post_id, "new", PAGE, api, fresh=True).get("comments")
            tree = list(_walk(nodes))
            depths = _depths(nodes)
        except hand.HandError as exc:
            errors.append(f"{post_id}: {exc}")
            continue
        mine = {str(n.get("id")) for n, _ in tree if (_author(n) or "").lower() == me}
        # comment id -> Teeny's first answer under it, with when it was made.
        answers: dict[str, tuple[str, float | None]] = {}
        for node, parent in tree:
            if parent is not None and str(node.get("id")) in mine and parent not in answers:
                answers[parent] = (str(node.get("id")), owed._epoch(node.get("created_at")))
        if own:
            # An answer Moltbook hides until its challenge is answered is not in
            # the thread, but the ledger has it (and the hand refuses a second).
            for row in conn.execute(
                f"SELECT parent_id, comment_id, at FROM moltbook_comments WHERE post_id = ? AND parent_id IS NOT NULL"
                f" AND status IN ({','.join('?' * len(rules.COUNTED))}) ORDER BY at",
                (post_id, *rules.COUNTED),
            ):
                answers.setdefault(str(row["parent_id"]), (row["comment_id"], float(row["at"])))
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
            cid = str(cid)
            depth = depths.get(cid, 1)
            # Moltbook draws five levels; an answer below the last is never
            # seen, so a comment at that level is read but cannot be answered.
            payload: Json = {"post_id": post_id, "on_own_post": own, "depth": depth, "can_answer": depth < rules.MAX_REPLY_DEPTH}
            if parent is not None:
                payload["parent_id"] = parent
                payload["answering_teeny"] = parent in mine
            if own:
                owed.track(conn, cid, post_id, parent, author or None, depth, owed._epoch(node.get("created_at")))
                if cid in answers:
                    owed.answered(conn, cid, *answers[cid])
            if _known(conn, known, cid):
                if own and _held_only(conn, known, cid):
                    owed.held(conn, cid)  # held before its owed row was kept
                elif own and owed.offer_again(conn, known, cid):
                    again += 1
                continue
            verdict = _screen(text, {"post_id": post_id, "comment_id": cid, "author": author})
            if verdict.kind != "allow":
                _keep_held(conn, cid, post_id, verdict.hold_id, payload)
                if own:
                    owed.held(conn, cid)
                held += 1
                continue
            if stimuli.add("moltbook", "comment", cid, text, who=author or None, payload=payload):
                added += 1
                if own:
                    owed.offered(conn, cid)
        if own and isinstance(nodes, list) and len(nodes) < PAGE:
            owed.gone(conn, post_id, {str(n.get("id")) for n, _ in tree})
    owed.expire(conn)
    out: Json = {"posts": len(posts), "seen": seen, "added": added, "held": held, "again": again}
    if errors:
        out["errors"] = errors
    return out


def release_held(context: Mapping[str, Any], text: str) -> bool:
    """A comment the guard held, into the inbox as `scan` would have added it,
    once the guard's re-screen found it clean: with the details the scan kept
    (its parent, depth and whether it can be answered), so an answer to a
    reply can be placed. False when the inbox already has it (an earlier
    release, or a hold of it from before scans kept held comments, cleared
    first). Later scans pass it over either way."""
    post_id = str(context["post_id"])
    cid = str(context["comment_id"])
    conn = rules.db()
    own = conn.execute(
        f"SELECT 1 FROM moltbook_posts WHERE post_id = ? AND status IN ({','.join('?' * len(rules.COUNTED))})",
        (post_id, *rules.COUNTED),
    ).fetchone()
    payload: Json = {**(owed.payload_of(conn, cid) or {}), "post_id": post_id, "on_own_post": own is not None}
    added = stimuli.add("moltbook", "comment", cid, text, who=context.get("author") or None, payload=payload)
    if added:
        owed.released(conn, cid)
    return added
