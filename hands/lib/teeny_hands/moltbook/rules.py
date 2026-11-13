"""The rules a Moltbook write must pass before Moltbook hears of it, and the
ledger they read.

Moltbook's published limits (skill.md, "Rate Limits"), kept here so Teeny
is refused locally with the wait instead of collecting 429s:

  writes    30 per 60s (every post, comment, upvote, follow, subscribe)
  posts     1 per 30 min; a new agent (first 24h) 1 per 2 hours
  comments  1 per 20s and 50 a day; a new agent 1 per 60s and 20 a day

"New" is the 24 hours after `MOLTBOOK_CLAIMED_AT` (epoch seconds or an ISO
date), else after the first post Moltbook accepted from this ledger, and an
account with neither is treated as new. Our own caps sit on top:
`MOLTBOOK_MAX_POSTS_PER_DAY` (3) and `MOLTBOOK_MAX_COMMENTS_PER_DAY` (30). A
"day" is the last 24 hours, rolling.

Verification: a challenge Moltbook sets and nobody answers expires, and
Moltbook suspends an account whose last ten challenges all failed (wrong or
expired). So every challenge this hand did not answer correctly (a wrong
answer, an expiry, or one left pending because it could not be read with
confidence) adds one to `consecutive_failures`, a correct answer resets it,
and at LOCKOUT_AFTER (3) every write is refused until the operator runs
`hands/moltbook unlock`.

Collaboration: a post that says it is a collaboration ("collab",
"partnered with", "with u/<name>") is refused unless every agent it names
has a `collabs` row (`hands/moltbook collab add NAME --note ...`): consent
first, then the post.

Crypto: Moltbook removes crypto posts from submolts with allow_crypto=false,
so a post or comment that reads as crypto (or names $TINY) is refused for a
submolt whose info does not say allow_crypto=true.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from .. import hand

MINUTE = 60.0
HOUR = 3600.0
DAY = 86400.0

WRITES_PER_MINUTE = 30
POST_GAP = 30 * MINUTE
NEW_POST_GAP = 2 * HOUR
COMMENT_GAP = 20.0
NEW_COMMENT_GAP = 60.0
COMMENTS_PER_DAY = 50
NEW_COMMENTS_PER_DAY = 20
DEFAULT_MAX_POSTS_PER_DAY = 3
DEFAULT_MAX_COMMENTS_PER_DAY = 30
LOCKOUT_AFTER = 3

#: Ledger statuses of content Moltbook accepted (it counts against the limits
#: whether or not its challenge was answered).
COUNTED = ("posted", "verified", "needs_verification", "verification_failed", "created")
ACCEPTED = ("posted", "verified")

SCHEMA = """
CREATE TABLE IF NOT EXISTS moltbook_posts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id  TEXT,
    at       REAL NOT NULL,
    submolt  TEXT NOT NULL,
    title    TEXT NOT NULL,
    status   TEXT NOT NULL,
    detail   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS moltbook_posts_at ON moltbook_posts (at);
CREATE TABLE IF NOT EXISTS moltbook_comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id TEXT,
    post_id    TEXT NOT NULL,
    parent_id  TEXT,
    at         REAL NOT NULL,
    status     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS moltbook_comments_post ON moltbook_comments (post_id);
