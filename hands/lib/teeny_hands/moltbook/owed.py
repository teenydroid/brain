"""The comments on Teeny's own Moltbook posts that are owed an answer
(docs/hands.md, "moltbook unanswered").

Every comment said to Teeny on one of its own posts (a top-level comment, or
a reply under Teeny's own comment there) gets a `moltbook_owed` row the
first time `inbox.scan` sees it, and keeps it until it is settled:

  waiting   seen, not yet answered
  held      the inbound guard holds it; `inbox.release_held` makes it waiting
  answered  Teeny's answer is under it (`answer_id`): sent by the hand
            (actions.comment) or seen in the thread
  declined  not to be answered, with a `reason`: one the mind gave
            (DECLINE_REASONS, through `decline`), `too_deep` (at Moltbook's
            last drawn level, where an answer is never seen) or `gone`
            (no longer in the thread)
  expired   still waiting after rules.OWED_MAX_AGE (three days)

A waiting comment the inbox already holds and a beat has settled, without an
answer, is offered to the mind again (`offer_again`): its stimulus goes back
to pending in its old place, oldest first, with `again` and
`waiting_minutes` in its details. The first offer again comes 15 minutes
after the last, then 30, 60 and every 2 hours (OFFER_BACKOFF_MINS), until it
is answered, declined or expired. Live, 2026-11-11 to 11-15, each comment was
offered once: when the mind put the answer off to a later beat, or the guard
held its draft during a judge outage, it was never seen again, and 31 of 58
went unanswered.

While any wait, they keep a reserve of the day's comment allowance
(rules.check_comment, `own_thread_reserve`).
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime
from typing import Any, Mapping

from .. import hand, stimuli
from . import rules

Json = dict[str, Any]

#: Why the mind may decline to answer a comment on its own post. Nothing
#: else: a question it finds hard, a quiet day or a tired battery is not one.
DECLINE_REASONS = ("spam", "boilerplate", "crypto", "instruction")
#: Minutes before a waiting comment is offered again, after 1, 2, 3, 4+ offers.
OFFER_BACKOFF_MINS = (15, 30, 60, 120)
#: Most rows `unanswered` lists.
LISTED = 50
OPEN = ("waiting", "held")


def _epoch(value: Any) -> float | None:
    """Moltbook's `created_at` (ISO, Z) as epoch seconds, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def track(conn: sqlite3.Connection, cid: str, post_id: str, parent_id: str | None, author: str | None,
          depth: int, said_at: float | None = None) -> None:
    """Records a comment said to Teeny on its own post, once. One at
    Moltbook's last drawn level is declined at once: an answer there is
    never seen."""
    now = hand.now()
    deep = depth >= rules.MAX_REPLY_DEPTH
    with hand.transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO moltbook_owed (comment_id, post_id, parent_id, author, depth, said_at, seen_at,"
            " status, reason, settled_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, post_id, parent_id, author, depth, said_at if said_at is not None else now, now,
             "declined" if deep else "waiting", "too_deep" if deep else None, now if deep else None),
        )


def status(conn: sqlite3.Connection, cid: str) -> str | None:
    row = conn.execute("SELECT status FROM moltbook_owed WHERE comment_id = ?", (cid,)).fetchone()
    return row[0] if row else None


def answered(conn: sqlite3.Connection, cid: str, answer_id: str | None, at: float | None = None) -> bool:
    """Teeny's answer is under it. True when this settled it."""
    with hand.transaction(conn):
        cur = conn.execute(
            "UPDATE moltbook_owed SET status = 'answered', answer_id = ?, settled_at = ?, reason = NULL"
            " WHERE comment_id = ? AND status != 'answered'",
            (answer_id, at if at is not None else hand.now(), cid),
        )
    return cur.rowcount == 1


def held(conn: sqlite3.Connection, cid: str) -> None:
    with hand.transaction(conn):
        conn.execute("UPDATE moltbook_owed SET status = 'held' WHERE comment_id = ? AND status = 'waiting'", (cid,))


def released(conn: sqlite3.Connection, cid: str) -> None:
    """The guard let a held comment through: it waits, offered once now."""
    now = hand.now()
    with hand.transaction(conn):
        conn.execute(
            "UPDATE moltbook_owed SET status = 'waiting', offered = offered + 1, offered_at = ?"
            " WHERE comment_id = ? AND status = 'held'",
            (now, cid),
        )


def offered(conn: sqlite3.Connection, cid: str) -> None:
    """It went into the inbox for the first time."""
    with hand.transaction(conn):
        conn.execute(
            "UPDATE moltbook_owed SET offered = offered + 1, offered_at = ? WHERE comment_id = ? AND offered = 0",
            (hand.now(), cid),
        )


def gone(conn: sqlite3.Connection, post_id: str, present: set[str]) -> None:
    """The open comments of a post read whole that are no longer in it."""
    rows = conn.execute(
        f"SELECT comment_id FROM moltbook_owed WHERE post_id = ? AND status IN ({','.join('?' * len(OPEN))})",
        (post_id, *OPEN),
    ).fetchall()
    missing = [r[0] for r in rows if r[0] not in present]
    if missing:
        with hand.transaction(conn):
            conn.executemany(
                "UPDATE moltbook_owed SET status = 'declined', reason = 'gone', settled_at = ? WHERE comment_id = ?",
                [(hand.now(), cid) for cid in missing],
            )


def expire(conn: sqlite3.Connection) -> int:
    with hand.transaction(conn):
        cur = conn.execute(
            "UPDATE moltbook_owed SET status = 'expired', settled_at = ? WHERE status = 'waiting' AND said_at < ?",
            (hand.now(), hand.now() - rules.OWED_MAX_AGE),
        )
    return cur.rowcount


