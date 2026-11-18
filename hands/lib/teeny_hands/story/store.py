"""The story bible: `storylines` and `story_beats` in state/hands.db.

A storyline is `active` or `retired`. Its `question_at` is when its open
question was last set (by the seed, or by a beat that brought a new one).
A beat is one post that moved a storyline: which post, its words, its
shape and format, the question it left open, and `refs`, the ledger rows
and files it was told from, as desk sources (`<table>:<key>`,
`evolve:<id>`, `file:<path>`, `gh:<owner/repo>#<n>`; audit.py resolves
them).

`seed` inserts the storylines of brain/narrative/STORYLINES.toml that are
not in the ledger yet and never touches one that is. `beat` records a post
already made; in a dry run that post never went out, so nothing is written.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .. import hand

Json = dict[str, Any]
DAY = 86400.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS storylines (
    key           TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    premise       TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT '',
    open_question TEXT,
    next          TEXT,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    question_at   REAL NOT NULL,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS story_beats (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    storyline     TEXT NOT NULL REFERENCES storylines (key),
    at            REAL NOT NULL,
    post_id       TEXT NOT NULL,
    surface       TEXT NOT NULL DEFAULT 'x',
    format        TEXT,
    shape         TEXT,
    text          TEXT NOT NULL,
    open_question TEXT,
    refs          TEXT NOT NULL DEFAULT '[]',
    UNIQUE (storyline, surface, post_id)
);
CREATE INDEX IF NOT EXISTS story_beats_storyline ON story_beats (storyline, at);
"""

#: Beats `state` shows per storyline, newest first.
LAST_BEATS = 3
#: Days an open question may wait before `due` names it.
DUE_DAYS = 3
#: The most storylines that may be active at once (the plan's four to six).
MAX_ACTIVE = 6
KEY = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
SURFACES = ("x", "moltbook", "reddit", "telegram", "web")
TEXT_CHARS = 4000
FIELD_CHARS = 500
MAX_REFS = 20
REF_CHARS = 300

SEED_FILE = Path("narrative") / "STORYLINES.toml"


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def _brain() -> Path:
    """The live brain the core boots (bin/lib/teeny.sh's TEENY_BRAIN)."""
    return Path(os.environ.get("OPENHUMAN_WORKSPACE") or hand.root() / "brain")


