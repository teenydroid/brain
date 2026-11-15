"""Closing hands/reddit's ledger rows when the browser desk settles their
task (hands/tasks calls `settle`), and what the report brought back.

A task that ended ok marks its rows `done`, with the permalink of what Teeny
wrote (`own`, so it is never voted on); any other outcome marks them
`failed`, which frees their place under the caps. From an ok report's
`reddit` object it keeps:

  items      [{permalink, title?, author?}]   into `reddit_seen`: what an upvote may target
  rules      a subreddit's rules (a browse of one)   into `reddit_rules`
  handle, karma {post, comment}, age_days   into `reddit_account` (a stats read)

Every field is checked and anything malformed is left out; nothing here ever
raises past the desk. `sync` closes rows whose task ended without a settle
(a stale claim failed, a code that never came, a hand-over to a person).
"""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from .. import hand
from . import rules

#: The most items one report adds to `reddit_seen`.
MAX_ITEMS = 50
TITLE_CHARS = 300
RULES_KEEP_CHARS = 4000

Json = dict[str, Any]


def sync(conn: sqlite3.Connection) -> int:
    """Closes queued rows whose desk task has ended: done when it ended ok,
    failed otherwise. How many changed."""
    from ..tasks import queue

    queue.db().close()  # browser_tasks exists
    now = hand.now()
    changed = 0
    with hand.transaction(conn):
        for final, status in (("('failed', 'needs_human')", "failed"), ("('ok')", "done")):
            changed += conn.execute(
                f"UPDATE reddit_actions SET status = ?, settled_at = ? WHERE status = 'queued' AND task_id IN"
                f" (SELECT id FROM browser_tasks WHERE status IN {final})",
                (status, now),
            ).rowcount
    return changed


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _items(conn: sqlite3.Connection, task_id: int, items: Any) -> int:
    if not isinstance(items, list):
        return 0
    kept = 0
    for item in items[:MAX_ITEMS]:
        if not isinstance(item, Mapping):
            continue
        try:
            link = rules.permalink(item.get("permalink"))
        except hand.HandError:
            continue
        title = item.get("title") if isinstance(item.get("title"), str) else None
        author = item.get("author") if isinstance(item.get("author"), str) else None
        conn.execute(
            "INSERT INTO reddit_seen (target, permalink, subreddit, title, author, task_id, seen_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(target) DO UPDATE SET permalink = excluded.permalink,"
            " subreddit = excluded.subreddit, title = coalesce(excluded.title, reddit_seen.title),"
            " author = coalesce(excluded.author, reddit_seen.author), task_id = excluded.task_id,"
            " seen_at = excluded.seen_at",
            (link["target"], link["url"], link["subreddit"], (title or "")[:TITLE_CHARS] or None,
             (author or "").strip().removeprefix("u/")[:40] or None, task_id, hand.now()),
        )
        kept += 1
    return kept


def _account(conn: sqlite3.Connection, report: Mapping[str, Any]) -> bool:
    karma = report.get("karma")
    if not isinstance(karma, Mapping) and report.get("age_days") is None:
        return False
    found = rules.account()
    handle = report.get("handle") if isinstance(report.get("handle"), str) else None
    try:
        handle = rules.username(handle) if handle else (found or {}).get("handle")
    except hand.HandError:
        handle = (found or {}).get("handle")
    if not handle:
        return False
    karma = karma if isinstance(karma, Mapping) else {}
    age = report.get("age_days")
    age = float(age) if isinstance(age, (int, float)) and not isinstance(age, bool) and age >= 0 else None
    conn.execute(
        "INSERT INTO reddit_account (handle, karma_post, karma_comment, age_days, checked_at) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(handle) DO UPDATE SET karma_post = coalesce(excluded.karma_post, reddit_account.karma_post),"
        " karma_comment = coalesce(excluded.karma_comment, reddit_account.karma_comment),"
        " age_days = coalesce(excluded.age_days, reddit_account.age_days), checked_at = excluded.checked_at",
        (handle, _int(karma.get("post")), _int(karma.get("comment")), age, hand.now()),
    )
    return True


def settle(task_id: int, outcome: str, report: Mapping[str, Any], notes: str = "") -> Json:
    """Closes the task's rows and keeps what an ok report brought back."""
    report = report if isinstance(report, Mapping) else {}
    conn = rules.db()
    try:
        rows = conn.execute("SELECT * FROM reddit_actions WHERE task_id = ? AND status = 'queued'",
                            (task_id,)).fetchall()
        out: Json = {"rows": len(rows)}
        status = "done" if outcome == "ok" else "failed"
        link = None
        if outcome == "ok" and report.get("permalink") is not None:
            try:
                link = rules.permalink(report.get("permalink"))
            except hand.HandError:
                out["permalink_refused"] = str(report.get("permalink"))[:200]
        with hand.transaction(conn):
            for row in rows:
                writes = row["verb"] in ("comment", "reply", "post")
                conn.execute(
                    "UPDATE reddit_actions SET status = ?, settled_at = ?, permalink = ?, own = ? WHERE id = ?",
                    (status, hand.now(), link["url"] if link and writes else None,
                     link["target"] if link and writes else None, row["id"]),
                )
            if outcome == "ok":
                out["seen"] = _items(conn, task_id, report.get("items"))
                browse = next((r for r in rows if r["verb"] == "browse" and r["subreddit"]), None)
                said = report.get("rules")
                if browse is not None and isinstance(said, str) and said.strip():
                    conn.execute(
                        "INSERT INTO reddit_rules (subreddit, rules, task_id, captured_at) VALUES (?, ?, ?, ?)"
                        " ON CONFLICT(subreddit) DO UPDATE SET rules = excluded.rules, task_id = excluded.task_id,"
                        " captured_at = excluded.captured_at",
                        (browse["subreddit"], said.strip()[:RULES_KEEP_CHARS], task_id, hand.now()),
                    )
                    out["rules"] = browse["subreddit"]
                if _account(conn, report):
                    out["account"] = True
        out["status"] = status
        if link and any(r["verb"] in ("comment", "reply", "post") for r in rows):
            out["permalink"] = link["url"]
        return out
    finally:
        conn.close()