CREATE INDEX IF NOT EXISTS moltbook_comments_at ON moltbook_comments (at);
CREATE TABLE IF NOT EXISTS moltbook_actions (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     REAL NOT NULL,
    verb   TEXT NOT NULL,
    target TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}',
    UNIQUE (verb, target)
);
CREATE TABLE IF NOT EXISTS moltbook_pending (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    at                REAL NOT NULL,
    kind              TEXT NOT NULL,
    content_id        TEXT,
    verification_code TEXT NOT NULL,
    challenge_text    TEXT NOT NULL,
    expires_at        TEXT,
    status            TEXT NOT NULL,
    note              TEXT
);
CREATE TABLE IF NOT EXISTS moltbook_state (
    name  TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS moltbook_post_metrics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                TEXT NOT NULL CHECK (kind IN ('post', 'comment')),
    target_id           TEXT NOT NULL,
    checkpoint          INTEGER NOT NULL,
    at                  REAL NOT NULL,
    score               INTEGER,
    comment_count       INTEGER,
    verification_status TEXT,
    UNIQUE (kind, target_id, checkpoint)
);
CREATE INDEX IF NOT EXISTS moltbook_post_metrics_at ON moltbook_post_metrics (at);
CREATE TABLE IF NOT EXISTS moltbook_held (
    comment_id TEXT PRIMARY KEY,
    post_id    TEXT NOT NULL,
    hold_id    INTEGER,
    at         REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS collabs (
    name         TEXT PRIMARY KEY COLLATE NOCASE,
    consent_note TEXT NOT NULL,
    consented_at REAL NOT NULL
);
"""


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


# --- settings ------------------------------------------------------------------------


def max_posts_per_day() -> int:
    return hand.number("MOLTBOOK_MAX_POSTS_PER_DAY", DEFAULT_MAX_POSTS_PER_DAY, 0, 48)


def max_comments_per_day() -> int:
    return hand.number("MOLTBOOK_MAX_COMMENTS_PER_DAY", DEFAULT_MAX_COMMENTS_PER_DAY, 0, COMMENTS_PER_DAY)


def _claimed_at_setting() -> float | None:
    raw = hand.setting("MOLTBOOK_CLAIMED_AT")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise hand.HandError("MOLTBOOK_CLAIMED_AT must be epoch seconds or an ISO date") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def new_agent(conn: sqlite3.Connection) -> bool:
    """Whether Moltbook's first-24-hours limits apply."""
    claimed = _claimed_at_setting()
    if claimed is None:
        row = conn.execute(
            f"SELECT min(at) FROM moltbook_posts WHERE status IN ({','.join('?' * len(ACCEPTED))})", ACCEPTED
        ).fetchone()
        claimed = row[0]
    return claimed is None or hand.now() - float(claimed) < DAY


# --- the verification lockout ------------------------------------------------------------


def failures(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM moltbook_state WHERE name = 'consecutive_failures'").fetchone()
    return int(row[0]) if row else 0


def set_failures(conn: sqlite3.Connection, value: int) -> None:
    conn.execute(
        "INSERT INTO moltbook_state (name, value, at) VALUES ('consecutive_failures', ?, ?)"
        " ON CONFLICT(name) DO UPDATE SET value = excluded.value, at = excluded.at",
        (str(value), hand.now()),
    )


def check_lockout(conn: sqlite3.Connection) -> None:
    count = failures(conn)
    if count >= LOCKOUT_AFTER:
        raise hand.Refused(
            f"{count} verification challenges in a row were not answered correctly; every write waits for the"
            " operator (hands/moltbook unlock)",
            rule="verification_lockout",
            consecutive_failures=count,
        )


# --- the limits ---------------------------------------------------------------------------


def _counted(table: str) -> str:
    return f"SELECT {{what}} FROM {table} WHERE status IN ({','.join(repr(s) for s in COUNTED)})"


def check_write_rate(conn: sqlite3.Connection) -> None:
    since = hand.now() - MINUTE
    writes = sum(
        conn.execute(f"SELECT count(*) FROM {table} WHERE at > ?", (since,)).fetchone()[0]
        for table in ("moltbook_posts", "moltbook_comments", "moltbook_actions")
    )
    if writes >= WRITES_PER_MINUTE:
        raise hand.Refused(f"{writes} Moltbook writes in the last minute; the limit is {WRITES_PER_MINUTE}", rule="rate_limit", wait_seconds=60)


def _gap(conn: sqlite3.Connection, table: str, gap: float, what: str) -> None:
    last = conn.execute(_counted(table).format(what="max(at)")).fetchone()[0]
    if last is not None and hand.now() - float(last) < gap:
        wait = int(gap - (hand.now() - float(last))) + 1
        raise hand.Refused(f"one {what} per {int(gap // 60) or int(gap)} {'min' if gap >= 60 else 's'}; wait {wait}s", rule="rate_limit", wait_seconds=wait)


def _today(conn: sqlite3.Connection, table: str) -> int:
    sql = _counted(table).format(what="count(*)") + " AND at > ?"
    return int(conn.execute(sql, (hand.now() - DAY,)).fetchone()[0])


def check_post(conn: sqlite3.Connection) -> dict[str, Any]:
    check_lockout(conn)
    check_write_rate(conn)
    new = new_agent(conn)
    _gap(conn, "moltbook_posts", NEW_POST_GAP if new else POST_GAP, "post")
    cap = max_posts_per_day()
    done = _today(conn, "moltbook_posts")
    if done >= cap:
        raise hand.Refused(f"{done} Moltbook post(s) in the last 24 hours; the cap is {cap}", rule="daily_cap")
    return {"new_agent": new}


def check_comment(conn: sqlite3.Connection) -> dict[str, Any]:
    check_lockout(conn)
    check_write_rate(conn)
    new = new_agent(conn)
    _gap(conn, "moltbook_comments", NEW_COMMENT_GAP if new else COMMENT_GAP, "comment")
    cap = min(max_comments_per_day(), NEW_COMMENTS_PER_DAY if new else COMMENTS_PER_DAY)
    done = _today(conn, "moltbook_comments")
    if done >= cap:
        raise hand.Refused(f"{done} Moltbook comment(s) in the last 24 hours; the cap is {cap}", rule="daily_cap")
    return {"new_agent": new}


# --- collaboration consent ------------------------------------------------------------------

_COLLAB = re.compile(r"\bcollab\w*|\bco-?(?:authored|created|built|written)\b|\bpartner(?:ed|ing|ship)?\s+with\b", re.IGNORECASE)
_WITH_USER = re.compile(r"\b(?:with|ft\.?|feat\.?|featuring|alongside)\s+u/([A-Za-z0-9_-]{1,40})", re.IGNORECASE)
_USER = re.compile(r"(?<![A-Za-z0-9_/])u/([A-Za-z0-9_-]{1,40})")


def collab_names(text: str) -> tuple[bool, set[str]]:
    """Whether the text claims a collaboration, and the agents it names in it."""
    names = {m.group(1) for m in _WITH_USER.finditer(text)}
    claims = bool(_COLLAB.search(text))
    if claims:
        names |= {m.group(1) for m in _USER.finditer(text)}
    return claims or bool(names), names


def check_collab(conn: sqlite3.Connection, text: str) -> None:
    claims, names = collab_names(text)
    if not claims:
        return
    if not names:
        raise hand.Refused(
            "the post claims a collaboration but names nobody whose consent is on file; name them as u/<name>"
            " and record their consent first (hands/moltbook collab add)",
            rule="collab",
        )
    missing = sorted(n for n in names if not conn.execute("SELECT 1 FROM collabs WHERE name = ?", (n,)).fetchone())
    if missing:
        raise hand.Refused(
            "the post names a collaboration nobody consented to on record: " + ", ".join(f"u/{n}" for n in missing),
            rule="collab",
            names=missing,
        )


def collab_add(name: str, note: str) -> dict[str, Any]:
    conn = db()
    try:
        conn.execute(
            "INSERT INTO collabs (name, consent_note, consented_at) VALUES (?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET consent_note = excluded.consent_note, consented_at = excluded.consented_at",
            (name, note, hand.now()),
        )
    finally:
        conn.close()
    return {"collab": name, "consent_note": note}


def collab_list() -> dict[str, Any]:
    conn = db()
    try:
        rows = conn.execute("SELECT name, consent_note, consented_at FROM collabs ORDER BY name").fetchall()
    finally:
        conn.close()
    return {"collabs": [dict(r) for r in rows]}


def collab_remove(name: str) -> dict[str, Any]:
    conn = db()
    try:
        gone = conn.execute("DELETE FROM collabs WHERE name = ?", (name,)).rowcount
    finally:
        conn.close()
    if not gone:
        raise hand.Refused("no such collab on record", name=name)
    return {"removed": name}


# --- crypto -------------------------------------------------------------------------------------

_CRYPTO = re.compile(
    r"\$[A-Za-z]{2,10}\b"
    r"|\b(?:crypto\w*|blockchain\w*|bitcoin|btc|ethereum|eth|solana|nfts?|defi|web3|memecoins?|shitcoins?|altcoins?"
    r"|stablecoins?|airdrops?|tokenomics|on-?chain|staking|dex|pump\.fun|solscan|wallets?|hodl\w*)\b"
    r"|\b(?:my|our|the|a|this)\s+token\b(?!\s+(?:limit|count|window|budget)s?\b)",
    re.IGNORECASE,
)
#: Teeny's own ticker, in capitals: "tiny" in lower case is just a word.
_TICKER = re.compile(r"\bTINY\b")


def looks_crypto(texts: Iterable[str]) -> bool:
    return any(_CRYPTO.search(t) or _TICKER.search(t) for t in texts if t)


def check_crypto(info: Any, submolt: str) -> None:
    """Refuses crypto content for a submolt that does not allow it."""
    allow = info.get("allow_crypto") if isinstance(info, dict) else None
    if allow is not True:
        raise hand.Refused(
            f"m/{submolt} does not allow crypto content (allow_crypto is not true); Moltbook would remove it",
            rule="crypto",
            submolt=submolt,
        )
