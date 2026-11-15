"""The threads Teeny follows on Hacker News (`hn_watch`), whose new comments
`inbox` files.

A watch is an item (a story, or a comment and the replies under it), why it
is watched, and `last_seen`: the newest comment time the inbox has read in
it, so a comment is filed only once it is newer. It starts at the moment the
watch was added, so what was said before is not filed (`thread` reads it).
A watch lapses after WATCH_DAYS: HN conversations are over by then, and the
list stays short (MAX_WATCHES). It writes only state/hands.db, so `DRY_RUN`
does not apply.
"""

from __future__ import annotations

from typing import Any

from .. import hand
from . import client, reads

#: How long a watch lasts.
WATCH_DAYS = 14
#: Watches at once.
MAX_WATCHES = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS hn_watch (
    item_id   INTEGER PRIMARY KEY,
    title     TEXT,
    note      TEXT NOT NULL DEFAULT '',
    added_at  REAL NOT NULL,
    last_seen REAL NOT NULL
);
"""

Json = dict[str, Any]


def db():
    return hand.open_db(SCHEMA)


def _row(row) -> Json:
    out = dict(row)
    out["expires_at"] = row["added_at"] + WATCH_DAYS * 86400
    return out


def _since() -> float:
    return hand.now() - WATCH_DAYS * 86400


def add(value: Any, note: str | None = None) -> Json:
    """Watches a live item. Watching one twice keeps the first watch."""
    item_id = client.item_id(value)
    got = reads.raw_item(item_id)
    if not reads.alive(got):
        raise hand.HandError(f"no Hacker News item {item_id} to watch (missing, deleted or dead)")
    title = reads.plain(got.get("title")) or None
    now = hand.now()
    conn = db()
    with hand.transaction(conn):
        conn.execute("DELETE FROM hn_watch WHERE added_at < ?", (_since(),))
        known = conn.execute("SELECT * FROM hn_watch WHERE item_id = ?", (item_id,)).fetchone()
        if known is not None:
            return {"watch": _row(known), "already": True}
        count = conn.execute("SELECT COUNT(*) FROM hn_watch").fetchone()[0]
        if count >= MAX_WATCHES:
            raise hand.Refused(f"already watching {MAX_WATCHES} threads; remove one first", rule="watches")
        conn.execute(
            "INSERT INTO hn_watch (item_id, title, note, added_at, last_seen) VALUES (?, ?, ?, ?, ?)",
            (item_id, title, (note or "").strip(), now, now),
        )
        row = conn.execute("SELECT * FROM hn_watch WHERE item_id = ?", (item_id,)).fetchone()
    return {"watch": _row(row)}


def remove(value: Any) -> Json:
    item_id = client.item_id(value)
    conn = db()
    with hand.transaction(conn):
        cur = conn.execute("DELETE FROM hn_watch WHERE item_id = ?", (item_id,))
    if cur.rowcount == 0:
        raise hand.HandError(f"not watching {item_id}")
    return {"removed": item_id}


def listing() -> Json:
    rows = db().execute("SELECT * FROM hn_watch ORDER BY added_at, item_id").fetchall()
    return {"watches": [_row(r) for r in rows]}


def active() -> list[Json]:
    """The watches that have not lapsed, oldest first."""
    rows = db().execute(
        "SELECT * FROM hn_watch WHERE added_at >= ? ORDER BY added_at, item_id", (_since(),)
    ).fetchall()
    return [_row(r) for r in rows]


def any_active() -> bool:
    return bool(active())


def seen(item_id: int, at: float) -> None:
    """The inbox read every comment in this watch up to `at`."""
    conn = db()
    with hand.transaction(conn):
        conn.execute("UPDATE hn_watch SET last_seen = MAX(last_seen, ?) WHERE item_id = ?", (at, item_id))
