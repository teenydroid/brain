"""How Teeny's own Moltbook posts and comments landed (docs/hands.md,
"moltbook metrics"): `moltbook_post_metrics`, one row per checkpoint.

`refresh` measures each of Teeny's own writes about CHECKPOINTS hours after
it went out (1h, 24h, 72h): score, comment count (a comment's: its
replies) and Moltbook's verification status, through the read verbs and
their cache. A checkpoint on record is never fetched again. A run that
comes late records one row for the latest checkpoint due and skips the
ones it missed, so a write measured once at 30h has a 24h row and no 1h
row; the row's `at` says when it was really read.

Reads only, so it records under DRY_RUN too, as `inbox` does: nothing it
does reaches Moltbook but GETs. It reads at most MAX_FETCHES writes a run,
newest first, and stops at the first 429.
"""

from __future__ import annotations

from typing import Any, Mapping

from .. import hand
from ..rest import RestApi, RestError
from . import reads, rules
from .actions import _walk

HOUR = 3600.0
DAY = 86400.0
#: Hours after a write it is measured.
CHECKPOINTS = (1, 24, 72)
#: How far back a write is still measured.
WINDOW_DAYS = 14
#: Writes read per run, at most.
MAX_FETCHES = 20
#: A comment is looked for in its post's latest comments, this many.
COMMENTS_READ = 100

Json = dict[str, Any]


def _due(age: float) -> int | None:
    """The latest checkpoint `age` seconds have reached."""
    reached = [c for c in CHECKPOINTS if age >= c * HOUR]
    return reached[-1] if reached else None


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _score(node: Mapping[str, Any]) -> int | None:
    score = _int(node.get("score"))
    if score is None:
        up, down = _int(node.get("upvotes")), _int(node.get("downvotes"))
        if up is not None:
            score = up - (down or 0)
    return score


def _targets(conn) -> list[Json]:
    """Teeny's own accepted writes in the window, newest first."""
    since = hand.now() - WINDOW_DAYS * DAY
    counted = ",".join("?" * len(rules.COUNTED))
    rows = conn.execute(
        f"SELECT 'post' AS kind, post_id AS target_id, post_id, at FROM moltbook_posts"
        f" WHERE post_id IS NOT NULL AND at >= ? AND status IN ({counted})"
        " UNION ALL"
        f" SELECT 'comment', comment_id, post_id, at FROM moltbook_comments"
        f" WHERE comment_id IS NOT NULL AND at >= ? AND status IN ({counted})"
        " ORDER BY at DESC",
        (since, *rules.COUNTED, since, *rules.COUNTED),
    ).fetchall()
    return [dict(r) for r in rows]


def _recorded(conn) -> dict[tuple[str, str], int]:
    rows = conn.execute("SELECT kind, target_id, max(checkpoint) FROM moltbook_post_metrics GROUP BY kind, target_id")
    return {(r[0], r[1]): int(r[2]) for r in rows}


def _measure(target: Mapping[str, Any], api: RestApi | None) -> Json | None:
    """The target's numbers as Moltbook shows them now; None when it is not
    there to read (a comment beyond the latest COMMENTS_READ of a thread)."""
    if target["kind"] == "post":
        got = reads.post(str(target["target_id"]), api).get("post")
        if not isinstance(got, Mapping):
            return None
        return {"score": _score(got), "comment_count": _int(got.get("comment_count")),
                "verification_status": got.get("verification_status")}
    tree = reads.comments(str(target["post_id"]), "new", COMMENTS_READ, api).get("comments")
    node = next((n for n, _ in _walk(tree) if str(n.get("id")) == str(target["target_id"])), None)
    if node is None:
        return None
    replies = node.get("replies")
    count = len(replies) if isinstance(replies, list) else _int(node.get("reply_count"))
    return {"score": _score(node), "comment_count": count, "verification_status": node.get("verification_status")}


_COLUMNS = ("checkpoint", "at", "score", "comment_count", "verification_status")


def latest(conn, kind: str, target_id: str) -> Json | None:
    """The newest metrics row of one write, or None."""
    row = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM moltbook_post_metrics WHERE kind = ? AND target_id = ?"
        " ORDER BY checkpoint DESC LIMIT 1",
        (kind, target_id),
    ).fetchone()
    return dict(row) if row else None


def listing(days: int = 7) -> Json:
    """The latest row of each write measured in the last `days`, newest
    write first: a post with its title, a comment with its post's id."""
    conn = rules.db()
    try:
        rows = conn.execute(
            "SELECT m.kind, m.target_id, m.checkpoint, m.at, m.score, m.comment_count, m.verification_status,"
            "       p.title, c.post_id, coalesce(p.at, c.at) AS written_at"
            " FROM moltbook_post_metrics m"
            " JOIN (SELECT kind, target_id, max(checkpoint) AS checkpoint FROM moltbook_post_metrics"
            "       GROUP BY kind, target_id) newest USING (kind, target_id, checkpoint)"
            " LEFT JOIN moltbook_posts p ON m.kind = 'post' AND p.post_id = m.target_id"
            " LEFT JOIN moltbook_comments c ON m.kind = 'comment' AND c.comment_id = m.target_id"
            " WHERE m.at >= ? ORDER BY written_at DESC, m.id DESC",
            (hand.now() - days * DAY,),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        item = {k: row[k] for k in ("kind", "target_id", *_COLUMNS)}
        item.update({"title": row["title"]} if row["kind"] == "post" else {"post_id": row["post_id"]})
        out.append(item)
    return {"days": days, "metrics": out}


def refresh(api: RestApi | None = None) -> Json:
    """Measures the writes with a checkpoint due: `recorded` (kind, id,
    checkpoint), `missing` (not there to read), `errors`, `left` (due but
    past MAX_FETCHES, for the next run) and `rate_limited` when a 429 ended
    the run."""
    conn = rules.db()
    try:
        now = hand.now()
        done = _recorded(conn)
        due = []
        for target in _targets(conn):
            checkpoint = _due(now - float(target["at"]))
            if checkpoint is not None and checkpoint > done.get((target["kind"], str(target["target_id"])), 0):
                due.append((target, checkpoint))
        out: Json = {"recorded": [], "missing": [], "errors": [], "left": max(0, len(due) - MAX_FETCHES),
                     "rate_limited": False}
        for target, checkpoint in due[:MAX_FETCHES]:
            key = (target["kind"], str(target["target_id"]))
            try:
                got = _measure(target, api)
            except RestError as error:
                if error.status == 429:
                    out["rate_limited"] = True
                    break
                out["errors"].append(f"{key[0]} {key[1]}: {error}")
                continue
            except hand.HandError as error:
                out["errors"].append(f"{key[0]} {key[1]}: {error}")
                continue
            if got is None:
                out["missing"].append({"kind": key[0], "target_id": key[1]})
                continue
            conn.execute(
                "INSERT OR IGNORE INTO moltbook_post_metrics"
                " (kind, target_id, checkpoint, at, score, comment_count, verification_status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*key, checkpoint, now, got["score"], got["comment_count"], got["verification_status"]),
            )
            out["recorded"].append({"kind": key[0], "target_id": key[1], "checkpoint": checkpoint})
    finally:
        conn.close()
    return out
