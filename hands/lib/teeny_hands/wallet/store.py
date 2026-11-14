"""The wallet's tables in state/hands.db.

  wallet_payments  every payment the hand attempted, one row each, written
                   `pending` before the core is asked to prepare or execute,
                   then `paid` (with the signature), `in_doubt`, `failed`
                   (prepare failed: nothing could have moved) or `refused`
                   (the core's spend policy said no before signing)
  bills            fixed bills that keep Teeny running (a server, an API
                   plan), each in dollars a month or a year; operator-kept
  vendors          the only addresses a `vendor` payment may go to, each on
                   one chain; operator-kept
  wallet_tiny_buys every run of `buy-tiny` (buy.py): `skipped` with its
                   reason, or `pending` before the core is asked, then
                   `bought` (with the signature), `failed` (nothing could
                   have moved) or `in_doubt`
  wallet_conversions  every swap and bridge the hand attempted, with its
                   provider (lifi or debridge) and LI.FI's `tool`: `pending`
                   before the core is asked to execute, then `sent` (with
                   the signature and, for a deBridge bridge, its order id),
                   `in_doubt`, `failed` or `refused`. `recipient` is NULL
                   for Teeny's own address; set, the bridge paid an
                   allowlisted vendor and has its own `wallet_payments` row
                   (convert.py, "Paying a vendor through a bridge")
  x402_requests    every paid HTTP request (x402) the hand attempted:
                   `pending`, then `paid`, `free` (no 402), `in_doubt`,
                   `failed` or `refused`
  x402_hosts       the only hosts an x402 payment may go to; operator-kept

Older checkouts made `wallet_payments` before it had a chain and before ETH,
and before the `refund` and `bill` purposes (docs/roadmap.md, S3 and S5):
`db()` rebuilds it once, every old row kept (on `solana` when it had no chain), and gives `vendors` its
chain column the same way. `wallet_conversions` from before LI.FI gains its
`provider` and `tool` columns, every old row a deBridge one, and its
`recipient` column (NULL, Teeny's own address, on every old row).

A `pending`, `in_doubt` or `paid` row counts against every cap, and a
purpose and ref with such a row is never paid again: an in-doubt payment
is looked at by a person, never retried by the hand. A `pending`,
`in_doubt` or `bought` buy counts against the per-payment and daily caps
the same way, beside the payments.
"""

from __future__ import annotations