def _backoff(offers: int) -> float:
    return 60.0 * OFFER_BACKOFF_MINS[min(max(offers, 1), len(OFFER_BACKOFF_MINS)) - 1]


def offer_again(conn: sqlite3.Connection, inbox: sqlite3.Connection, cid: str) -> bool:
    """A waiting comment a beat already read and left unanswered goes back to
    the mind, when its back-off has passed. True when it did."""
    row = conn.execute("SELECT * FROM moltbook_owed WHERE comment_id = ?", (cid,)).fetchone()
    now = hand.now()
    if row is None or row["status"] != "waiting" or row["said_at"] < now - rules.OWED_MAX_AGE:
        return False
    if row["offered_at"] is not None and now - float(row["offered_at"]) < _backoff(int(row["offered"])):
        return False
    sid = f"moltbook:comment:{cid}"
    found = inbox.execute("SELECT status, payload FROM stimuli WHERE id = ?", (sid,)).fetchone()
    if found is None or found["status"] != "done":
        return False  # not in the inbox yet, or still waiting there
    payload = json.loads(found["payload"] or "{}")
    payload.update(again=int(row["offered"]), waiting_minutes=int((now - float(row["said_at"])) // 60))
    if not stimuli.again("moltbook", "comment", cid, payload):
        return False
    with hand.transaction(conn):
        conn.execute("UPDATE moltbook_owed SET offered = offered + 1, offered_at = ? WHERE comment_id = ?", (now, cid))
    return True


def payload_of(conn: sqlite3.Connection, cid: str) -> Json | None:
    """The details a held comment's stimulus carries, as the scan made them."""
    row = conn.execute("SELECT payload FROM moltbook_held WHERE comment_id = ?", (cid,)).fetchone()
    if row is None or not row[0]:
        return None
    try:
        got = json.loads(row[0])
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


# --- the verbs ---------------------------------------------------------------------------


def decline(body: Any) -> Json:
    """`hands/moltbook decline {comment_id, reason, note?}`: the mind will not
    answer this comment, and says why (DECLINE_REASONS)."""
    if not isinstance(body, Mapping):
        raise hand.HandError("decline takes {comment_id, reason, note?}")
    cid = body.get("comment_id")
    reason = body.get("reason")
    note = body.get("note")
    if not isinstance(cid, str) or not cid.strip():
        raise hand.HandError("decline needs a comment_id")
    if reason not in DECLINE_REASONS:
        raise hand.HandError(f"a decline's reason is one of {', '.join(DECLINE_REASONS)}")
    if note is not None and not isinstance(note, str):
        raise hand.HandError("a decline's note is text")
    cid = cid.strip()
    conn = rules.db()
    now = status(conn, cid)
    if now is None:
        raise hand.Refused("no comment on Teeny's own posts by that id waits for an answer", rule="not_owed")
    if now not in OPEN:
        raise hand.Refused(f"that comment is already {now}", rule="settled", status=now)
    if hand.dry_run():
        return {"dry_run": True, "would_decline": cid, "reason": reason}
    with hand.transaction(conn):
        conn.execute(
            "UPDATE moltbook_owed SET status = 'declined', reason = ?, note = ?, settled_at = ? WHERE comment_id = ?",
            (reason, (note or "").strip()[:300] or None, hand.now(), cid),
        )
    return {"declined": cid, "reason": reason}


def unanswered(max_items: int = LISTED) -> Json:
    """`hands/moltbook unanswered`: the comments on Teeny's own posts still
    owed an answer and how long each has waited, and how the last 24 hours
    settled. The shape is stable (part of the daily stats):

      unanswered       waiting, not yet answered
      held             held by the inbound guard
      oldest_minutes   the longest wait among both, or null
      comments         [{comment_id, post_id, parent_id, author, depth,
                         status, minutes, offered}], oldest first
      last_24h         {answered, declined, expired, median_answer_minutes}
    """
    conn = rules.db()
    expire(conn)
    now = hand.now()
    rows = conn.execute(
        f"SELECT * FROM moltbook_owed WHERE status IN ({','.join('?' * len(OPEN))}) ORDER BY said_at, comment_id",
        OPEN,
    ).fetchall()
    comments = [
        {
            "comment_id": r["comment_id"], "post_id": r["post_id"], "parent_id": r["parent_id"], "author": r["author"],
            "depth": r["depth"], "status": r["status"], "minutes": int((now - float(r["said_at"])) // 60),
            "offered": r["offered"],
        }
        for r in rows
    ]
    settled = conn.execute(
        "SELECT status, said_at, settled_at FROM moltbook_owed WHERE settled_at >= ? AND status IN"
        " ('answered', 'declined', 'expired')",
        (now - rules.DAY,),
    ).fetchall()
    waits = [max(0.0, float(r["settled_at"]) - float(r["said_at"])) / 60 for r in settled if r["status"] == "answered"]
    return {
        "unanswered": sum(1 for c in comments if c["status"] == "waiting"),
        "held": sum(1 for c in comments if c["status"] == "held"),
        "oldest_minutes": max((c["minutes"] for c in comments), default=None),
        "comments": comments[:max_items],
        "last_24h": {
            "answered": len(waits),
            "declined": sum(1 for r in settled if r["status"] == "declined"),
            "expired": sum(1 for r in settled if r["status"] == "expired"),
            "median_answer_minutes": round(statistics.median(waits)) if waits else None,
        },
    }
