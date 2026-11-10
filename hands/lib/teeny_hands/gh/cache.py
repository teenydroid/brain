"""A read-through cache in front of GitHub, in the `gh_cache` table of
state/hands.db, as hands/x keeps `x_cache`: the same reads recur (a repo's
policy before every PR to it, the ship log every morning), and GitHub
rate-limits a token. A store that cannot be read or written is a miss, never
a failed read; an error is never cached; writes never come near it.
`GH_CACHE_ENABLED=0` turns it off."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from typing import Any, Callable

from .. import hand
from . import open_db

MINUTE = 60.0
DAY = 86400.0

#: How long each kind of answer is trusted, in seconds.
TTL = {
    "whoami": DAY,
    "repo": 10 * MINUTE,
    "issues": 10 * MINUTE,
    "pr": 5 * MINUTE,
    "contributing": DAY,
    "activity": 10 * MINUTE,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gh_cache (
    key        TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    value      TEXT NOT NULL,
    expires_at REAL NOT NULL
);
"""

_MISS = object()


def _key(kind: str, parts: tuple[Any, ...]) -> str:
    # Keyed on a digest of the token too: another token can see other things.
    raw = json.dumps([kind, hashlib.sha256((hand.setting("GITHUB_TOKEN") or "").encode()).hexdigest()[:16], *parts])
    return f"{kind}:{hashlib.sha256(raw.encode()).hexdigest()[:32]}"


def _read(key: str) -> Any:
    try:
        conn = open_db(_SCHEMA)
        try:
            row = conn.execute("SELECT value FROM gh_cache WHERE key = ? AND expires_at > ?", (key, hand.now())).fetchone()
        finally:
            conn.close()
        return _MISS if row is None else json.loads(row[0])
    except (sqlite3.Error, ValueError) as exc:
        print(f"hands/gh: cache unreadable ({type(exc).__name__}); asking GitHub", file=sys.stderr)
        return _MISS


def _write(key: str, kind: str, value: Any) -> None:
    try:
        conn = open_db(_SCHEMA)
        try:
            at = hand.now()
            conn.execute(
                "INSERT OR REPLACE INTO gh_cache (key, kind, value, expires_at) VALUES (?, ?, ?, ?)",
                (key, kind, json.dumps(value, ensure_ascii=False), at + TTL[kind]),
            )
            conn.execute("DELETE FROM gh_cache WHERE expires_at <= ?", (at,))
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"hands/gh: could not cache ({type(exc).__name__})", file=sys.stderr)


def cached(kind: str, parts: tuple[Any, ...], fetch: Callable[[], Any]) -> Any:
    """`fetch()`'s answer, from the store while it is fresh."""
    if not hand.flag("GH_CACHE_ENABLED", True):
        return fetch()
    key = _key(kind, parts)
    hit = _read(key)
    if hit is not _MISS:
        return hit
    value = fetch()
    _write(key, kind, value)
    return value
