"""The one inbox Teeny's mind reads (docs/architecture.md, "One mind").

Every surface writes what happened here instead of each running its own
scoped, amnesiac drafting turn: a mention on X, a comment on one of Teeny's
Moltbook posts, a GitHub comment, mail, a builder or operator report, a
playbook's nudge, what a hand did with Teeny's last answer, and a follow-up
Teeny set itself. `bin/tick` drains a batch into the `teeny:mind` thread, so
one conversation sees all of it in order.

A stimulus is unique on (surface, kind, ref): adding the same event twice is
a no-op, so a surface can re-scan freely.

  pending  waiting for a tick
  taken    in a tick's batch (`tick` names it); put back by `release` when
           the tick fails, or by the next `take` once it has been out an hour
  done     the tick that read it settled

Follow-ups are Teeny's own reminders ("check whether ana's cli shipped"),
kept in `follow_ups` until they come due, then raised as a `self`
stimulus.

Text is stored capped and never trusted: it is what strangers wrote, read
by a turn that has no acting tools.
"""

from __future__ import annotations

import json
import secrets
from typing import Any, Mapping

from . import hand

SURFACES = ("x", "moltbook", "gh", "mail", "builder", "operator", "nudge", "hand", "self", "world")
STATUSES = ("pending", "taken", "done")
#: One stimulus's text, beyond which it is cut.
MAX_TEXT_CHARS = 4000
#: A batch out this long belonged to a tick that died.
TAKEN_TIMEOUT_SECS = 3600
#: How far ahead a follow-up may be set.
MAX_FOLLOW_UP_SECS = 90 * 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS stimuli (
    id         TEXT PRIMARY KEY,
    surface    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    ref        TEXT NOT NULL,
    who        TEXT,
    text       TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    at         REAL NOT NULL,
    status     TEXT NOT NULL,
    tick       TEXT,
    taken_at   REAL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS stimuli_status ON stimuli (status, at);
CREATE TABLE IF NOT EXISTS follow_ups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    due_at     REAL NOT NULL,
    about      TEXT NOT NULL,
    ref        TEXT,
    status     TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS follow_ups_due ON follow_ups (status, due_at);
CREATE TABLE IF NOT EXISTS mind_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    note       TEXT NOT NULL,
    who        TEXT,
    tick       TEXT,
    at         REAL NOT NULL
);
"""

Json = dict[str, Any]


def db():
    return hand.open_db(SCHEMA)


def to_mind() -> bool:
    """Whether surfaces feed this inbox (TEENY_INBOX=mind, the default) or
    the core's inbound queue and its per-surface agents (TEENY_INBOX=core,
    the old path, kept while the mind is new)."""
    return (hand.setting("TEENY_INBOX", "mind") or "mind").strip().lower() != "core"


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= MAX_TEXT_CHARS else text[:MAX_TEXT_CHARS].rstrip() + " [...cut]"


def _row(row) -> Json:
    out = {k: row[k] for k in ("id", "surface", "kind", "ref", "who", "text", "at")}
    out["payload"] = json.loads(row["payload"] or "{}")
    return out


def add(surface: str, kind: str, ref: str, text: str, who: str | None = None,
        payload: Mapping[str, Any] | None = None, at: float | None = None) -> bool:
    """Adds one event; False when it was already there."""
    if surface not in SURFACES:
        raise hand.HandError(f"surface is one of {', '.join(SURFACES)}")
    for name, value in (("kind", kind), ("ref", ref)):
        if not isinstance(value, str) or not value.strip():
            raise hand.HandError(f"a stimulus needs a {name}")
    if not isinstance(text, str) or not text.strip():
        raise hand.HandError("a stimulus needs text")
    now = hand.now()
    conn = db()
    with hand.transaction(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO stimuli (id, surface, kind, ref, who, text, payload, at, status, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?, 'pending', ?)",
            (f"{surface}:{kind}:{ref}", surface, kind.strip(), ref.strip(), who, _clip(text),
             json.dumps(dict(payload or {}), ensure_ascii=False), at if at is not None else now, now),
        )
    return cur.rowcount == 1


def take(max_items: int = 20, max_chars: int = 30_000) -> Json:
    """The oldest pending stimuli, up to `max_items` and about `max_chars` of
    text (always at least one), marked taken under a new tick id."""
    now = hand.now()
    tick = secrets.token_hex(6)
    conn = db()
    with hand.transaction(conn):
        conn.execute(
            "UPDATE stimuli SET status = 'pending', tick = NULL, taken_at = NULL, updated_at = ?"
            " WHERE status = 'taken' AND taken_at < ?",
            (now, now - TAKEN_TIMEOUT_SECS),
        )
        rows = conn.execute(
            "SELECT * FROM stimuli WHERE status = 'pending' ORDER BY at, rowid LIMIT ?", (max_items,)
        ).fetchall()
        picked, used = [], 0
        for row in rows:
            size = len(row["text"])
            if picked and used + size > max_chars:
                break
            picked.append(row)
            used += size
        conn.executemany(
            "UPDATE stimuli SET status = 'taken', tick = ?, taken_at = ?, updated_at = ? WHERE id = ?",
            [(tick, now, now, row["id"]) for row in picked],
        )
    return {"tick": tick if picked else None, "stimuli": [_row(r) for r in picked]}


def _finish(tick: str, status: str) -> int:
    if not isinstance(tick, str) or not tick:
        raise hand.HandError("needs a tick id")
    now = hand.now()
    conn = db()
    with hand.transaction(conn):
        if status == "done":
            cur = conn.execute(
                "UPDATE stimuli SET status = 'done', updated_at = ? WHERE status = 'taken' AND tick = ?", (now, tick)
            )
        else:
            cur = conn.execute(
                "UPDATE stimuli SET status = 'pending', tick = NULL, taken_at = NULL, updated_at = ?"
                " WHERE status = 'taken' AND tick = ?",
                (now, tick),
            )
    return cur.rowcount


def settle(tick: str) -> int:
    """The tick read its batch: done for good."""
    return _finish(tick, "done")


def release(tick: str) -> int:
    """The tick failed: its batch goes back to pending, in its old place."""
    return _finish(tick, "pending")


def counts() -> dict[str, int]:
    conn = db()
    got = dict(conn.execute("SELECT status, COUNT(*) FROM stimuli GROUP BY status").fetchall())
    return {s: int(got.get(s, 0)) for s in STATUSES}


def recent(max_items: int = 50) -> Json:
    conn = db()
    rows = conn.execute("SELECT * FROM stimuli ORDER BY at DESC, rowid DESC LIMIT ?", (max_items,)).fetchall()
    return {"stimuli": [{**_row(r), "status": r["status"]} for r in rows]}


# --- follow-ups -------------------------------------------------------------------------


def follow_up(due_at: float, about: str, ref: str | None = None) -> Json:
    """Teeny's own reminder, raised as a stimulus once `due_at` passes."""
    now = hand.now()
    if not isinstance(about, str) or not about.strip():
        raise hand.HandError("a follow-up needs `about`")
    if not isinstance(due_at, (int, float)) or due_at <= now:
        raise hand.HandError("a follow-up is due in the future")
    if due_at - now > MAX_FOLLOW_UP_SECS:
        raise hand.HandError(f"a follow-up is due within {MAX_FOLLOW_UP_SECS // 86400} days")
    conn = db()
    with hand.transaction(conn):
        cur = conn.execute(
            "INSERT INTO follow_ups (due_at, about, ref, status, created_at) VALUES (?,?,?, 'pending', ?)",
            (float(due_at), _clip(about), ref, now),
        )
    return {"id": cur.lastrowid, "due_at": float(due_at), "about": about.strip()}


