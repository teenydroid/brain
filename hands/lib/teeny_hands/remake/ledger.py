"""What hands/remake remembers, in state/hands.db (tables made on demand).

`remake_sources(video_id, source, url, title, study_id, used_at)`: the videos
a remake was made from. `seen(video_id)` is how charts leaves one out and
study refuses it; `mark_used(...)` is for whatever makes the remake to call
once it has used the video (hands/remake study itself marks nothing: a
study is a look, not a use).

`remake_studies`: one row per study's vision call, claimed under a lock
before the call (so two studies at once cannot both take the day's last),
then finished with what the call reported (asks, tokens, cost in USD
micros). It is its own table: this is research spend, not a media asset.
A study that never reached the model is released, and so does not count.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .. import hand
from ..guard.holds import iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS remake_sources (
    video_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    study_id TEXT,
    used_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS remake_studies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    study_id TEXT NOT NULL,
    model TEXT NOT NULL,
    stage TEXT NOT NULL,
    ok INTEGER NOT NULL DEFAULT 0,
    usable INTEGER,
    reason TEXT,
    asks INTEGER,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_micro INTEGER
);
CREATE INDEX IF NOT EXISTS remake_studies_at ON remake_studies (at);
"""


def _db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def seen(video_id: str) -> bool:
    """Whether a remake was already made from this video."""
    conn = _db()
    try:
        return conn.execute("SELECT 1 FROM remake_sources WHERE video_id = ?", (video_id,)).fetchone() is not None
    finally:
        conn.close()


def seen_any(video_ids: list[str]) -> set[str]:
    """Which of `video_ids` were already made (one read)."""
    conn = _db()
    try:
        have = {r[0] for r in conn.execute("SELECT video_id FROM remake_sources")}
    finally:
        conn.close()
    return have & set(video_ids)


def mark_used(video_id: str, source: str, url: str, title: str | None = None, study_id: str | None = None) -> bool:
    """Records a video as made into a remake; False when it already was."""
    conn = _db()
    try:
        done = conn.execute(
            "INSERT OR IGNORE INTO remake_sources (video_id, source, url, title, study_id, used_at) VALUES (?, ?, ?, ?, ?, ?)",
            (video_id, source, url, title, study_id, iso(hand.now())))
        return done.rowcount == 1
    finally:
        conn.close()


def used_today(conn: sqlite3.Connection | None = None) -> int:
    """Studies that reached the model today (UTC), pending ones included."""
    own = conn is None
    conn = conn or _db()
    try:
        return conn.execute("SELECT COUNT(*) FROM remake_studies WHERE at >= ?", (iso(hand.day_start()),)).fetchone()[0]
    finally:
        if own:
            conn.close()


def claim(study_id: str, model: str, limit: int) -> int | None:
    """Today's study claimed as a `pending` row under the study lock, or None
    when the day's `limit` is used up."""
    with hand.lock("remake-study", timeout=60):
        conn = _db()
        try:
            if used_today(conn) >= limit:
                return None
            return conn.execute(
                "INSERT INTO remake_studies (at, study_id, model, stage, ok) VALUES (?, ?, ?, 'pending', 0)",
                (iso(hand.now()), study_id, model)).lastrowid
        finally:
            conn.close()


def finish(row_id: int, **fields: Any) -> None:
    """The claimed row updated with what happened (stage, ok, usable, reason,
    asks, tokens_in, tokens_out, cost_micro). A ledger that cannot be written
    loses the record, never the study."""
    try:
        conn = _db()
        try:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE remake_studies SET {sets} WHERE id = ?", (*fields.values(), row_id))
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        pass


def release(row_id: int) -> None:
    """The claim given back: the study never asked the model."""
    try:
        conn = _db()
        try:
            conn.execute("DELETE FROM remake_studies WHERE id = ? AND stage = 'pending'", (row_id,))
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        pass