import sqlite3
import time
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
    chain       TEXT NOT NULL DEFAULT 'solana',
    asset       TEXT NOT NULL CHECK (asset IN ('SOL', 'ETH', 'USDC')),
    amount_raw  INTEGER NOT NULL CHECK (amount_raw > 0),
    usd_micro   INTEGER NOT NULL,
    to_address  TEXT NOT NULL,
    purpose     TEXT NOT NULL CHECK (purpose IN ('bounty', 'vendor', 'refund', 'bill')),
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
    chain       TEXT NOT NULL DEFAULT 'solana',
    note        TEXT,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS wallet_conversions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('swap', 'bridge')),
    from_chain      TEXT NOT NULL,
    from_asset      TEXT NOT NULL,
    amount_raw      TEXT NOT NULL,
    usd_micro       INTEGER NOT NULL,
    to_chain        TEXT NOT NULL,
    to_asset        TEXT NOT NULL,
    expected_raw    TEXT,
    quote_id        TEXT,
    approval_tx     TEXT,
    signature       TEXT,
    order_id        TEXT,
    provider        TEXT,
    tool            TEXT,
    recipient       TEXT,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'in_doubt', 'failed', 'refused')),
    error           TEXT
);
CREATE INDEX IF NOT EXISTS wallet_conversions_created ON wallet_conversions (created_at);
CREATE TABLE IF NOT EXISTS x402_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    host            TEXT NOT NULL,
    url             TEXT NOT NULL,
    max_micro       INTEGER NOT NULL,
    usd_micro       INTEGER NOT NULL DEFAULT 0,
    network         TEXT,
    recipient       TEXT,
    payment_id      TEXT,
    http_status     INTEGER,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'free', 'in_doubt', 'failed', 'refused')),
    error           TEXT
);
CREATE INDEX IF NOT EXISTS x402_requests_created ON x402_requests (created_at);
CREATE TABLE IF NOT EXISTS x402_hosts (
    host        TEXT PRIMARY KEY,
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


_PAYMENT_COLUMNS = (
    "id, created_at, updated_at, cluster, chain, asset, amount_raw, usd_micro,"
    " to_address, purpose, ref, status, quote_id, signature, error"
)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _purposes_current(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'wallet_payments'").fetchone()
    return row is None or "'bill'" in (row[0] or "")


def _migrate(conn: sqlite3.Connection) -> None:
    """Brings tables made by an older hand up to SCHEMA, once."""
    if "chain" not in _columns(conn, "wallet_payments") or not _purposes_current(conn):
        # The asset CHECK cannot be altered in place: rebuild the table.
        with hand.transaction(conn):
            has_chain = "chain" in _columns(conn, "wallet_payments")
            if not has_chain or not _purposes_current(conn):
                create = SCHEMA.split("CREATE TABLE IF NOT EXISTS wallet_payments", 1)[1].split(");", 1)[0] + ");"
                conn.execute("CREATE TABLE wallet_payments_new" + create)
                old = _PAYMENT_COLUMNS if has_chain else _PAYMENT_COLUMNS.replace(" chain,", " 'solana',")
                conn.execute(f"INSERT INTO wallet_payments_new ({_PAYMENT_COLUMNS}) SELECT {old} FROM wallet_payments")
                conn.execute("DROP TABLE wallet_payments")
                conn.execute("ALTER TABLE wallet_payments_new RENAME TO wallet_payments")
        conn.executescript(SCHEMA)  # the indexes the drop took with it
    if "chain" not in _columns(conn, "vendors"):
        _add_column(conn, "vendors", "chain TEXT NOT NULL DEFAULT 'solana'")
    if "provider" not in _columns(conn, "wallet_conversions"):
        with hand.transaction(conn):
            if "provider" not in _columns(conn, "wallet_conversions"):
                conn.execute("ALTER TABLE wallet_conversions ADD COLUMN provider TEXT")
                conn.execute("ALTER TABLE wallet_conversions ADD COLUMN tool TEXT")
                conn.execute("UPDATE wallet_conversions SET provider = 'debridge'")
    if "recipient" not in _columns(conn, "wallet_conversions"):
        _add_column(conn, "wallet_conversions", "recipient TEXT")


def _add_column(conn: sqlite3.Connection, table: str, column: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
    except sqlite3.OperationalError as exc:  # another hand added it first
        if "duplicate column" not in str(exc):
            raise


def db() -> sqlite3.Connection:
    for attempt in range(20):
        try:
            conn = hand.open_db(SCHEMA)
            _migrate(conn)
            return conn
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or attempt == 19:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys() if row[k] is not None}


def _counted() -> str:
    return ",".join("?" * len(COUNTED))


def spent_since(conn: sqlite3.Connection, since: float, to_address: str | None = None) -> int:
    """Micro-dollars in counted rows created after `since` (to one address,
    on every chain; an EVM address matches without case). Without an address
    the $TINY buys count too: a buy is money out of the wallet like any
    payment, it only goes to Teeny's own holding."""
    query = f"SELECT COALESCE(SUM(usd_micro), 0) FROM wallet_payments WHERE status IN ({_counted()}) AND created_at > ?"
    params: list[Any] = [*COUNTED, since]
    if to_address is not None:
        if to_address.startswith("0x"):
            query += " AND lower(to_address) = lower(?)"
        else:
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


#: Conversions and x402 requests that may have moved funds.
CONVERSION_COUNTED = ("pending", "sent", "in_doubt")
X402_COUNTED = ("pending", "paid", "in_doubt")


def converted_since(conn: sqlite3.Connection, since: float) -> int:
    marks = ",".join("?" * len(CONVERSION_COUNTED))
    return int(conn.execute(
        f"SELECT COALESCE(SUM(usd_micro), 0) FROM wallet_conversions WHERE status IN ({marks}) AND created_at > ?",
        (*CONVERSION_COUNTED, since),
    ).fetchone()[0])


def x402_since(conn: sqlite3.Connection, since: float) -> int:
    """Micro-dollars of x402 requests after `since`: a pending or in-doubt
    one counts at its cap, a paid one at what it paid."""
    marks = ",".join("?" * len(X402_COUNTED))
    return int(conn.execute(
        f"SELECT COALESCE(SUM(CASE WHEN status = 'paid' THEN usd_micro ELSE max_micro END), 0) FROM x402_requests"
        f" WHERE status IN ({marks}) AND created_at > ?",
        (*X402_COUNTED, since),
    ).fetchone()[0])


def insert_row(conn: sqlite3.Connection, table: str, **fields: Any) -> int:
    if table not in ("wallet_conversions", "x402_requests"):
        raise hand.HandError(f"no table {table}")
    now = hand.now()
    fields = {"created_at": now, "updated_at": now, "status": "pending", **fields}
    names = ", ".join(fields)
    cursor = conn.execute(f"INSERT INTO {table} ({names}) VALUES ({', '.join('?' * len(fields))})", tuple(fields.values()))
    return int(cursor.lastrowid)


_UPDATABLE = {
    "wallet_conversions": {"status", "quote_id", "approval_tx", "signature", "order_id", "expected_raw", "tool", "error"},
    "x402_requests": {"status", "usd_micro", "network", "recipient", "payment_id", "http_status", "error"},
}


def update_row(conn: sqlite3.Connection, table: str, row_id: int, **fields: Any) -> None:
    allowed = _UPDATABLE.get(table)
    if allowed is None or set(fields) - allowed:
        raise hand.HandError(f"cannot set {', '.join(sorted(set(fields) - (allowed or set())))} on {table}")
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE {table} SET {sets}, updated_at = ? WHERE id = ?", (*fields.values(), hand.now(), row_id))


def active_for_ref(conn: sqlite3.Connection, purpose: str, ref: str) -> sqlite3.Row | None:
    """A counted row for this purpose and ref: it was paid, or may have been."""
    return conn.execute(
        f"SELECT id, status, signature FROM wallet_payments WHERE purpose = ? AND ref = ? AND status IN ({_counted()}) ORDER BY id LIMIT 1",
        (purpose, ref, *COUNTED),
    ).fetchone()


def insert_pending(
    conn: sqlite3.Connection, *, cluster: str, asset: str, amount_raw: int, usd_micro: int, to_address: str, purpose: str, ref: str,
    chain: str = "solana",
) -> int:
    now = hand.now()
    cursor = conn.execute(
        "INSERT INTO wallet_payments (created_at, updated_at, cluster, chain, asset, amount_raw, usd_micro, to_address, purpose, ref, status)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
        (now, now, cluster, chain, asset, amount_raw, usd_micro, to_address, purpose, ref),
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


def vendor_by_address(conn: sqlite3.Connection, address: str, chain: str | None = None) -> sqlite3.Row | None:
    """The vendor allowlisted at `address` (on `chain`, when given). An EVM
    address matches without case."""
    query = "SELECT name, address, chain, note FROM vendors WHERE "
    query += "lower(address) = lower(?)" if address.startswith("0x") else "address = ?"
    params: list[Any] = [address]
    if chain is not None:
        query += " AND chain = ?"
        params.append(chain)
    return conn.execute(query, params).fetchone()
