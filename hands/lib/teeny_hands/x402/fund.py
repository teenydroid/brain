"""Keeping the x402 rail funded (docs/hands.md, "x402")."""

from __future__ import annotations

from typing import Any, Callable

from .. import hand
from . import settings

Core = Callable[..., Any]
Json = dict[str, Any]

#: What the pinned job answers while X402_FUND is not set.
OFF: Json = {"skipped": "x402 funding is off (X402_FUND=1 in secrets/teeny.env turns it on)"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS x402_fund_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    at              REAL NOT NULL,
    base_before_micro INTEGER NOT NULL,
    need_micro      INTEGER NOT NULL,
    source          TEXT,
    kind            TEXT,
    amount          TEXT,
    usd_micro       INTEGER NOT NULL DEFAULT 0,
    conversion_id   INTEGER,
    outcome         TEXT NOT NULL CHECK (outcome IN ('sent', 'in_doubt', 'failed', 'refused', 'no_source')),
    rule            TEXT,
    detail          TEXT
);
CREATE INDEX IF NOT EXISTS x402_fund_runs_at ON x402_fund_runs (at);
"""


def db():
    return hand.open_db(SCHEMA)


def fund(core: Core = hand.core) -> Json:
    if not settings.fund_on(hand.secrets()):
        return dict(OFF)
    raise hand.HandError("not yet")


def runs(max_rows: int) -> list[Json]:
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM x402_fund_runs ORDER BY id DESC LIMIT ?", (max_rows,)).fetchall()
    finally:
        conn.close()
    return [{k: r[k] for k in r.keys() if r[k] is not None} for r in rows]


def status() -> Json:
    raise hand.HandError("not yet")


def log(max_rows: int) -> Json:
    from ..wallet import x402 as wallet_x402

    return {"requests": wallet_x402.log(max_rows)["requests"], "fund_runs": runs(max_rows)}
