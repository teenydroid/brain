"""How Teeny's own Reddit posts and comments landed (docs/hands.md, "reddit
metrics"): `reddit_post_metrics`, one row per checkpoint, the shape of
hands/moltbook's.

`refresh` measures each of Teeny's own writes about CHECKPOINTS hours after
it went out (1h, 24h, 72h), for WINDOW_DAYS: its score, a post's comment
count, and its `state`: `live`, `removed` (by a moderator, the admins or the
spam filter) or `deleted` (by Teeny). A checkpoint on record is never
fetched again; a late run records the latest checkpoint due and skips the
ones it missed. Every write due is read in one `/api/info` call (Reddit
takes 100 fullnames a call, MAX_FETCHES). A write found `removed` the first
time starts a back-off on its subreddit (rules, `removed`).

Reads only, so it records under DRY_RUN too. With nothing due it calls
nothing, so it needs no credentials then (bin/stats-snapshot runs it every
hour on every box).

`daily` is the account's karma for hands/stats (source `reddit`).
"""

from __future__ import annotations

from typing import Any, Mapping

from .. import hand
from ..rest import RestError
from . import client, reads, rules

HOUR = 3600.0
DAY = 86400.0
CHECKPOINTS = (1, 24, 72)
WINDOW_DAYS = 14
MAX_FETCHES = 100
#: `removed_by_category` values that mean Teeny's own hand took it down.
SELF_REMOVED = ("deleted", "author")

Json = dict[str, Any]


def _due(age: float) -> int | None:
    reached = [c for c in CHECKPOINTS if age >= c * HOUR]
    return reached[-1] if reached else None


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def state(thing: Mapping[str, Any]) -> str:
    """`live`, `removed` or `deleted`, from what Reddit shows the author.
    A shadow removal that Reddit shows its author as live cannot be seen
    here."""
    category = thing.get("removed_by_category")
    text = thing.get("selftext") if thing.get("kind") == "t3" else thing.get("body")
    if category in SELF_REMOVED or text == "[deleted]" or thing.get("author") == "[deleted]":
        return "deleted"
    if category or thing.get("banned_by") or thing.get("removed") is True or thing.get("spam") is True or text == "[removed]":
        return "removed"
    return "live"


def _targets(conn) -> list[Json]:
    since = hand.now() - WINDOW_DAYS * DAY
    counted = ",".join("?" * len(rules.COUNTED))
    rows = conn.execute(
        f"SELECT 'post' AS kind, post_id AS target_id, subreddit, at FROM reddit_posts"
        f" WHERE post_id IS NOT NULL AND at >= ? AND status IN ({counted})"
        " UNION ALL"
        f" SELECT 'comment', comment_id, subreddit, at FROM reddit_comments"
        f" WHERE comment_id IS NOT NULL AND at >= ? AND status IN ({counted})"
        " ORDER BY at DESC",
        (since, *rules.COUNTED, since, *rules.COUNTED),
    ).fetchall()
    return [dict(r) for r in rows]


def _recorded(conn) -> dict[tuple[str, str], int]:
    rows = conn.execute("SELECT kind, target_id, max(checkpoint) FROM reddit_post_metrics GROUP BY kind, target_id")
    return {(r[0], r[1]): int(r[2]) for r in rows}


def _seen_removed(conn, kind: str, target_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM reddit_post_metrics WHERE kind = ? AND target_id = ? AND state = 'removed'", (kind, target_id)
    ).fetchone() is not None


