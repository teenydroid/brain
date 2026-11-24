"""The $TINY candles: fetched from a public OHLC endpoint, kept in `chart_candles`.

Read-only public data: no key, no wallet, no trade. `CHART_API_BASE` in
secrets/teeny.env overrides the endpoint so tests can serve it from localhost.
"""
from __future__ import annotations

import json
import math
import sqlite3
import urllib.request
from typing import Any

from .. import hand

Row = tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS chart_candles (
    coin       TEXT NOT NULL,
    days       INTEGER NOT NULL,
    ts         INTEGER NOT NULL,
    open       REAL NOT NULL,
    high       REAL NOT NULL,
    low        REAL NOT NULL,
    close      REAL NOT NULL,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (coin, days, ts)
);
"""


def _api_base() -> str:
    return hand.setting("CHART_API_BASE") or "https://api.coingecko.com/api/v3"


def open_db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def fetch(coin: str, days: int) -> dict[str, Any]:
    """Fetch `days` of OHLC candles for `coin` and replace the stored window.

    Returns `{"coin", "days", "candles"}`. A bad answer (not a list, or a row
    that is not a five-tuple of numbers) raises HandError and stores nothing.
    """
    url = f"{_api_base()}/coins/{coin}/ohlc?vs_currency=usd&days={int(days)}"
    request = urllib.request.Request(url, headers={"accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read() or b"[]")
    except Exception as exc:
        raise hand.HandError(f"chart: fetch failed: {type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, list):
        raise hand.HandError(f"chart: expected a list of candles, got {type(payload).__name__}")
    rows: list[tuple] = []
    for row in payload:
        if (
            not isinstance(row, (list, tuple))
            or len(row) != 5
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in row)
        ):
            raise hand.HandError("chart: bad candle row from the endpoint")
        ts, open_, high, low, close = (float(v) for v in row)
        if not all(math.isfinite(v) for v in (ts, open_, high, low, close)):
            raise hand.HandError("chart: non-finite candle from the endpoint")
        if not (low <= high and low <= min(open_, close) <= max(open_, close) <= high):
            raise hand.HandError("chart: bad OHLC range from the endpoint")
        rows.append((int(ts), open_, high, low, close))
    with hand.db() as conn:
        conn.executescript(SCHEMA)
        with hand.transaction(conn):
            conn.execute("DELETE FROM chart_candles WHERE coin = ? AND days = ?", (coin, int(days)))
            conn.executemany(
                "INSERT INTO chart_candles (coin, days, ts, open, high, low, close, fetched_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(coin, days, ts, o, h, l, c, hand.now()) for ts, o, h, l, c in rows],
            )
    return {"coin": coin, "days": int(days), "candles": len(rows)}


def rows(coin: str, days: int) -> list[tuple]:
    """The stored candles for `coin` over `days`, oldest first."""
    with hand.db() as conn:
        conn.executescript(SCHEMA)
        return [
            (int(row[0]), row[1], row[2], row[3], row[4])
            for row in conn.execute(
                "SELECT ts, open, high, low, close FROM chart_candles"
                " WHERE coin = ? AND days = ? ORDER BY ts",
                (coin, int(days)),
            )
        ]