def iso(at: float | None) -> str | None:
    if at is None:
        return None
    return datetime.fromtimestamp(float(at), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(body: Mapping[str, Any], name: str, limit: int, required: bool = False) -> str | None:
    value = body.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise hand.HandError(f"{name} is required")
        return None
    if not isinstance(value, str):
        raise hand.HandError(f"{name} is a string")
    value = value.strip()
    if len(value) > limit:
        raise hand.HandError(f"{name} is over {limit} characters")
    return value


# --- seeds ------------------------------------------------------------------------------


def seeds(path: Path | None = None) -> list[Json]:
    """The storylines of the seed file, checked: each has a key, a title, a
    premise and an open question."""
    path = path or _brain() / SEED_FILE
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise hand.HandError(f"no seed file at {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise hand.HandError(f"{path.name} is not TOML: {exc}") from exc
    out = []
    for item in data.get("storyline") or []:
        if not isinstance(item, dict):
            raise hand.HandError(f"{path.name}: a storyline is a table")
        key = item.get("key")
        if not isinstance(key, str) or not KEY.match(key):
            raise hand.HandError(f"{path.name}: bad storyline key {key!r}")
        for field in ("title", "premise", "open_question"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise hand.HandError(f"{path.name}: {key} needs a {field}")
        out.append(item)
    if len({i["key"] for i in out}) != len(out):
        raise hand.HandError(f"{path.name}: a key is used twice")
    if len(out) > MAX_ACTIVE:
        raise hand.HandError(f"{path.name}: at most {MAX_ACTIVE} storylines")
    return out


def seed() -> Json:
    """Insert each seed storyline the ledger lacks; a storyline already there
    keeps its live state."""
    items = seeds()
    if hand.dry_run():
        # Read-only, so a rehearsal leaves no table and no row behind.
        have: set[str] = set()
        path = hand.state_dir() / "hands.db"
        if path.is_file():
            ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                if ro.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'storylines'").fetchone():
                    have = {r[0] for r in ro.execute("SELECT key FROM storylines")}
            finally:
                ro.close()
        return {"dry_run": True, "would_add": [i["key"] for i in items if i["key"] not in have],
                "kept": [i["key"] for i in items if i["key"] in have]}
    now = hand.now()
    conn = db()
    try:
        added = []
        with hand.transaction(conn):
            for item in items:
                done = conn.execute(
                    "INSERT OR IGNORE INTO storylines (key, title, premise, state, open_question, next, status,"
                    " question_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
                    (item["key"], item["title"].strip(), item["premise"].strip(), (item.get("state") or "").strip(),
                     item["open_question"].strip(), (item.get("next") or "").strip() or None, now, now, now),
                )
                if done.rowcount:
                    added.append(item["key"])
    finally:
        conn.close()
    return {"added": added, "kept": [i["key"] for i in items if i["key"] not in added]}


# --- reads ------------------------------------------------------------------------------


def _beat(row: sqlite3.Row) -> Json:
    try:
        refs = json.loads(row["refs"] or "[]")
    except ValueError:
        refs = []
    return {"id": row["id"], "storyline": row["storyline"], "at": iso(row["at"]), "post_id": row["post_id"],
            "surface": row["surface"], "format": row["format"], "shape": row["shape"], "text": row["text"],
            "open_question": row["open_question"], "refs": refs}


def beats(conn: sqlite3.Connection, key: str, limit: int | None = None) -> list[Json]:
    sql = "SELECT * FROM story_beats WHERE storyline = ? ORDER BY at DESC, id DESC"
    rows = conn.execute(sql + (" LIMIT ?" if limit else ""), (key, limit) if limit else (key,)).fetchall()
    return [_beat(r) for r in rows]


def _storyline(conn: sqlite3.Connection, row: sqlite3.Row) -> Json:
    count = conn.execute("SELECT count(*) FROM story_beats WHERE storyline = ?", (row["key"],)).fetchone()[0]
    return {"key": row["key"], "title": row["title"], "premise": row["premise"], "state": row["state"],
            "open_question": row["open_question"], "question_at": iso(row["question_at"]), "next": row["next"],
            "status": row["status"], "updated_at": iso(row["updated_at"]), "beats_total": count,
            "beats": beats(conn, row["key"], LAST_BEATS)}


def state(conn: sqlite3.Connection | None = None) -> Json:
    """Every active storyline with its last LAST_BEATS beats, oldest
    storyline first."""
    own = conn is None
    conn = conn or db()
    try:
        rows = conn.execute("SELECT * FROM storylines WHERE status = 'active' ORDER BY created_at, key").fetchall()
        return {"storylines": [_storyline(conn, r) for r in rows]}
    finally:
        if own:
            conn.close()


def due(days: int = DUE_DAYS, conn: sqlite3.Connection | None = None) -> Json:
    """Active storylines whose open question has waited more than DAYS days
    with no beat since, the longest wait first. A storyline never told at
    all is due at once."""
    own = conn is None
    conn = conn or db()
    try:
        now = hand.now()
        out = []
        for row in conn.execute("SELECT * FROM storylines WHERE status = 'active'").fetchall():
            last = conn.execute("SELECT max(at) FROM story_beats WHERE storyline = ?", (row["key"],)).fetchone()[0]
            since = max(row["question_at"], last or 0.0)
            if last is not None and since > now - days * DAY:
                continue
            out.append({"key": row["key"], "title": row["title"], "open_question": row["open_question"],
                        "next": row["next"], "question_at": iso(row["question_at"]), "last_beat_at": iso(last),
                        "waiting_days": round((now - since) / DAY, 1),
                        "reason": "never told" if last is None else f"no beat in {days} days"})
        out.sort(key=lambda d: -d["waiting_days"])
        return {"days": days, "due": out}
    finally:
        if own:
            conn.close()


# --- writes -----------------------------------------------------------------------------


def _refs(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(r, str) and r.strip() for r in value):
        raise hand.HandError("refs is a list of desk sources (strings)")
    refs = list(dict.fromkeys(r.strip() for r in value))
    if len(refs) > MAX_REFS or any(len(r) > REF_CHARS for r in refs):
        raise hand.HandError(f"refs: at most {MAX_REFS}, each up to {REF_CHARS} characters")
    return refs


def beat(body: Any) -> Json:
    """Record a posted beat: `{storyline, post_id, text, open_question?,
    shape?, format?, surface?, refs?, state?, next?}`. The storyline's open
    question (and when it was set), its state and its next move are updated
    from the beat when it brings them. A beat already recorded for the same
    post is not recorded twice."""
    if not isinstance(body, dict):
        raise hand.HandError("beat takes a JSON object on stdin")
    key = _text(body, "storyline", 32, required=True)
    post_id = _text(body, "post_id", 64, required=True)
    text = _text(body, "text", TEXT_CHARS, required=True)
    surface = _text(body, "surface", 16) or "x"
    if surface not in SURFACES:
        raise hand.HandError(f"surface is one of {', '.join(SURFACES)}")
    fields = {name: _text(body, name, FIELD_CHARS) for name in ("open_question", "shape", "format", "state", "next")}
    refs = _refs(body.get("refs"))
    row = {"storyline": key, "post_id": post_id, "surface": surface, "text": text, "refs": refs, **fields}
    conn = db()
    try:
        story = conn.execute("SELECT status FROM storylines WHERE key = ?", (key,)).fetchone()
        if story is None:
            raise hand.Refused("unknown storyline", storyline=key)
        if story["status"] != "active":
            raise hand.Refused("storyline retired", storyline=key)
        if hand.dry_run():
            return {"dry_run": True, "would_record": row}
        now = hand.now()
        with hand.transaction(conn):
            done = conn.execute(
                "INSERT OR IGNORE INTO story_beats (storyline, at, post_id, surface, format, shape, text, open_question,"
                " refs) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key, now, post_id, surface, fields["format"], fields["shape"], text, fields["open_question"],
                 json.dumps(refs, ensure_ascii=False)),
            )
            if not done.rowcount:
                existing = conn.execute("SELECT id FROM story_beats WHERE storyline = ? AND surface = ? AND post_id = ?",
                                        (key, surface, post_id)).fetchone()
                return {"recorded": False, "already": existing["id"], "storyline": key}
            beat_id = done.lastrowid
            sets, params = ["updated_at = ?"], [now]
            if fields["open_question"]:
                sets += ["open_question = ?", "question_at = ?"]
                params += [fields["open_question"], now]
            for name in ("state", "next"):
                if fields[name]:
                    sets.append(f"{name} = ?")
                    params.append(fields[name])
            conn.execute(f"UPDATE storylines SET {', '.join(sets)} WHERE key = ?", (*params, key))
        return {"recorded": True, "beat_id": beat_id, "storyline": _storyline(
            conn, conn.execute("SELECT * FROM storylines WHERE key = ?", (key,)).fetchone())}
    finally:
        conn.close()
