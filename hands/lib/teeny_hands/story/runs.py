"""`studio_runs`: one row per bin/studio run (docs/hands.md, "story").

bin/studio is a script under bin/, and bin/ never writes state/hands.db
itself: the room's record comes here as JSON on stdin (`record-run`), the
same record bin/studio appends to state/studio/runs.jsonl. The row keeps the
columns a report filters on (when, which slot, the outcome, the models, the
counts) and the whole record beside them, so every pitch, draft, pairing,
verdict and the pick (or the veto) can be read back (`runs`).

A run record is written under DRY_RUN too: it is the room's own log of what
it did, sends nothing to anyone, and a dry run is exactly what the operator
reads before the studio goes live. A run already recorded (same id) is not
recorded twice.
"""

from __future__ import annotations

import json
from typing import Any

from .. import hand
from . import store

Json = dict[str, Any]

SCHEMA = """
CREATE TABLE IF NOT EXISTS studio_runs (
    id            TEXT PRIMARY KEY,
    at            REAL NOT NULL,
    slot          TEXT,
    dry           INTEGER NOT NULL DEFAULT 1,
    seed          INTEGER,
    explore       INTEGER NOT NULL DEFAULT 0,
    outcome       TEXT NOT NULL,
    reason        TEXT,
    writer_model  TEXT,
    editor_model  TEXT,
    pitches       INTEGER NOT NULL DEFAULT 0,
    drafts        INTEGER NOT NULL DEFAULT 0,
    calls         INTEGER NOT NULL DEFAULT 0,
    secs          REAL,
    pick          TEXT,
    record        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS studio_runs_at ON studio_runs (at);
"""

#: What a run can come to (bin/lib/studio.py): `published` (a real post went
#: out), `picked` (a pick that went no further: a dry run's, which would
#: post), `empty` (a pick, and nothing could go out), `vetoed`, `skipped`,
#: `failed`. An editor's single-candidate check (`kind` veto) is `picked`,
#: `vetoed` or `failed`.
OUTCOMES = ("published", "picked", "empty", "vetoed", "skipped", "failed")
#: Columns added since the table was made: the record's kind (`run`, or
#: `veto`), and the id of the post a run published.
COLUMNS = {"kind": "TEXT", "post_id": "TEXT"}
#: The most a record may hold, as compact JSON.
MAX_RECORD_BYTES = 512 * 1024
LIMIT_DEFAULT = 10


def db():
    conn = hand.open_db(SCHEMA)
    hand.add_columns(conn, "studio_runs", COLUMNS)
    return conn


def _count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def record(body: Any) -> Json:
    """Keep one run record: `{id, at (unix seconds), outcome, ...}` as
    bin/studio writes it."""
    if not isinstance(body, dict):
        raise hand.HandError("record-run takes a JSON object on stdin")
    run_id = body.get("id")
    if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 80:
        raise hand.HandError("id is a string of at most 80 characters")
    at = body.get("at_unix")
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        raise hand.HandError("at_unix is a number (unix seconds)")
    outcome = body.get("outcome")
    if outcome not in OUTCOMES:
        raise hand.HandError(f"outcome is one of {', '.join(OUTCOMES)}")
    text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode()) > MAX_RECORD_BYTES:
        raise hand.HandError(f"the record is over {MAX_RECORD_BYTES} bytes")
    models = body.get("models") if isinstance(body.get("models"), dict) else {}
    calls = body.get("calls") if isinstance(body.get("calls"), dict) else {}
    pick = body.get("pick")
    kind = body.get("kind") if body.get("kind") in ("run", "veto") else "run"
    published = body.get("published") if isinstance(body.get("published"), dict) else {}
    post_id = published.get("post_id") if isinstance(published.get("post_id"), str) else None
    row = (run_id, float(at), body.get("slot") if isinstance(body.get("slot"), str) else None,
           0 if body.get("dry") is False else 1,
           body.get("seed") if isinstance(body.get("seed"), int) else None,
           1 if body.get("explore") else 0, outcome,
           str(body["reason"])[:500] if body.get("reason") is not None else None,
           str(models.get("writer") or "")[:100] or None, str(models.get("editor") or "")[:100] or None,
           _count(body.get("pitches")), _count(body.get("drafts")),
           int(calls.get("total") or 0) if isinstance(calls.get("total"), int) else 0,
           float(body["secs"]) if isinstance(body.get("secs"), (int, float)) else None,
           json.dumps(pick, ensure_ascii=False) if pick is not None else None, text, kind, post_id)
    conn = db()
    try:
        with hand.transaction(conn):
            done = conn.execute(
                "INSERT OR IGNORE INTO studio_runs (id, at, slot, dry, seed, explore, outcome, reason, writer_model,"
                " editor_model, pitches, drafts, calls, secs, pick, record, kind, post_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
        return {"recorded": bool(done.rowcount), "id": run_id}
    finally:
        conn.close()


def runs(limit: int = LIMIT_DEFAULT, full: bool = False) -> Json:
    """The newest runs, newest first: the columns, and with `full` the whole
    record of each."""
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM studio_runs ORDER BY at DESC, id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        item = {"id": r["id"], "at": store.iso(r["at"]), "slot": r["slot"], "dry": bool(r["dry"]),
                "seed": r["seed"], "explore": bool(r["explore"]), "outcome": r["outcome"], "reason": r["reason"],
                "models": {"writer": r["writer_model"], "editor": r["editor_model"]},
                "pitches": r["pitches"], "drafts": r["drafts"], "calls": r["calls"], "secs": r["secs"],
                "pick": json.loads(r["pick"]) if r["pick"] else None, "kind": r["kind"] or "run",
                "post_id": r["post_id"]}
        if full:
            item["record"] = json.loads(r["record"])
        out.append(item)
    return {"runs": out}
