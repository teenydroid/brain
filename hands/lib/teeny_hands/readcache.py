"""A read-through cache in state/hands.db, for the hands that came after
hands/gh (which keeps its own `gh_cache`): one table per hand, keyed on a
digest of the kind, the arguments and the hand's key (another key can see
other things). A store that cannot be read or written is a miss, never a
failed read; an error is never cached; writes never come near it."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from typing import Any, Callable, Mapping

from . import hand

_MISS = object()
_TABLE = re.compile(r"^[a-z][a-z0-9_]*_cache$")


class ReadCache:
    """`table` (`<hand>_cache`), the secret whose digest scopes it, the TTL of
    each kind in seconds, and the setting that turns it off."""

    def __init__(self, table: str, key_setting: str, ttl: Mapping[str, float], enabled_setting: str) -> None:
        if not _TABLE.match(table):
            raise ValueError(f"bad cache table name {table!r}")
        self.table = table
        self.key_setting = key_setting
        self.ttl = dict(ttl)
        self.enabled_setting = enabled_setting
        self.schema = (
            f"CREATE TABLE IF NOT EXISTS {table} (key TEXT PRIMARY KEY, kind TEXT NOT NULL,"
            " value TEXT NOT NULL, expires_at REAL NOT NULL);"
        )

    def _key(self, kind: str, parts: tuple[Any, ...]) -> str:
        scope = hashlib.sha256((hand.setting(self.key_setting) or "").encode()).hexdigest()[:16]
        raw = json.dumps([kind, scope, *parts], default=str)
        return f"{kind}:{hashlib.sha256(raw.encode()).hexdigest()[:32]}"

    def _warn(self, message: str) -> None:
        print(f"hands/{self.table[: -len('_cache')]}: {message}", file=sys.stderr)

    def _read(self, key: str) -> Any:
        try:
            conn = hand.open_db(self.schema)
            try:
                row = conn.execute(
                    f"SELECT value FROM {self.table} WHERE key = ? AND expires_at > ?", (key, hand.now())
                ).fetchone()
            finally:
                conn.close()
            return _MISS if row is None else json.loads(row[0])
        except (sqlite3.Error, ValueError) as exc:
            self._warn(f"cache unreadable ({type(exc).__name__}); asking the service")
            return _MISS

    def _write(self, key: str, kind: str, value: Any) -> None:
        try:
            conn = hand.open_db(self.schema)
            try:
                at = hand.now()
                conn.execute(
                    f"INSERT OR REPLACE INTO {self.table} (key, kind, value, expires_at) VALUES (?, ?, ?, ?)",
                    (key, kind, json.dumps(value, ensure_ascii=False), at + self.ttl[kind]),
                )
                conn.execute(f"DELETE FROM {self.table} WHERE expires_at <= ?", (at,))
            finally:
                conn.close()
        except sqlite3.Error as exc:
            self._warn(f"could not cache ({type(exc).__name__})")

    def cached(self, kind: str, parts: tuple[Any, ...], fetch: Callable[[], Any]) -> Any:
        """`fetch()`'s answer, from the store while it is fresh."""
        if not hand.flag(self.enabled_setting, True):
            return fetch()
        key = self._key(kind, parts)
        hit = self._read(key)
        if hit is not _MISS:
            return hit
        value = fetch()
        self._write(key, kind, value)
        return value
