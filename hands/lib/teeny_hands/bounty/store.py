"""The bounty tables, and the one question the wallet asks of them.

  bounties        one row per bounty: what it is, the prize (USDC, atomic),
                  the deadline, its tag ("bounty #3", which every post about
                  it carries so the hand can find those posts), the posts it
                  was announced in, the winner and when that became public,
                  the address the winner claimed and from which reply, and
                  the payment
  bounty_entries  replies to the bounty's X post and comments on its Moltbook
                  post, one row each, unique per (bounty, channel, source id)

Statuses: open -> picked -> claimed -> paid, or in_doubt when the payment
might have gone out and a person has to look. `payable` is what hands/wallet
asks before a `bounty` payment, from these rows alone, so the wallet does
not take the bounty hand's word for anything.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from .. import hand

SCHEMA = """
CREATE TABLE IF NOT EXISTS bounties (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        REAL NOT NULL,
    title             TEXT NOT NULL,
    rules             TEXT NOT NULL,
    prize_raw         INTEGER NOT NULL CHECK (prize_raw > 0),
    deadline          REAL NOT NULL,
    channels          TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open', 'picked', 'claimed', 'paid', 'in_doubt')),
    x_post_id         TEXT,
    moltbook_post_id  TEXT,
    winner_entry_id   INTEGER,
    reasons           TEXT,
    picked_at         REAL,
    public_at         REAL,
    winner_post_id    TEXT,
    claim_address     TEXT,
    claim_source_id   TEXT,
    claimed_at        REAL,
    payment_id        INTEGER,
    paid_at           REAL
);
CREATE TABLE IF NOT EXISTS bounty_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    bounty_id     INTEGER NOT NULL REFERENCES bounties (id),
    channel       TEXT NOT NULL CHECK (channel IN ('x', 'moltbook')),
    source_id     TEXT NOT NULL,
    author        TEXT NOT NULL,
    text          TEXT NOT NULL,
    created_at    REAL,
    collected_at  REAL NOT NULL,
    late          INTEGER NOT NULL DEFAULT 0,
    held          INTEGER NOT NULL DEFAULT 0,
    UNIQUE (bounty_id, channel, source_id)
);
CREATE INDEX IF NOT EXISTS bounty_entries_bounty ON bounty_entries (bounty_id, channel);
"""

HOUR = 3600.0


def tag(bounty_id: int) -> str:
    """What every post about the bounty carries, so the hand can find it."""
    return f"bounty #{bounty_id}"


def tag_pattern(bounty_id: int) -> re.Pattern[str]:
    """The tag, not a longer number that starts with it (#3 is not #30)."""
    return re.compile(r"\bbounty\s*#" + str(int(bounty_id)) + r"(?!\d)", re.IGNORECASE)


def challenge_hours() -> int:
    """How long a winner is public before the prize goes out: anybody can
    object in that time. BOUNTY_CHALLENGE_HOURS, 12 unless set (1 to 168)."""
    return hand.number("BOUNTY_CHALLENGE_HOURS", 12, 1, 168)


def get(conn: sqlite3.Connection, bounty_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM bounties WHERE id = ?", (bounty_id,)).fetchone()


def payable(conn: sqlite3.Connection, ref: str, to: str, asset: str, raw: int) -> dict[str, Any]:
    """Whether a `bounty` payment of `raw` `asset` to `to` for bounty `ref`
    may go out now. Refused with rule `bounty` and the reason otherwise."""
    if not re.fullmatch(r"[1-9][0-9]{0,9}", ref or ""):
        raise hand.Refused("a bounty payment's ref is the bounty's id", rule="bounty")
    row = get(conn, int(ref))
    if row is None:
        raise hand.Refused(f"there is no bounty {ref}", rule="bounty")
    if row["status"] in ("paid", "in_doubt") or row["payment_id"] is not None:
        raise hand.Refused(f"bounty {ref} has already been paid (or may have been)", rule="bounty", status=row["status"])
    if row["winner_entry_id"] is None:
        raise hand.Refused(f"bounty {ref} has no winner yet", rule="bounty")
    if row["public_at"] is None:
        raise hand.Refused(f"the winner of bounty {ref} has not been announced in public yet", rule="bounty")
    due = row["public_at"] + challenge_hours() * HOUR
    if hand.now() < due:
        raise hand.Refused(
            f"bounty {ref}'s winner has been public for less than {challenge_hours()} hours",
            rule="challenge_window",
            wait_seconds=int(due - hand.now()) + 1,
        )
    if not row["claim_address"]:
        raise hand.Refused(f"the winner of bounty {ref} has not claimed it with an address", rule="bounty")
    if to != row["claim_address"]:
        raise hand.Refused("that is not the address the winner claimed with", rule="claimed_address")
    if asset != "USDC" or raw != row["prize_raw"]:
        raise hand.Refused(f"bounty {ref}'s prize is exactly {row['prize_raw']} USDC atomic units", rule="prize")
    return {"bounty_id": row["id"], "title": row["title"]}
