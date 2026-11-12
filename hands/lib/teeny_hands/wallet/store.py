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
  wallet_tiny_buys every run of `buy-tiny` (buy.py): `skipped` with its
                   reason, or `pending` before the core is asked, then
                   `bought` (with the signature), `failed` (nothing could
                   have moved) or `in_doubt`

A `pending`, `in_doubt` or `paid` row counts against every cap, and a
purpose and ref with such a row is never paid again: an in-doubt payment
is looked at by a person, never retried by the hand. A `pending`,
`in_doubt` or `bought` buy counts against the per-payment and daily caps
the same way, beside the payments.
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
CREATE TABLE IF NOT EXISTS wallet_tiny_buys (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    cluster         TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('skipped', 'pending', 'bought', 'failed', 'in_doubt')),
    input_mint      TEXT NOT NULL,
    output_mint     TEXT NOT NULL CHECK (output_mint = '2AF7CqwieUjUPALL7icuZtL3X7wENdjUjGBMmfV2pump'),
    usdc_raw        INTEGER NOT NULL CHECK (usdc_raw >= 0),
    usd_micro       INTEGER NOT NULL CHECK (usd_micro >= 0),
    tiny_out_raw    INTEGER,
    tiny_min_raw    INTEGER,
    price_impact_bps INTEGER,
    slippage_bps    INTEGER,
    route           TEXT,
    since           REAL NOT NULL,
    earned_micro    INTEGER NOT NULL,
    spent_micro     INTEGER NOT NULL,
    share           TEXT NOT NULL,
    reason          TEXT,
    quote_id        TEXT,
    signature       TEXT,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS wallet_tiny_buys_created ON wallet_tiny_buys (created_at);
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
    """Micro-dollars in counted rows created after `since` (to one address).
    Without an address the $TINY buys count too: a buy is money out of the
    wallet like any payment, it only goes to Teeny's own holding."""
    query = f"SELECT COALESCE(SUM(usd_micro), 0) FROM wallet_payments WHERE status IN ({_counted()}) AND created_at > ?"
    params: list[Any] = [*COUNTED, since]
    if to_address is not None:
        query += " AND to_address = ?"
        params.append(to_address)
    total = int(conn.execute(query, params).fetchone()[0])
    if to_address is None:
        total += bought_since(conn, since)
    return total


#: Buys that may have moved funds, so they count against the caps.
BUY_COUNTED = ("pending", "bought", "in_doubt")
BUY_STATUSES = ("skipped", "pending", "bought", "failed", "in_doubt")


def bought_since(conn: sqlite3.Connection, since: float) -> int:
    """Micro-dollars in counted $TINY buys created after `since`."""
    marks = ",".join("?" * len(BUY_COUNTED))
    return int(
        conn.execute(
            f"SELECT COALESCE(SUM(usd_micro), 0) FROM wallet_tiny_buys WHERE status IN ({marks}) AND created_at > ?",
            (*BUY_COUNTED, since),
        ).fetchone()[0]
    )


def last_buy(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The newest buy that may have moved funds: inflows are counted from it."""
    marks = ",".join("?" * len(BUY_COUNTED))
    return conn.execute(
        f"SELECT * FROM wallet_tiny_buys WHERE status IN ({marks}) ORDER BY created_at DESC, id DESC LIMIT 1", BUY_COUNTED
    ).fetchone()


def insert_buy(conn: sqlite3.Connection, **fields: Any) -> int:
    now = hand.now()
    row = {"created_at": now, "updated_at": now, **fields}
    if row.get("status") not in BUY_STATUSES:
        raise hand.HandError(f"unknown buy status {row.get('status')!r}")
    names = ", ".join(row)
    cursor = conn.execute(f"INSERT INTO wallet_tiny_buys ({names}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    return int(cursor.lastrowid)


def update_buy(conn: sqlite3.Connection, buy_id: int, **fields: Any) -> None:
    allowed = {"status", "quote_id", "signature", "error"}
    if set(fields) - allowed:
        raise hand.HandError(f"cannot set {', '.join(sorted(set(fields) - allowed))} on a buy")
    if "status" in fields and fields["status"] not in BUY_STATUSES:
        raise hand.HandError(f"unknown buy status {fields['status']!r}")
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE wallet_tiny_buys SET {sets}, updated_at = ? WHERE id = ?", (*fields.values(), hand.now(), buy_id))


def buy(conn: sqlite3.Connection, buy_id: int) -> dict[str, Any] | None:
    return row_dict(conn.execute("SELECT * FROM wallet_tiny_buys WHERE id = ?", (buy_id,)).fetchone())


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
