"""The giveaway tables, in state/hands.db.

  giveaway_rounds        one row per round: its format, pool, winners and
                         best-reply prize (atomic $TINY), the prompt, the
                         deadline, its X post, the judge's pick, the seed, and
                         once settled the tree and the distribution
  giveaway_entries       one row per account that replied to the round post
                         before the deadline: its first reply, the address it
                         bound, whether the guard held it, and what the
                         webhook said about the account
  giveaway_draws         the draw's verdict on every entrant: eligible or
                         not and why, its place in the draw order, and for a
                         winner the kind, the amount, the leaf and its proof
  giveaway_publications  every round file pushed to the diary, with its
                         sha256 (bin/lib/receipts.py makes each a receipt)

Statuses: open -> drawn -> settled -> clawed_back, or open -> void (nothing
to pay: a milestone not reached, or nobody eligible). The diary shows a void
round as `drawn` with no claims (docs/drops-schema.md has no `void`).
"""

from __future__ import annotations

import re
import sqlite3

from .. import hand

SCHEMA = """
CREATE TABLE IF NOT EXISTS giveaway_rounds (
    id                 INTEGER PRIMARY KEY,
    created_at         REAL NOT NULL,
    format             TEXT NOT NULL CHECK (format IN ('drop', 'milestone', 'challenge')),
    pool_raw           INTEGER NOT NULL CHECK (pool_raw > 0),
    winners            INTEGER NOT NULL CHECK (winners >= 0),
    best_reply_raw     INTEGER NOT NULL CHECK (best_reply_raw >= 0),
    prompt             TEXT NOT NULL,
    deadline           REAL NOT NULL,
    threshold          INTEGER,
    cluster            TEXT NOT NULL CHECK (cluster IN ('devnet', 'mainnet')),
    post_id            TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'open'
                       CHECK (status IN ('open', 'drawn', 'void', 'settled', 'clawed_back')),
    best_entry_id      INTEGER,
    judge_reasons      TEXT,
    judged_at          REAL,
    judge_post_id      TEXT,
    best_skipped       TEXT,
    outcome            TEXT,
    drawn_at           REAL,
    seed_slot          INTEGER,
    seed_blockhash     TEXT,
    seed_sha256        TEXT,
    lookups_used       INTEGER NOT NULL DEFAULT 0,
    merkle_root        TEXT,
    total_raw          INTEGER,
    clawback_at        REAL,
    authority          TEXT,
    distribution_seed  TEXT,
    distributor        TEXT,
    vault              TEXT,
    tx_create          TEXT,
    tx_fund            TEXT,
    tx_clawback        TEXT,
    settled_at         REAL,
    clawed_back_at     REAL,
    published_sha256   TEXT,
    published_at       REAL
);
CREATE TABLE IF NOT EXISTS giveaway_entries (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id           INTEGER NOT NULL REFERENCES giveaway_rounds (id),
    user_id            TEXT NOT NULL,
    handle             TEXT NOT NULL,
    post_id            TEXT NOT NULL,
    text               TEXT NOT NULL,
    created_at         REAL NOT NULL,
    address            TEXT,
    address_post_id    TEXT,
    held               INTEGER NOT NULL DEFAULT 0,
    unscreened         INTEGER NOT NULL DEFAULT 0,
    author_created_at  TEXT,
    author_followers   INTEGER,
    author_verified    INTEGER,
    collected_at       REAL NOT NULL,
    updated_at         REAL NOT NULL,
    UNIQUE (round_id, user_id)
);
CREATE TABLE IF NOT EXISTS giveaway_draws (
    round_id     INTEGER NOT NULL REFERENCES giveaway_rounds (id),
    entry_id     INTEGER NOT NULL REFERENCES giveaway_entries (id),
    handle       TEXT NOT NULL,
    eligible     INTEGER NOT NULL,
    reason       TEXT NOT NULL,
    position     INTEGER,
    kind         TEXT CHECK (kind IN ('random', 'best_reply')),
    address      TEXT,
    amount_raw   INTEGER,
    leaf_index   INTEGER,
    proof        TEXT,
    PRIMARY KEY (round_id, entry_id)
);
CREATE TABLE IF NOT EXISTS giveaway_publications (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id  INTEGER NOT NULL REFERENCES giveaway_rounds (id),
    status    TEXT NOT NULL,
    sha256    TEXT NOT NULL,
    at        REAL NOT NULL,
    UNIQUE (round_id, sha256)
);
"""

STATUSES = ("open", "drawn", "void", "settled", "clawed_back")
FORMATS = ("drop", "milestone", "challenge")
HOUR = 3600.0
DAY = 86400.0


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def tag(round_id: int) -> str:
    """What every post about the round carries: "drop #N"."""
    return f"drop #{int(round_id)}"


def tag_pattern(round_id: int) -> re.Pattern[str]:
    """The tag, not a longer number that starts with it (#3 is not #30)."""
    return re.compile(r"\bdrop\s*#" + str(int(round_id)) + r"(?!\d)", re.IGNORECASE)


def get(conn: sqlite3.Connection, round_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM giveaway_rounds WHERE id = ?", (round_id,)).fetchone()


def next_id(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM giveaway_rounds").fetchone()[0])