def raise_due() -> int:
    """Every follow-up now due becomes a `self` stimulus, once."""
    now = hand.now()
    conn = db()
    with hand.transaction(conn):
        due = conn.execute(
            "SELECT * FROM follow_ups WHERE status = 'pending' AND due_at <= ? ORDER BY due_at", (now,)
        ).fetchall()
        for row in due:
            conn.execute(
                "INSERT OR IGNORE INTO stimuli (id, surface, kind, ref, who, text, payload, at, status, updated_at)"
                " VALUES (?, 'self', 'follow_up', ?, NULL, ?, ?, ?, 'pending', ?)",
                (f"self:follow_up:{row['id']}", str(row["id"]),
                 f"you said you would come back to this: {row['about']}",
                 json.dumps({"ref": row["ref"], "set_at": row["created_at"]}), row["due_at"], now),
            )
            conn.execute("UPDATE follow_ups SET status = 'raised' WHERE id = ?", (row["id"],))
    return len(due)


def follow_ups(status: str = "pending") -> Json:
    conn = db()
    rows = conn.execute(
        "SELECT id, due_at, about, ref, status FROM follow_ups WHERE status = ? ORDER BY due_at", (status,)
    ).fetchall()
    return {"follow_ups": [dict(r) for r in rows]}


# --- notes ------------------------------------------------------------------------------


def note(text: str, who: str | None = None, tick: str | None = None) -> Json:
    """Something Teeny chose to remember. The nightly reflection folds the
    day's notes into MEMORY.md; recall keeps them after that."""
    if not isinstance(text, str) or not text.strip():
        raise hand.HandError("a note needs text")
    conn = db()
    with hand.transaction(conn):
        cur = conn.execute(
            "INSERT INTO mind_notes (note, who, tick, at) VALUES (?,?,?,?)", (_clip(text), who, tick, hand.now())
        )
    return {"id": cur.lastrowid}


def notes(since: float = 0.0, who: str | None = None, max_items: int = 200) -> Json:
    conn = db()
    if who:
        rows = conn.execute(
            "SELECT id, note, who, at FROM mind_notes WHERE at >= ? AND who = ? COLLATE NOCASE ORDER BY at LIMIT ?",
            (since, who, max_items),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, note, who, at FROM mind_notes WHERE at >= ? ORDER BY at LIMIT ?", (since, max_items)
        ).fetchall()
    return {"notes": [dict(r) for r in rows]}
