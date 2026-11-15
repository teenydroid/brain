"""The babysitter's ledger in state/hands.db.

  babysit_audits      one row per audit: the pull request, its head, the
                      verdict and the findings (rules and places, no text)
  babysit_injections  one row per prompt-injection sighting: who, where,
                      which rule and a digest of the span, never the span
  babysit_actions     every write that happened: labels, the audit
                      comment, a merge

No row carries a pull request's words. The dream and the audit read these
tables, and a payload kept here would reach the model it was written for.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Iterable

from .. import hand

SCHEMA = """
CREATE TABLE IF NOT EXISTS babysit_audits (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        REAL NOT NULL,
    repo      TEXT NOT NULL,
    number    INTEGER NOT NULL,
    head_sha  TEXT,
    author    TEXT,
    tier      TEXT,
    verdict   TEXT NOT NULL,
    findings  TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS babysit_audits_pr ON babysit_audits (repo, number, at);
CREATE TABLE IF NOT EXISTS babysit_injections (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        REAL NOT NULL,
    repo      TEXT NOT NULL,
    number    INTEGER NOT NULL,
    head_sha  TEXT,
    who       TEXT,
    tier      TEXT,
    rule      TEXT NOT NULL,
    location  TEXT NOT NULL,
    digest    TEXT NOT NULL,
    UNIQUE (repo, number, who, rule, location, digest)
);
CREATE INDEX IF NOT EXISTS babysit_injections_at ON babysit_injections (at);
CREATE TABLE IF NOT EXISTS babysit_actions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        REAL NOT NULL,
    verb      TEXT NOT NULL,
    repo      TEXT NOT NULL,
    number    INTEGER NOT NULL,
    head_sha  TEXT,
    detail    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS babysit_actions_verb_at ON babysit_actions (verb, at);
"""


def connect():
    return hand.open_db(SCHEMA)


def record_audit(result: dict[str, Any], injections: Iterable[dict[str, Any]]) -> int | None:
    """The audit row, and each injection sighting not seen before. Logged,
    never raised: a broken ledger must not turn a verdict into an error."""
    try:
        conn = connect()
        try:
            with hand.transaction(conn):
                cur = conn.execute(
                    "INSERT INTO babysit_audits (at, repo, number, head_sha, author, tier, verdict, findings)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (hand.now(), result["repo"], result["number"], result.get("head_sha"), result.get("author"),
                     result.get("tier"), result["verdict"], json.dumps(result.get("findings", []), sort_keys=True)),
                )
                for hit in injections:
                    conn.execute(
                        "INSERT OR IGNORE INTO babysit_injections"
                        " (at, repo, number, head_sha, who, tier, rule, location, digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (hand.now(), result["repo"], result["number"], result.get("head_sha"), hit.get("who"),
                         hit.get("tier"), hit["rule"], hit["where"], hit["digest"]),
                    )
                return int(cur.lastrowid)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"hands/babysit: could not record the audit: {type(exc).__name__}", file=sys.stderr)
        return None


def record_action(verb: str, repo: str, number: int, head_sha: str | None, **detail: Any) -> None:
    """The row for a write that happened. Logged, never raised: an error now
    would invite a retry of something already done."""
    try:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO babysit_actions (at, verb, repo, number, head_sha, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (hand.now(), verb, repo, number, head_sha, json.dumps(detail, sort_keys=True)),
            )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"hands/babysit: did {verb} on {repo}#{number} but could not record it: {type(exc).__name__}",
              file=sys.stderr)


def acted(verb: str, repo: str, number: int, head_sha: str | None) -> bool:
    """Whether `verb` already happened on this pull request at this head."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM babysit_actions WHERE verb = ? AND repo = ? AND number = ? AND head_sha IS ? LIMIT 1",
            (verb, repo, number, head_sha),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def count_since(verb: str, since: float) -> int:
    conn = connect()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM babysit_actions WHERE verb = ? AND at >= ?",
                                (verb, since)).fetchone()[0])
    finally:
        conn.close()


def injections(days: int, max_rows: int) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT at, repo, number, head_sha, who, tier, rule, location, digest FROM babysit_injections"
            " WHERE at >= ? ORDER BY at DESC, id DESC LIMIT ?",
            (hand.now() - days * 86400, max_rows),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def audits(max_rows: int) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, at, repo, number, head_sha, author, tier, verdict, findings FROM babysit_audits"
            " ORDER BY at DESC, id DESC LIMIT ?",
            (max_rows,),
        ).fetchall()
        return [{**dict(row), "findings": json.loads(row["findings"])} for row in rows]
    finally:
        conn.close()
