"""The wallet's tables in state/hands.db.

  wallet_payments  every payment the hand attempted, one row each, written
                   `pending` before the core is asked to prepare or execute,
                   then `paid` (with the signature), `in_doubt`, `failed`
                   (prepare failed: nothing could have moved) or `refused`
                   (the core's spend policy said no before signing)
  bills            fixed bills that keep Teeny running (a server, an API
                   plan), each in dollars a month or a year; operator-kept
  vendors          the only addresses a `vendor` payment may go to;
                   operator-kept

A `pending`, `in_doubt` or `paid` row counts against every cap, and a
purpose and ref with such a row is never paid again: an in-doubt payment
is looked at by a person, never retried by the hand.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .. import hand
from ..bounty import store as bounty_store

SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS wallet_payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    cluster     TEXT NOT NULL,
    asset       TEXT NOT NULL CHECK (asset IN ('SOL', 'USDC')),
    amount_raw  INTEGER NOT NULL CHECK (amount_raw > 0),
    usd_micro   INTEGER NOT NULL,
    to_address  TEXT NOT NULL,
    purpose     TEXT NOT NULL CHECK (purpose IN ('bounty', 'vendor')),
    ref         TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'in_doubt', 'failed', 'refused')),
    quote_id    TEXT,
    signature   TEXT,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS wallet_payments_created ON wallet_payments (created_at);
CREATE INDEX IF NOT EXISTS wallet_payments_to ON wallet_payments (to_address, created_at);
CREATE INDEX IF NOT EXISTS wallet_payments_ref ON wallet_payments (purpose, ref);
CREATE TABLE IF NOT EXISTS bills (
    name        TEXT PRIMARY KEY,
    usd_micro   INTEGER NOT NULL CHECK (usd_micro > 0),
    period      TEXT NOT NULL CHECK (period IN ('month', 'year')),
    note        TEXT,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS vendors (
    name        TEXT PRIMARY KEY,
    address     TEXT NOT NULL UNIQUE,
    note        TEXT,
    created_at  REAL NOT NULL
);
"""
    + bounty_store.SCHEMA
)

#: Rows that may have moved funds, so they count against the caps.
COUNTED = ("pending", "paid", "in_doubt")
STATUSES = ("pending", "paid", "in_doubt", "failed", "refused")

DAY = 86400.0
WEEK = 7 * DAY


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys() if row[k] is not None}


def _counted() -> str:
    return ",".join("?" * len(COUNTED))


def spent_since(conn: sqlite3.Connection, since: float, to_address: str | None = None) -> int:
    """Micro-dollars in counted rows created after `since` (to one address)."""
    query = f"SELECT COALESCE(SUM(usd_micro), 0) FROM wallet_payments WHERE status IN ({_counted()}) AND created_at > ?"
    params: list[Any] = [*COUNTED, since]
    if to_address is not None:
        query += " AND to_address = ?"
        params.append(to_address)
    return int(conn.execute(query, params).fetchone()[0])


def active_for_ref(conn: sqlite3.Connection, purpose: str, ref: str) -> sqlite3.Row | None:
    """A counted row for this purpose and ref: it was paid, or may have been."""
    return conn.execute(
        f"SELECT id, status, signature FROM wallet_payments WHERE purpose = ? AND ref = ? AND status IN ({_counted()}) ORDER BY id LIMIT 1",
        (purpose, ref, *COUNTED),
    ).fetchone()


def insert_pending(conn: sqlite3.Connection, *, cluster: str, asset: str, amount_raw: int, usd_micro: int, to_address: str, purpose: str, ref: str) -> int:
    now = hand.now()
    cursor = conn.execute(
        "INSERT INTO wallet_payments (created_at, updated_at, cluster, asset, amount_raw, usd_micro, to_address, purpose, ref, status)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
        (now, now, cluster, asset, amount_raw, usd_micro, to_address, purpose, ref),
    )
    return int(cursor.lastrowid)


def update(conn: sqlite3.Connection, payment_id: int, **fields: Any) -> None:
    allowed = {"status", "quote_id", "signature", "error"}
    if set(fields) - allowed:
        raise hand.HandError(f"cannot set {', '.join(sorted(set(fields) - allowed))} on a payment")
    if "status" in fields and fields["status"] not in STATUSES:
        raise hand.HandError(f"unknown payment status {fields['status']!r}")
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE wallet_payments SET {sets}, updated_at = ? WHERE id = ?", (*fields.values(), hand.now(), payment_id))


def payment(conn: sqlite3.Connection, payment_id: int) -> dict[str, Any] | None:
    return row_dict(conn.execute("SELECT * FROM wallet_payments WHERE id = ?", (payment_id,)).fetchone())


def vendor_by_address(conn: sqlite3.Connection, address: str) -> sqlite3.Row | None:
    return conn.execute("SELECT name, address, note FROM vendors WHERE address = ?", (address,)).fetchone()
