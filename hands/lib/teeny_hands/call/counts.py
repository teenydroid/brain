"""The call log (`call_log` in state/hands.db) and the day's caps it counts.

One row a call, whatever became of it: the service, the method, the path,
read or write, whether it was sent, the outcome, the service's status and a
reason clipped to DETAIL_CHARS. Never the body, the query or the key.

The caps are settings in secrets/teeny.env, per service:

  CALL_<SERVICE>_READS_PER_DAY    GETs a UTC day (default 0: off; at most 5000)
  CALL_<SERVICE>_WRITES_PER_DAY   every other method  (default 0: off; at most 500)

A call counts once it is sent: its row is written, `sent` = 1, in the same
transaction that finds the day's count under the cap, so two calls at once
cannot both take the last one. A call refused before that (a rule, the
guard, a rehearsed write) spends nothing; one the service failed was sent,
and counts.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .. import hand
from .services import MAX_READS_PER_DAY, MAX_WRITES_PER_DAY, Service

SCHEMA = """
CREATE TABLE IF NOT EXISTS call_log (
  id INTEGER PRIMARY KEY,
  at REAL NOT NULL,
  service TEXT NOT NULL,
  method TEXT,
  path TEXT,
  kind TEXT,
  sent INTEGER NOT NULL DEFAULT 0,
  outcome TEXT NOT NULL,
  status INTEGER,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS call_log_day ON call_log (service, kind, sent, at);
"""

DETAIL_CHARS = 200
SERVICE_CHARS = 40


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


#: What a service gets a UTC day when secrets/teeny.env names no number: a
#: listed service works without a setting, and 0 there turns it off.
DEFAULT_READS_PER_DAY = 500
DEFAULT_WRITES_PER_DAY = 50


def limit(svc: Service, kind: str) -> tuple[str, int]:
    """The setting that caps KIND ("read" or "write") on SVC, and its value."""
    if kind == "read":
        return svc.reads_setting, hand.number(svc.reads_setting, DEFAULT_READS_PER_DAY, 0, MAX_READS_PER_DAY)
    return svc.writes_setting, hand.number(svc.writes_setting, DEFAULT_WRITES_PER_DAY, 0, MAX_WRITES_PER_DAY)


def _sent_today(conn: sqlite3.Connection, svc: Service, kind: str) -> int:
    row = conn.execute("SELECT COUNT(*) FROM call_log WHERE service = ? AND kind = ? AND sent = 1 AND at >= ?",
                       (svc.name, kind, hand.day_start())).fetchone()
    return int(row[0])


def _check(conn: sqlite3.Connection, svc: Service, kind: str) -> None:
    setting, most = limit(svc, kind)
    plural = "reads" if kind == "read" else "writes"
    if most == 0:
        raise hand.Refused(f"{plural} on {svc.name} are off ({setting} is 0 or unset in secrets/teeny.env)",
                           rule="cap")
    if _sent_today(conn, svc, kind) >= most:
        raise hand.Refused(f"today's {most} {plural} on {svc.name} are spent ({setting}); the count starts again "
                           "at 00:00 UTC", rule="cap")


def check(svc: Service, kind: str) -> None:
    """Refused when KIND is off on SVC or today's are spent; counts nothing."""
    conn = db()
    try:
        _check(conn, svc, kind)
    finally:
        conn.close()


def reserve(svc: Service, kind: str, method: str, path: str) -> int:
    """Counts one call as sent, under the cap, and answers its row."""
    conn = db()
    try:
        with hand.transaction(conn):
            _check(conn, svc, kind)
            cur = conn.execute("INSERT INTO call_log (at, service, method, path, kind, sent, outcome) "
                               "VALUES (?, ?, ?, ?, ?, 1, 'sending')", (hand.now(), svc.name, method, path, kind))
            return int(cur.lastrowid)
    finally:
        conn.close()


def record(row: dict[str, Any], outcome: str, detail: str | None = None, status: int | None = None) -> None:
    """ROW's outcome: the row reserve wrote when it has an `id`, else a new
    row for a call that was never sent."""
    detail = detail[:DETAIL_CHARS] if detail else None
    status = status if isinstance(status, int) and not isinstance(status, bool) else None
    conn = db()
    try:
        if row.get("id") is not None:
            conn.execute("UPDATE call_log SET outcome = ?, status = ?, detail = ? WHERE id = ?",
                         (outcome, status, detail, row["id"]))
        else:
            conn.execute("INSERT INTO call_log (at, service, method, path, kind, sent, outcome, status, detail) "
                         "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
                         (hand.now(), str(row.get("service") or "?")[:SERVICE_CHARS], row.get("method"),
                          row.get("path"), row.get("kind"), outcome, status, detail))
    finally:
        conn.close()
