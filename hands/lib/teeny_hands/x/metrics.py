"""How Teeny's own X posts and replies landed (docs/hands.md, "x metrics"):
`x_post_metrics`, one row per checkpoint, as moltbook/metrics.py keeps them
for Moltbook.

`refresh` measures each own post, quote and reply of the last WINDOW_DAYS
about CHECKPOINTS hours after it went out (1h, 24h, 72h): likes, replies,
reposts, quotes, impressions and bookmarks. A checkpoint on record is never
fetched again. A run that comes late records one row for the latest
checkpoint due and skips the ones it missed; the row's `at` says when it was
really read.

All the posts due are read in one lookup (`/2/tweets?ids=`), signed as the
account, so X bills them as owned reads ($0.001 a post), at most
MAX_FETCHES a run, newest first. A post X no longer has gets a row with no
numbers, so it is not asked for again at that checkpoint. Reads only, so it
records under DRY_RUN too, as `moltbook metrics refresh` does.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .. import hand
from . import open_db
from . import posting_rules as rules
from .client import XApi, XError

HOUR = 3600.0
DAY = 86400.0
#: Hours after a post it is measured.
CHECKPOINTS = (1, 24, 72)
#: How far back a post is still measured.
WINDOW_DAYS = 14
#: Posts read per run, at most (one lookup).
MAX_FETCHES = 30
#: What X counts, as model._metrics names it.
COUNTERS = ("likes", "replies", "reposts", "quotes", "impressions", "bookmarks")

SCHEMA = rules.SCHEMA + """
CREATE TABLE IF NOT EXISTS x_post_metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id     TEXT NOT NULL,
    checkpoint  INTEGER NOT NULL,
    at          REAL NOT NULL,
    likes       INTEGER,
    replies     INTEGER,
    reposts     INTEGER,
    quotes      INTEGER,
    impressions INTEGER,
    bookmarks   INTEGER,
    UNIQUE (post_id, checkpoint)
);
CREATE INDEX IF NOT EXISTS x_post_metrics_at ON x_post_metrics (at);
"""

Json = dict[str, Any]


def db() -> sqlite3.Connection:
    conn = open_db(SCHEMA)
    rules.add_tag_columns(conn)
    return conn


def _due(age: float) -> int | None:
    """The latest checkpoint `age` seconds have reached."""
    reached = [c for c in CHECKPOINTS if age >= c * HOUR]
    return reached[-1] if reached else None


def refresh(x: XApi | None = None) -> Json:
    """Measures the posts with a checkpoint due: `recorded` (id, checkpoint),
    `missing` (X no longer has them), `left` (due but past MAX_FETCHES, for
    the next run), and `error` or `rate_limited` when the lookup failed."""
    conn = db()
    try:
        now = hand.now()
        done = {r[0]: int(r[1]) for r in conn.execute("SELECT post_id, max(checkpoint) FROM x_post_metrics GROUP BY post_id")}
        rows = conn.execute(
            "SELECT id, created_at FROM x_posts WHERE created_at >= ? ORDER BY created_at DESC",
            (now - WINDOW_DAYS * DAY,),
        ).fetchall()
        due = []
        for row in rows:
            checkpoint = _due(now - float(row["created_at"]))
            if checkpoint is not None and checkpoint > done.get(row["id"], 0):
                due.append((row["id"], checkpoint))
        out: Json = {"recorded": [], "missing": [], "left": max(0, len(due) - MAX_FETCHES), "rate_limited": False}
        due = due[:MAX_FETCHES]
        if not due:
            return out
        try:
            got = (x or XApi.from_settings()).own_posts([post_id for post_id, _ in due])
        except XError as error:
            if error.status == 429:
                out["rate_limited"] = True
            else:
                out["error"] = str(error)
            return out
        found = {p["id"]: p.get("metrics") or {} for p in got}
        for post_id, checkpoint in due:
            counters = found.get(post_id)
            if counters is None:
                out["missing"].append(post_id)
                values: list[Any] = [None] * len(COUNTERS)
            else:
                values = [counters.get(name) for name in COUNTERS]
            conn.execute(
                f"INSERT OR IGNORE INTO x_post_metrics (post_id, checkpoint, at, {', '.join(COUNTERS)})"
                f" VALUES (?, ?, ?, {', '.join('?' * len(COUNTERS))})",
                (post_id, checkpoint, now, *values),
            )
            if counters is not None:
                out["recorded"].append({"id": post_id, "checkpoint": checkpoint})
    finally:
        conn.close()
    return out


def listing(days: int = 7) -> Json:
    """The latest row of each own post measured in the last `days`, newest
    post first, with its kind, format and the first 120 characters."""
    conn = db()
    try:
        rows = conn.execute(
            f"SELECT m.post_id, m.checkpoint, m.at, {', '.join('m.' + c for c in COUNTERS)},"
            "       p.kind, p.format, p.experiment, substr(p.text, 1, 120) AS text, p.created_at"
            " FROM x_post_metrics m"
            " JOIN (SELECT post_id, max(checkpoint) AS checkpoint FROM x_post_metrics GROUP BY post_id) newest"
            "   USING (post_id, checkpoint)"
            " JOIN x_posts p ON p.id = m.post_id"
            " WHERE m.at >= ? ORDER BY p.created_at DESC",
            (hand.now() - days * DAY,),
        ).fetchall()
    finally:
        conn.close()
    return {"days": days, "metrics": [{k: row[k] for k in row.keys() if row[k] is not None} for row in rows]}
