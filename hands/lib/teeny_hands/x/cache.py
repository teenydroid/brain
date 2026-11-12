"""A read-through cache in front of the X API, in the `x_cache` table of
state/hands.db. Ported from crates/teeny-x/src/cache.rs (itself
`legacy/src/clients/x-cache.ts`) with the same keys, kinds and TTLs.

X bills every post a read returns, and the same reads recur: a thread's root
once per mention under it, the same search from several jobs, a post the
agent was just shown in a search. A post's content never changes once
published, so a post seen through any read is kept a long while. Lists
(searches, a conversation) do change and are kept briefly: long enough to
absorb a job asking the same thing several times in one run, short enough
that nothing reads as stale.

Every read is also a single flight: callers asking the same thing at once
share one request. In the Rust that was an in-process table of futures; a
hand is one process per call, so here it is a flock per key bucket, and the
caller that waited re-reads the store before going to X. A store that cannot
be read or written is a miss, not a failed read. Writes never come near this
layer, and neither does a failed read: an error is not cached.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterator

from .. import hand
from . import open_db, twitr
from .client import XApi, number_setting
from .model import Json, SearchOptions

MINUTE = 60.0
HOUR = 3600.0
DAY = 86400.0

#: How long a caller waits for another's flight before asking X itself.
FLIGHT_WAIT_SECONDS = 45.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS x_cache (
    key        TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    value      TEXT NOT NULL,
    stored_at  REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS x_cache_expires ON x_cache (expires_at);
"""


@dataclass(frozen=True)
class Ttl:
    """How long each kind of answer is trusted, in seconds."""

    #: A post's content cannot change; the counters on it are a snapshot.
    post: float = 30 * DAY
    #: A post X said was not there: deleted, protected, or not yet visible.
    missing: float = HOUR
    search: float = 10 * MINUTE
    conversation: float = 10 * MINUTE
    #: The account the token acts as. Keyed on the token, so a re-auth is a
    #: miss; the day only bounds a rename.
    identity: float = DAY

    @classmethod
    def from_settings(cls) -> "Ttl":
        """`X_CACHE_POST_TTL_DAYS` for posts, `X_CACHE_SEARCH_TTL_MINUTES`
        for searches and conversations, the defaults for the rest."""
        post_days = number_setting("X_CACHE_POST_TTL_DAYS", 30.0, 0.0, 36500.0)
        search_minutes = number_setting("X_CACHE_SEARCH_TTL_MINUTES", 10.0, 0.0, 525600.0)
        return cls(post=post_days * DAY, search=search_minutes * MINUTE, conversation=search_minutes * MINUTE)

    def of(self, kind: str) -> float:
        return getattr(self, kind)


