"""hands/rent's rows in state/hands.db.

`rent_jobs` is one bounty Teeny posted (or rehearsed), its money and where it
stands:

  rehearsed  a DRY_RUN post: previewed and priced, nothing sent
  posting    the post was sent and its answer not seen; `poll` sends it again
             with the same idempotency key, which RentAHuman answers with the
             first bounty rather than a second
  draft      posted, but the RentAHuman wallet did not cover it: nobody sees
             it until the operator pays (`pay_url`) or tops up
  posted     open and paid for: applicants come in
  hired      an applicant accepted; waiting for their work
  submitted  work waits for Teeny's review
  paying     the pay call was sent; never sent twice
  paid       final
  rejected   the work was refused; the held pay stays until RentAHuman settles
  cancelled  ended by Teeny or by RentAHuman with nobody paid
  failed     refused by RentAHuman before anything was posted
  in_doubt   a pay whose outcome is unknown: never retried, the operator looks

`rent_events` is what `poll` has already passed on (an application, a
submission, a message), so nothing reaches the mind twice.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable, Mapping

from .. import hand

STATUSES = ("rehearsed", "posting", "draft", "posted", "hired", "submitted", "paying", "paid", "rejected",
            "cancelled", "failed", "in_doubt")
#: Still Teeny's to follow.
OPEN = ("posting", "draft", "posted", "hired", "submitted")
#: What counts against the day's money: anything that was, or may have been,
#: paid for. A rehearsal or a refused post spent nothing.
SPENT = ("posting", "draft", "posted", "hired", "submitted", "paying", "paid", "rejected", "cancelled", "in_doubt")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS rent_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    at              REAL NOT NULL,
    updated_at      REAL NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ({", ".join(repr(s) for s in STATUSES)})),
    title           TEXT NOT NULL,
    request         TEXT NOT NULL,
    idem            TEXT UNIQUE,
    pay_cents       INTEGER NOT NULL,
    total_cents     INTEGER NOT NULL,
    bounty_id       TEXT,
    url             TEXT,
    pay_url         TEXT,
    application_id  TEXT,
    hire_id         TEXT,
    conversation_id TEXT,
    submission_id   TEXT,
    hired_at        REAL,
    paid_at         REAL,
    note            TEXT
);
CREATE TABLE IF NOT EXISTS rent_events (
    job_id  INTEGER NOT NULL REFERENCES rent_jobs(id),
    kind    TEXT NOT NULL,
    ref     TEXT NOT NULL,
    at      REAL NOT NULL,
    detail  TEXT,
    PRIMARY KEY (job_id, kind, ref)
);
"""


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def row(conn: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    found = conn.execute("SELECT * FROM rent_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(found) if found else None


def need(conn: sqlite3.Connection, job_id: Any) -> dict[str, Any]:
    try:
        number = int(job_id)
    except (TypeError, ValueError):
        raise hand.Refused("job is a rent job's number (hands/rent list)") from None
    found = row(conn, number)
    if found is None:
        raise hand.Refused(f"no rent job #{number}")
    return found


def update(conn: sqlite3.Connection, job_id: int, **fields: Any) -> None:
    fields["updated_at"] = hand.now()
    names = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(f"UPDATE rent_jobs SET {names} WHERE id = ?", (*fields.values(), job_id))


def insert(conn: sqlite3.Connection, status: str, title: str, request: Mapping[str, Any], pay_cents: int,
           total_cents: int) -> int:
    now = hand.now()
    cur = conn.execute(
        "INSERT INTO rent_jobs (at, updated_at, status, title, request, pay_cents, total_cents)"
        " VALUES (?,?,?,?,?,?,?)",
        (now, now, status, title, json.dumps(dict(request), ensure_ascii=False, sort_keys=True), pay_cents,
         total_cents),
    )
    return int(cur.lastrowid)


def spent_today(conn: sqlite3.Connection) -> int:
    marks = ",".join("?" for _ in SPENT)
    found = conn.execute(
        f"SELECT COALESCE(SUM(total_cents), 0) FROM rent_jobs WHERE status IN ({marks}) AND at >= ?",
        (*SPENT, hand.day_start()),
    ).fetchone()
    return int(found[0])


def open_jobs(conn: sqlite3.Connection, statuses: Iterable[str] = OPEN) -> list[dict[str, Any]]:
    wanted = tuple(statuses)
    marks = ",".join("?" for _ in wanted)
    return [dict(r) for r in conn.execute(f"SELECT * FROM rent_jobs WHERE status IN ({marks}) ORDER BY id", wanted)]


def listing(conn: sqlite3.Connection, status: str | None, limit: int = 50) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute("SELECT * FROM rent_jobs WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit))
    else:
        rows = conn.execute("SELECT * FROM rent_jobs ORDER BY id DESC LIMIT ?", (limit,))
    return [public(dict(r)) for r in rows]


def public(job: Mapping[str, Any]) -> dict[str, Any]:
    """A row as a verb answers it: the request as JSON, not a string."""
    out = dict(job)
    try:
        out["request"] = json.loads(out.get("request") or "{}")
    except ValueError:
        pass
    return out


def seen(conn: sqlite3.Connection, job_id: int, kind: str, ref: str, detail: Mapping[str, Any] | None = None) -> bool:
    """Records an event; False when it was already there."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO rent_events (job_id, kind, ref, at, detail) VALUES (?,?,?,?,?)",
        (job_id, kind, ref, hand.now(), json.dumps(dict(detail or {}), ensure_ascii=False)),
    )
    return cur.rowcount == 1
