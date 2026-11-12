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

HOUR = 3600.0
DAY = 86400.0
#: Hours after a write it is measured.
CHECKPOINTS = (1, 24, 72)
#: How far back a write is still measured.
WINDOW_DAYS = 14
#: Writes read per run, at most.
MAX_FETCHES = 20

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
    got = reads.post(str(target["target_id"]), api).get("post")
    if not isinstance(got, Mapping):
        return None
    return {"score": _score(got), "comment_count": _int(got.get("comment_count")),
            "verification_status": got.get("verification_status")}


def refresh(api: RestApi | None = None) -> Json:
    conn = rules.db()
    try:
        now = hand.now()
        done = _recorded(conn)
        recorded: list[Json] = []
        for target in _targets(conn):
            checkpoint = _due(now - float(target["at"]))
            key = (target["kind"], str(target["target_id"]))
            if checkpoint is None or checkpoint <= done.get(key, 0):
                continue
            got = _measure(target, api)
            if got is None:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO moltbook_post_metrics"
                " (kind, target_id, checkpoint, at, score, comment_count, verification_status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*key, checkpoint, now, got["score"], got["comment_count"], got["verification_status"]),
            )
            recorded.append({"kind": key[0], "target_id": key[1], "checkpoint": checkpoint})
    finally:
        conn.close()
    return {"recorded": recorded}
