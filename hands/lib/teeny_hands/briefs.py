"""hands/brief: what the team tells Teeny to post about (docs/hands.md, "brief").

  O  add [--surface x|moltbook] TEXT
                        queue a brief: what the next told-to post is about
                        (x, the default), or a Moltbook lead (moltbook)
  R  next               the oldest open X brief, or {"brief": null}
  R  leads              the open Moltbook leads of the last 48 hours
  R  list [--max N]     the briefs, newest first
  A  post {id, text, media_ids?}  post `text` (Teeny's draft of brief `id`),
                        with any picture bin/act attached, through hands/x
                        post, then mark the brief posted
  O  cancel ID          drop an open brief

Teeny posts twice a day: the morning gm (brain/playbooks/morning.md) and one
post about whatever the team asked for (brain/playbooks/brief.md, which reads
`next` and answers through `post`). A brief is the team's topic, not Teeny's
words: the playbook drafts the post in Teeny's voice, bin/act runs it past the
guard, and hands/x applies the posting rules, pacing and DRY_RUN as it does to
every post. A brief is marked posted only when X took the post; a dry run, a
pacing refusal or an error leaves it open for the next run.

A Moltbook lead is the operator scouting for Teeny: a Moltbook thread worth
joining, or a topic seen on Reddit, X or Moltbook, with a note. It is never
an X brief. The Moltbook playbooks read `leads` as one input among others and
decide for themselves whether a lead is worth a post or a comment, under the
same guard and caps as anything else they write. A lead is never marked used:
it drops out after LEAD_HOURS, or when the operator cancels it, and the
Moltbook comment rules already refuse a second comment on one post.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from . import hand

SCHEMA = """
CREATE TABLE IF NOT EXISTS briefs (
  id         INTEGER PRIMARY KEY,
  text       TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'posted', 'cancelled')),
  created_at REAL NOT NULL,
  post_id    TEXT,
  post_text  TEXT,
  done_at    REAL,
  surface    TEXT NOT NULL DEFAULT 'x' CHECK (surface IN ('x', 'moltbook'))
);
CREATE INDEX IF NOT EXISTS briefs_status ON briefs (status, created_at);
"""

MAX_TEXT = 2000
SURFACES = ("x", "moltbook")
#: How long a Moltbook lead is offered: a thread two days old has moved on.
LEAD_HOURS = 48
MAX_LEADS = 10

Json = dict[str, Any]


def _db():
    conn = hand.open_db(SCHEMA)
    # A ledger from before surfaces: every brief in it was an X brief.
    if "surface" not in {r[1] for r in conn.execute("PRAGMA table_info(briefs)")}:
        try:
            conn.execute("ALTER TABLE briefs ADD COLUMN surface TEXT NOT NULL DEFAULT 'x'")
            conn.commit()
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc):
                raise
    return conn


def _public(row) -> Json:
    out = {"id": row["id"], "text": row["text"], "status": row["status"], "surface": row["surface"], "created_at": row["created_at"]}
    if row["post_id"]:
        out["post_id"] = row["post_id"]
    return out


def add(text: str, surface: str = "x") -> Json:
    if surface not in SURFACES:
        raise hand.Refused(f"a brief is for one of {', '.join(SURFACES)}")
    text = (text or "").strip()
    if not text:
        raise hand.Refused("a brief needs some text")
    if len(text) > MAX_TEXT:
        raise hand.Refused(f"a brief is at most {MAX_TEXT} characters")
    conn = _db()
    try:
        with hand.transaction(conn):
            cur = conn.execute("INSERT INTO briefs (text, created_at, surface) VALUES (?, ?, ?)", (text, hand.now(), surface))
        return {"brief": _public(conn.execute("SELECT * FROM briefs WHERE id = ?", (cur.lastrowid,)).fetchone())}
    finally:
        conn.close()


def next_open() -> Json:
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM briefs WHERE status = 'open' AND surface = 'x' ORDER BY created_at, id LIMIT 1"
        ).fetchone()
        if row is None:
            return {"brief": None}
        return {"brief": {"id": row["id"], "text": row["text"]}}
    finally:
        conn.close()


def leads() -> Json:
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT id, text, created_at FROM briefs WHERE status = 'open' AND surface = 'moltbook' AND created_at >= ?"
            " ORDER BY created_at DESC, id DESC LIMIT ?",
            (hand.now() - LEAD_HOURS * 3600, MAX_LEADS),
        ).fetchall()
        return {"leads": [{"id": r["id"], "text": r["text"], "created_at": r["created_at"]} for r in rows]}
    finally:
        conn.close()


def listing(limit: int) -> Json:
    conn = _db()
    try:
        rows = conn.execute("SELECT * FROM briefs ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return {"briefs": [_public(r) for r in rows]}
    finally:
        conn.close()


def cancel(brief_id: int) -> Json:
    conn = _db()
    try:
        with hand.transaction(conn):
            changed = conn.execute(
                "UPDATE briefs SET status = 'cancelled', done_at = ? WHERE id = ? AND status = 'open'",
                (hand.now(), brief_id),
            ).rowcount
        if not changed:
            raise hand.Refused(f"brief {brief_id} is not open")
        return {"cancelled": brief_id}
    finally:
        conn.close()


def _x_hand() -> Path:
    hands = os.environ.get("TEENY_HANDS")
    return Path(hands) / "x" if hands else hand.root() / "hands" / "x"


def post(body: Any) -> Json:
    if not isinstance(body, dict):
        raise hand.HandError("post takes {id, text} on stdin")
    brief_id, text = body.get("id"), body.get("text")
    if not isinstance(brief_id, int) or isinstance(brief_id, bool):
        raise hand.Refused("post needs the brief's id")
    if not isinstance(text, str) or not text.strip():
        raise hand.Refused("post needs the text to post")
    media_ids = body.get("media_ids") or []
    if not isinstance(media_ids, list) or not all(isinstance(m, str) and m for m in media_ids):
        raise hand.HandError('"media_ids" must be a list of media ids')
    x_body: Json = {"text": text, **({"media_ids": media_ids} if media_ids else {})}
    with hand.lock("brief"):
        conn = _db()
        try:
            row = conn.execute("SELECT status FROM briefs WHERE id = ?", (brief_id,)).fetchone()
            if row is None or row["status"] != "open":
                raise hand.Refused(f"brief {brief_id} is not open")
            done = subprocess.run(
                [str(_x_hand()), "post"],
                input=json.dumps(x_body),
                capture_output=True,
                text=True,
                cwd=str(hand.root()),
                timeout=300,
            )
            try:
                result = json.loads(done.stdout.strip().splitlines()[-1]) if done.stdout.strip() else {}
            except json.JSONDecodeError:
                result = {"raw": done.stdout[-500:]}
            if done.returncode == 2:
                raise hand.Refused(result.get("refused") or "hands/x refused the post", brief=brief_id, x=result)
            if done.returncode != 0:
                raise hand.HandError(f"hands/x post failed: {result.get('error') or done.stderr[-300:]}")
            if result.get("dry_run"):
                return {"brief": brief_id, "x": result, "marked": False}
            with hand.transaction(conn):
                conn.execute(
                    "UPDATE briefs SET status = 'posted', post_id = ?, post_text = ?, done_at = ? WHERE id = ?",
                    (str(result.get("id") or ""), result.get("text") or text, hand.now(), brief_id),
                )
            return {"brief": brief_id, "x": result, "marked": True}
        finally:
            conn.close()


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/brief", description="What the team tells Teeny to post about.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    adding = verb("add", lambda a, _: add(" ".join(a.text), a.surface), "queue a brief or a Moltbook lead (operator)")
    adding.add_argument("--surface", choices=SURFACES, default="x")
    adding.add_argument("text", nargs="+")
    verb("next", lambda a, _: next_open(), "the oldest open X brief")
    verb("leads", lambda a, _: leads(), "the open Moltbook leads of the last 48 hours")
    verb("list", lambda a, _: listing(a.max), "the briefs, newest first").add_argument(
        "--max", type=_bounded(1, 100, "--max"), default=20
    )
    verb("post", lambda a, body: post(body), "post Teeny's draft of a brief (JSON on stdin)", body=True)
    verb("cancel", lambda a, _: cancel(a.id), "drop an open brief (operator)").add_argument(
        "id", type=_bounded(1, 2**62, "ID"), metavar="ID"
    )
    return root


def main(argv: list[str]) -> int:
    return hand.run("brief", parser(), argv)