def refresh(api: client.Reddit | None = None) -> Json:
    """Measures the writes with a checkpoint due: `recorded`, `missing`
    (Reddit no longer has it), `removed` (found removed for the first time),
    `errors`, `left` (past MAX_FETCHES, for the next run) and `rate_limited`."""
    conn = rules.db()
    try:
        now = hand.now()
        done = _recorded(conn)
        due = []
        for target in _targets(conn):
            checkpoint = _due(now - float(target["at"]))
            if checkpoint is not None and checkpoint > done.get((target["kind"], str(target["target_id"])), 0):
                due.append((target, checkpoint))
        out: Json = {"recorded": [], "missing": [], "removed": [], "errors": [],
                     "left": max(0, len(due) - MAX_FETCHES), "rate_limited": False}
        due = due[:MAX_FETCHES]
        if not due:
            return out
        names = [("t3_" if t["kind"] == "post" else "t1_") + str(t["target_id"]) for t, _ in due]
        try:
            found = {str(t.get("name")): t for t in reads.things(names, api)}
        except RestError as error:
            if error.status == 429:
                out["rate_limited"] = True
            else:
                out["errors"].append(str(error))
            return out
        for (target, checkpoint), name in zip(due, names):
            key = (target["kind"], str(target["target_id"]))
            thing = found.get(name)
            if thing is None:
                out["missing"].append({"kind": key[0], "target_id": key[1]})
                continue
            now_state = state(thing)
            first_removal = now_state == "removed" and not _seen_removed(conn, *key)
            conn.execute(
                "INSERT OR IGNORE INTO reddit_post_metrics (kind, target_id, checkpoint, at, score, comment_count, state)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*key, checkpoint, now, _int(thing.get("score")),
                 _int(thing.get("num_comments")) if key[0] == "post" else None, now_state),
            )
            out["recorded"].append({"kind": key[0], "target_id": key[1], "checkpoint": checkpoint})
            if first_removal:
                out["removed"].append({"kind": key[0], "target_id": key[1]})
                rules.start_backoff(conn, str(target["subreddit"]), "removed",
                                    f"Teeny's {key[0]} {key[1]} in r/{target['subreddit']} was removed", f"{key[0]} {key[1]}")
    finally:
        conn.close()
    return out


def listing(days: int = 7) -> Json:
    """The latest row of each write measured in the last `days`, newest
    write first: a post with its title, a comment with its post's id."""
    conn = rules.db()
    try:
        rows = conn.execute(
            "SELECT m.kind, m.target_id, m.checkpoint, m.at, m.score, m.comment_count, m.state,"
            "       p.title, c.post_id, coalesce(p.subreddit, c.subreddit) AS subreddit, coalesce(p.at, c.at) AS written_at"
            " FROM reddit_post_metrics m"
            " JOIN (SELECT kind, target_id, max(checkpoint) AS checkpoint FROM reddit_post_metrics"
            "       GROUP BY kind, target_id) newest USING (kind, target_id, checkpoint)"
            " LEFT JOIN reddit_posts p ON m.kind = 'post' AND p.post_id = m.target_id"
            " LEFT JOIN reddit_comments c ON m.kind = 'comment' AND c.comment_id = m.target_id"
            " WHERE m.at >= ? ORDER BY written_at DESC, m.id DESC",
            (hand.now() - days * DAY,),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        item = {k: row[k] for k in ("kind", "target_id", "subreddit", "checkpoint", "at", "score", "comment_count", "state")}
        item.update({"title": row["title"]} if row["kind"] == "post" else {"post_id": row["post_id"]})
        out.append(item)
    return {"days": days, "metrics": out}


def daily(api: client.Reddit | None = None) -> dict[str, float]:
    """The account's karma now (hands/stats, source `reddit`): `karma`
    (Reddit's total, else link plus comment), `link_karma`, `comment_karma`.
    A fresh read, so a suspension it finds starts the account's back-off."""
    me = reads.whoami(api, fresh=True)["me"]
    out: dict[str, float] = {}
    for key in ("link_karma", "comment_karma"):
        if _int(me.get(key)) is not None:
            out[key] = float(me[key])
    total = _int(me.get("total_karma"))
    if total is None and out:
        total = int(sum(out.values()))
    if total is not None:
        out["karma"] = float(total)
    return {k: out[k] for k in ("karma", "link_karma", "comment_karma") if k in out}
