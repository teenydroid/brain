"""The babysitter's ledger in state/hands.db.

  babysit_audits      one row per audit: the pull request, its head, the
                      verdict and the findings (rules and places, no text)
  babysit_injections  one row per prompt-injection sighting: who, where,
                      which rule and a digest of the span, never the span
  babysit_actions     every write that happened: labels, the audit
                      comment, a merge (`merge`, or `self_merge` for one no
                      person approved), and each deploy step after a
                      self-merge (`deploy_wait`, `deploy_refused`,
                      `deploy_ff`, `deploy_submodules`, `deploy_seed_cron`,
                      `deploy_restart`, `deploy_dry_run`), and, with
                      BABYSIT_SELF_MERGE_ALL, the judgement of a deploy
                      after it (rollback.py: `deploy_ok`, `deploy_bad` with
                      the paths it changed, `deploy_revert` and
                      `deploy_revert_held` for its revert issue,
                      `rollback_dry_run`); and the babysitter's agent
                      (sitter.py): `sitter_job` laid out, `sitter_merge`,
                      `sitter_wait` and `sitter_close` decided, `sitter_push`
                      (its head_sha is the new commit), `sitter_reply`,
                      `sitter_resolve`, `sitter_failed`, `sitter_left` to the
                      operator and `sitter_left_comment`. What it wrote is in
                      state/sitter/decisions.jsonl, not here

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


def sitter_decision(repo: str, number: int, head_sha: str | None) -> str | None:
    """What the babysitter's agent last decided on this pull request at this
    head: `merge`, `wait`, `close`, or None."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT verb FROM babysit_actions WHERE verb IN ('sitter_merge', 'sitter_wait', 'sitter_close')"
            " AND repo = ? AND number = ? AND head_sha = ? ORDER BY id DESC LIMIT 1", (repo, number, head_sha)).fetchone()
        return row["verb"].removeprefix("sitter_") if row else None
    finally:
        conn.close()


def sitter_rows(repo: str, number: int) -> list[dict[str, Any]]:
    """Every `sitter_*` row of a pull request, newest first."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT at, verb, head_sha, detail FROM babysit_actions WHERE substr(verb, 1, 7) = 'sitter_'"
            " AND repo = ? AND number = ? ORDER BY id DESC", (repo, number)).fetchall()
        return [{**dict(row), "detail": json.loads(row["detail"] or "{}")} for row in rows]
    finally:
        conn.close()


def count_since(verbs: str | tuple[str, ...], since: float) -> int:
    verbs = (verbs,) if isinstance(verbs, str) else tuple(verbs)
    conn = connect()
    try:
        marks = ", ".join("?" for _ in verbs)
        return int(conn.execute(f"SELECT COUNT(*) FROM babysit_actions WHERE verb IN ({marks}) AND at >= ?",
                                (*verbs, since)).fetchone()[0])
    finally:
        conn.close()


def actions_since(verbs: str | tuple[str, ...], since: float) -> list[dict[str, Any]]:
    """The `babysit_actions` rows of `verbs` at or after `since`, newest
    first, each with its detail read back."""
    verbs = (verbs,) if isinstance(verbs, str) else tuple(verbs)
    conn = connect()
    try:
        marks = ", ".join("?" for _ in verbs)
        rows = conn.execute(f"SELECT id, at, verb, repo, number, head_sha, detail FROM babysit_actions"
                            f" WHERE verb IN ({marks}) AND at >= ? ORDER BY at DESC, id DESC", (*verbs, since)).fetchall()
        return [{**dict(row), "detail": json.loads(row["detail"] or "{}")} for row in rows]
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