def digest(text: str) -> str:
    """The first 24 hex characters of the SHA-256 of `text`."""
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def _compact(value: Any) -> str:
    # serde_json::to_string: no spaces, non-ASCII as is.
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def search_key(query: str, options: SearchOptions, search_ttl: float) -> str:
    """The search key: a digest of the query and the options that change the
    answer, in the legacy key's JSON shape. A moving `start_time`/`end_time`
    ("now minus an hour") is rounded to the search TTL, so the same query
    asked twice inside one window is one request."""
    window_ms = int(search_ttl * 1000)

    def bucket(time: str | None) -> str | None:
        if time is None:
            return None
        try:
            parsed = datetime.fromisoformat(time.replace("Z", "+00:00"))
        except ValueError:
            return time
        if window_ms <= 0 or parsed.tzinfo is None:
            return time
        return str(int(parsed.timestamp() * 1000) // window_ms)

    key: Json = {"query": query}
    for name, value in (
        ("maxResults", options.max_results),
        ("sinceId", options.since_id),
        ("sortOrder", options.sort_order),
        ("startTime", bucket(options.start_time)),
        ("endTime", bucket(options.end_time)),
        ("paginationToken", options.pagination_token),
    ):
        if value is not None:
            key[name] = value
    return f"search:{digest(_compact(key))}"


_MISS = object()


class CachedReads:
    """The reads, each through `x_cache` first. Same methods as `XApi`'s
    reads, so a verb cannot tell which one it holds."""

    def __init__(self, x: XApi, ttl: Ttl) -> None:
        self._x = x
        self._ttl = ttl
        self._conn: sqlite3.Connection | None = None

    # --- the store ----------------------------------------------------------------------

    def _store(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = open_db(_SCHEMA)
        return self._conn

    def _read(self, key: str) -> Any:
        try:
            row = self._store().execute(
                "SELECT value FROM x_cache WHERE key = ? AND expires_at > ?", (key, hand.now())
            ).fetchone()
        except sqlite3.Error as exc:
            _warn(f"x cache: could not read ({type(exc).__name__}); asking X")
            return _MISS
        if row is None:
            return _MISS
        try:
            return json.loads(row[0])
        except ValueError:
            return _MISS

    def _write(self, key: str, kind: str, value: Any) -> None:
        stored_at = hand.now()
        try:
            conn = self._store()
            conn.execute(
                "INSERT OR REPLACE INTO x_cache (key, kind, value, stored_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (key, kind, json.dumps(value, ensure_ascii=False), stored_at, stored_at + self._ttl.of(kind)),
            )
            # The table keeps itself small: what has expired is never a hit.
            conn.execute("DELETE FROM x_cache WHERE expires_at <= ?", (stored_at,))
        except sqlite3.Error as exc:
            _warn(f"x cache: could not write ({type(exc).__name__})")

    def _remember(self, posts: list[Json]) -> None:
        """Every post a list brings back is a post already paid for; keeping
        each under its own key is what makes the next `get_post` free."""
        for post in posts:
            self._write(f"post:{post['id']}", "post", post)

    @contextlib.contextmanager
    def _flight(self, key: str) -> Iterator[None]:
        """Joins the flight already asking for `key`, or starts one: a flock
        on one of 256 buckets. Waiting too long, or a lock that cannot be
        taken at all, means asking X anyway; the flight saves money, it is
        not a rule."""
        held = hand.lock(f"x-read-{digest(key)[:2]}", timeout=FLIGHT_WAIT_SECONDS)
        try:
            held.__enter__()
        except (hand.HandError, OSError):
            yield
            return
        try:
            yield
        finally:
            held.__exit__(None, None, None)

    def _through(self, key: str, kind: str, fetch: Callable[[], Any]) -> Any:
        hit = self._read(key)
        if hit is not _MISS:
            return hit
        with self._flight(key):
            # Whoever held the flight may have just stored the answer.
            hit = self._read(key)
            if hit is not _MISS:
                return hit
            value = fetch()
            self._write(key, kind, value)
            return value

    # --- the reads ------------------------------------------------------------------------

    def _known_post(self, post_id: str) -> Any:
        """The stored post, None when X recently said it was not there, or
        `_MISS` when neither is known."""
        hit = self._read(f"post:{post_id}")
        if hit is not _MISS:
            return hit
        return None if self._read(f"missing:{post_id}") is not _MISS else _MISS

    def get_post(self, post_id: str) -> Json | None:
        known = self._known_post(post_id)
        if known is not _MISS:
            return known
        with self._flight(f"post:{post_id}"):
            known = self._known_post(post_id)
            if known is not _MISS:
                return known
            post = self._x.get_post(post_id)
            if post is None:
                self._write(f"missing:{post_id}", "missing", None)
            else:
                self._write(f"post:{post_id}", "post", post)
            return post

    def search_posts(self, query: str, options: SearchOptions) -> list[Json]:
        def fetch() -> list[Json]:
            posts = self._x.search_posts(query, options)
            self._remember(posts)
            return posts

        return self._through(search_key(query, options, self._ttl.search), "search", fetch)

    def get_conversation(self, conversation_id: str, max_results: int | None = None) -> list[Json]:
        def fetch() -> list[Json]:
            posts = self._x.get_conversation(conversation_id, max_results)
            self._remember(posts)
            return posts

        key = f"conversation:{conversation_id}:{'' if max_results is None else max_results}"
        return self._through(key, "conversation", fetch)

    def identity(self) -> Json:
        # Keyed on a digest of the access token: never the token itself, and
        # a token for another account is another key.
        key = f"identity:{digest(hand.require('X_ACCESS_TOKEN'))}"
        return self._through(key, "identity", self._x.identity)

    def user_by_username(self, username: str) -> Json | None:
        """Not kept: a profile changes, and a lookup is one user, not a bill
        per post."""
        return self._x.user_by_username(username)


def reads(x: XApi | None = None) -> CachedReads | XApi:
    """The reads the configuration asks for: through the store when
    `X_CACHE_ENABLED` is on (the default), straight to X otherwise."""
    x = x or twitr.client()
    if not hand.flag("X_CACHE_ENABLED", True):
        return x
    return CachedReads(x, Ttl.from_settings())


def remember(posts: list[Json]) -> None:
    """Posts that reached us without a read through this store (the mentions
    timeline, X's webhook) kept under their own keys, so replying to one
    (`publish.reply` looks its target up first) is not a second paid read.
    Needs no X keys; a store that cannot be written is only a later miss."""
    if not posts or not hand.flag("X_CACHE_ENABLED", True):
        return
    store = CachedReads(None, Ttl.from_settings())  # type: ignore[arg-type]
    store._remember([p for p in posts if p.get("id")])


def _warn(message: str) -> None:
    print(f"hands/x: {message}", file=sys.stderr)
