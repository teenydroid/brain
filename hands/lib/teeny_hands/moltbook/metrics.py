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

import json
from typing import Any, Mapping

from .. import hand
from ..rest import RestApi, RestError
from . import reads, rules
from .actions import _walk
from .study import _when

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


# --- the daily numbers (hands/stats snapshot) -----------------------------------------------------------
#
# What the plan's targets are written in (docs/audit/moltbook-plan.md,
# "Targets"), counted from the ledger once a day and kept in `stats` under
# source `moltbook` (docs/hands.md, "moltbook metrics daily"). Every window
# is the 24 hours or 7 days before the snapshot, not the calendar day.

#: An unanswered challenge expires this long after the write (Moltbook: five
#: minutes); a younger write is not settled yet and is left out of the loss.
CHALLENGE_SECONDS = 5 * 60.0
#: Ledger statuses of a write Moltbook took but hides: its challenge failed,
#: went unanswered, or was never reached.
LOST = ("needs_verification", "verification_failed", "created")
#: A metrics row's verification status that means the write is hidden.
HIDDEN = ("pending", "failed")
#: Own posts whose threads are read for comments Teeny has not answered.
THREAD_HOURS = 72
#: Threads read for that, at most, newest first.
MAX_THREADS = 25
#: Scores counted as a post that landed, and one that did well.
SCORE_MARKS = (10, 50)
#: The days of posts the top scores are counted over (the plan's 14).
SCORE_DAYS = WINDOW_DAYS


def _median(values: list[float]) -> float | None:
    values = sorted(values)
    if not values:
        return None
    middle = len(values) // 2
    got = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    return round(float(got), 1)


def _hidden(conn, kind: str, target_id: str) -> bool:
    row = conn.execute(
        "SELECT verification_status FROM moltbook_post_metrics WHERE kind = ? AND target_id = ?"
        " ORDER BY checkpoint DESC LIMIT 1",
        (kind, target_id),
    ).fetchone()
    return row is not None and row[0] in HIDDEN


def _live_posts(conn, since: float, until: float) -> list[str]:
    accepted = ",".join("?" * len(rules.ACCEPTED))
    rows = conn.execute(
        f"SELECT post_id FROM moltbook_posts WHERE post_id IS NOT NULL AND status IN ({accepted})"
        " AND at > ? AND at <= ?",
        (*rules.ACCEPTED, since, until),
    ).fetchall()
    return [r[0] for r in rows if not _hidden(conn, "post", r[0])]


def _lost_share(conn, kind: str | None, since: float, until: float) -> float | None:
    """Percent of writes Moltbook took in (since, until] that its challenge
    hid; None when there were none."""
    counted = ",".join("?" * len(rules.COUNTED))
    tables = {"post": "moltbook_posts", "comment": "moltbook_comments"}
    statuses: list[str] = []
    for name in ([kind] if kind else ["post", "comment"]):
        statuses += [r[0] for r in conn.execute(
            f"SELECT status FROM {tables[name]} WHERE status IN ({counted}) AND at > ? AND at <= ?",
            (*rules.COUNTED, since, until),
        )]
    if not statuses:
        return None
    return round(100.0 * sum(s in LOST for s in statuses) / len(statuses), 1)


def _comments_by_target(conn, since: float, until: float) -> tuple[int, int]:
    """Teeny's live comments in (since, until]: on other agents' posts, on its own."""
    accepted = ",".join("?" * len(rules.ACCEPTED))
    rows = conn.execute(
        "SELECT c.comment_id, c.detail, p.post_id IS NOT NULL AS own FROM moltbook_comments c"
        " LEFT JOIN (SELECT DISTINCT post_id FROM moltbook_posts WHERE post_id IS NOT NULL) p ON p.post_id = c.post_id"
        f" WHERE c.status IN ({accepted}) AND c.at > ? AND c.at <= ?",
        (*rules.ACCEPTED, since, until),
    ).fetchall()
    own = 0
    for row in rows:
        try:
            detail = json.loads(row["detail"] or "{}")
        except ValueError:
            detail = {}
        own += bool(row["own"] or (isinstance(detail, dict) and detail.get("own_post") is True))
    return len(rows) - own, own


def _scores(conn, since: float, until: float) -> tuple[list[int], list[int]]:
    """Of Teeny's live posts written in (since, until]: the 24h checkpoint's
    scores, and each post's best score at any checkpoint."""
    accepted = ",".join("?" * len(rules.ACCEPTED))
    rows = conn.execute(
        "SELECT p.post_id, m.checkpoint, m.score FROM moltbook_posts p"
        " JOIN moltbook_post_metrics m ON m.kind = 'post' AND m.target_id = p.post_id"
        f" WHERE p.status IN ({accepted}) AND p.at > ? AND p.at <= ? AND m.score IS NOT NULL",
        (*rules.ACCEPTED, since, until),
    ).fetchall()
    # A post whose latest row says Moltbook hides it is not live (the ledger
    # may still say verified), so its scores are left out like its count.
    rows = [r for r in rows if not _hidden(conn, "post", r["post_id"])]
    day = [int(r["score"]) for r in rows if r["checkpoint"] == 24]
    best: dict[str, int] = {}
    for r in rows:
        best[r["post_id"]] = max(best.get(r["post_id"], int(r["score"])), int(r["score"]))
    return day, list(best.values())


