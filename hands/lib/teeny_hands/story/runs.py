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
CREATE TABLE IF NOT EXISTS studio_slots (
    slot_id    TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    status     TEXT NOT NULL,
    intent     TEXT,
    post_id    TEXT,
    at         REAL NOT NULL,
    updated_at REAL NOT NULL
);
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


# --- one run a slot (bin/studio) ---------------------------------------------------------
#
# A slot (`<date>:<slot name>`, in Teeny's own time) is claimed before any
# model turn, and its run marks it as it goes: `claimed`, `posting` (the
# draft about to go to hands/x, written first), then `posted` (with the post
# id), `empty`, `skipped` or `failed`. A second run for a claimed slot, a
# rerun or a retry, does nothing: the slot had its one run. A slot left
# `posting` (the run died between handing hands/x the post and hearing back)
# may be out on X without being in x_posts: the next claim marks it
# `in_doubt` and says so, and nothing ever retries it.

SLOT_STATUSES = ("claimed", "posting", "posted", "empty", "skipped", "failed", "in_doubt")


def _slot_row(row) -> Json:
    out = {k: row[k] for k in ("slot_id", "run_id", "status", "post_id")}
    out["intent"] = json.loads(row["intent"]) if row["intent"] else None
    out["at"], out["updated_at"] = store.iso(row["at"]), store.iso(row["updated_at"])
    return out


def _slot_body(body: Any, *required: str) -> Json:
    if not isinstance(body, dict):
        raise hand.HandError("a JSON object on stdin")
    for name in required:
        value = body.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > 120:
            raise hand.HandError(f"{name} is a string of at most 120 characters")
    return body


def claim_slot(body: Any) -> Json:
    """`{slot_id, run_id, force?}`: the slot's one run. {claimed: true} for
    the first run (or `force`, the operator's --force-slot), else {claimed:
    false, slot: <the row>}. Either way, every other slot left `posting` is
    marked `in_doubt` and listed (`in_doubt`). Under DRY_RUN too: bin/studio
    claims only for a run that may post."""
    body = _slot_body(body, "slot_id", "run_id")
    now = hand.now()
    conn = db()
    try:
        with hand.transaction(conn):
            doubt = [dict(r) for r in conn.execute(
                "SELECT * FROM studio_slots WHERE status = 'posting' AND slot_id != ?", (body["slot_id"],)).fetchall()]
            conn.execute("UPDATE studio_slots SET status = 'in_doubt', updated_at = ? WHERE status = 'posting'"
                         " AND slot_id != ?", (now, body["slot_id"]))
            row = conn.execute("SELECT * FROM studio_slots WHERE slot_id = ?", (body["slot_id"],)).fetchone()
            if row is not None and not body.get("force"):
                out: Json = {"claimed": False, "slot": _slot_row(row)}
            else:
                conn.execute("INSERT OR REPLACE INTO studio_slots (slot_id, run_id, status, at, updated_at)"
                             " VALUES (?, ?, 'claimed', ?, ?)", (body["slot_id"], body["run_id"], now, now))
                out = {"claimed": True, "slot_id": body["slot_id"]}
                if row is not None:
                    out["replaced"] = _slot_row(row)
        out["in_doubt"] = [{"slot_id": r["slot_id"], "run_id": r["run_id"],
                            "intent": json.loads(r["intent"]) if r["intent"] else None} for r in doubt]
        return out
    finally:
        conn.close()


def mark_slot(body: Any) -> Json:
    """`{slot_id, run_id, status, intent?, post_id?}`: the run's progress on
    its own slot (a run that does not hold the slot changes nothing)."""
    body = _slot_body(body, "slot_id", "run_id", "status")
    if body["status"] not in SLOT_STATUSES:
        raise hand.HandError(f"status is one of {', '.join(SLOT_STATUSES)}")
    intent = body.get("intent")
    text = json.dumps(intent, ensure_ascii=False)[:8000] if intent is not None else None
    conn = db()
    try:
        with hand.transaction(conn):
            done = conn.execute(
                "UPDATE studio_slots SET status = ?, intent = coalesce(?, intent), post_id = coalesce(?, post_id),"
                " updated_at = ? WHERE slot_id = ? AND run_id = ?",
                (body["status"], text, body.get("post_id") if isinstance(body.get("post_id"), str) else None,
                 hand.now(), body["slot_id"], body["run_id"]))
        return {"marked": bool(done.rowcount)}
    finally:
        conn.close()