def own_thread_replies(conn, api: RestApi | None = None, now: float | None = None) -> Json:
    """Other agents' comments on Teeny's own posts of the last THREAD_HOURS
    and whether Teeny answered each: `comments`, `answered`, `unanswered`
    (answerable ones with no answer: a comment at rules.MAX_REPLY_DEPTH, or
    one replying to another agent rather than to Teeny, cannot be answered
    and is not counted), `answer_minutes` (from the comment to
    Teeny's first answer, each answered one), `threads` read and `complete`
    (False when a read failed, so the counts are short).

    An answer is a reply under the comment by Teeny that the thread shows
    as not hidden, or Teeny's own live comment with that parent in the
    ledger (its `at` is the answer's time when it is earlier).

    Read from the post's comments (the read cache, two minutes), at most
    MAX_THREADS threads, stopping at the first 429. When hands/moltbook
    gains its own read of unanswered own-thread comments, this is the one
    place to switch to it."""
    now = hand.now() if now is None else now
    accepted = ",".join("?" * len(rules.ACCEPTED))
    posts = [r[0] for r in conn.execute(
        f"SELECT post_id FROM moltbook_posts WHERE post_id IS NOT NULL AND status IN ({accepted}) AND at > ?"
        " ORDER BY at DESC LIMIT ?",
        (*rules.ACCEPTED, now - THREAD_HOURS * HOUR, MAX_THREADS),
    )]
    answers: dict[str, float] = {}
    for parent, at in conn.execute(
        f"SELECT parent_id, min(at) FROM moltbook_comments WHERE parent_id IS NOT NULL AND status IN ({accepted})"
        " GROUP BY parent_id",
        rules.ACCEPTED,
    ):
        answers[str(parent)] = float(at)
    out: Json = {"threads": 0, "comments": 0, "answered": 0, "unanswered": 0, "answer_minutes": [], "complete": True}
    if not posts:
        return out
    try:
        me = reads.me_name(api).lower()
    except hand.HandError:
        # Without Teeny's name no answer can be told apart: the thread numbers
        # are short, and the ledger's own numbers still land.
        out["complete"] = False
        return out
    for post_id in posts:
        try:
            tree = reads.comments(str(post_id), "new", COMMENTS_READ, api).get("comments")
        except RestError as error:
            if error.status == 404:
                continue
            out["complete"] = False
            if error.status == 429:
                break
            continue
        except hand.HandError:
            out["complete"] = False
            continue
        out["threads"] += 1
        _count_thread(tree, 1, me, answers, out)
    return out


def _author_name(node: Mapping[str, Any]) -> str:
    author = node.get("author")
    name = author.get("name") if isinstance(author, Mapping) else node.get("author_name")
    return name.lower() if isinstance(name, str) else ""


def _count_thread(nodes: Any, depth: int, me: str, answers: Mapping[str, float], out: Json,
                  under_me: bool = False) -> None:
    """`under_me`: the nodes are replies to Teeny. On its own post Teeny may
    answer a top-level comment or a reply to Teeny, nothing else
    (actions._check_comment_rules, once_per_post), so only those can wait."""
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        replies = [r for r in node.get("replies") or [] if isinstance(r, Mapping)] if isinstance(node.get("replies"), list) else []
        theirs = _author_name(node) != me and node.get("id") is not None
        if theirs and not node.get("is_deleted") and not node.get("is_spam"):
            out["comments"] += 1
            mine = [r for r in replies if _author_name(r) == me and r.get("verification_status") not in HIDDEN]
            ledger = answers.get(str(node["id"]))
            if mine or ledger is not None:
                out["answered"] += 1
                times = [t for t in (_when(r.get("created_at")) for r in mine) if t is not None]
                times += [ledger] if ledger is not None else []
                asked = _when(node.get("created_at"))
                if times and asked is not None:
                    out["answer_minutes"].append(round(max(0.0, min(times) - asked) / 60, 1))
            elif depth < rules.MAX_REPLY_DEPTH and (depth == 1 or under_me):
                out["unanswered"] += 1
        _count_thread(replies, depth + 1, me, answers, out, under_me=_author_name(node) == me)


def daily(api: RestApi | None = None) -> dict[str, float]:
    """The day's numbers for `stats` (docs/hands.md, "moltbook metrics
    daily"). A number with nothing to count is left out, never zero: a
    median of no posts, a share of no writes, the own-thread numbers when a
    thread could not be read."""
    now = hand.now()
    conn = rules.db()
    try:
        out: dict[str, float] = {"live_posts_24h": float(len(_live_posts(conn, now - DAY, now)))}
        day_scores, _ = _scores(conn, now - 7 * DAY, now)
        median = _median(day_scores)
        if median is not None:
            out["post_score_24h_median_7d"] = median
        _, best14 = _scores(conn, now - SCORE_DAYS * DAY, now)
        for mark in SCORE_MARKS:
            out[f"posts_{mark}_plus_{SCORE_DAYS}d"] = float(sum(s >= mark for s in best14))
        others, own = _comments_by_target(conn, now - DAY, now)
        out["comments_on_others_24h"] = float(others)
        out["comments_on_own_24h"] = float(own)
        settled = now - CHALLENGE_SECONDS
        for label, days in (("24h", 1), ("7d", 7)):
            for kind in (None, "post", "comment"):
                share = _lost_share(conn, kind, settled - days * DAY, settled)
                if share is not None:
                    out["lost_pct_" + (f"{kind}_" if kind else "") + label] = share
        threads = own_thread_replies(conn, api, now)
    finally:
        conn.close()
    if threads["complete"]:
        out["own_thread_unanswered"] = float(threads["unanswered"])
        out["own_thread_comments"] = float(threads["comments"])
        answerable = threads["answered"] + threads["unanswered"]
        if answerable:
            out["own_thread_answered_pct"] = round(100.0 * threads["answered"] / answerable, 1)
        median = _median(threads["answer_minutes"])
        if median is not None:
            out["own_thread_answer_minutes_median"] = median
    return out
