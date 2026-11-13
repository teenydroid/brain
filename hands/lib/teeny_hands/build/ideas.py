"""The build idea Teeny keeps for its daily job, in `build_ideas`:

  build_ideas(day, title, who, source, pitch, created_at, job_id)

  day      the UTC day it was kept: the primary key, so one idea a day
  title    what to build, in a few words
  who      who asked for it, or who would use it (a person, a thread's crowd,
           Teeny itself)
  source   where the need shows: a link, or the ref of a stimulus
  pitch    one line on what it does
  job_id   the daily job that was made from it, once one was

The `build-idea` playbook drafts one from what Teeny read that day (Hacker
News, the requests, the people it knows, its issues) and hands it to `keep`.
bin/act puts the title through the guard; `keep` reads every field against
the guard's lexicon itself, because the words end up in the builder's job.
Each field is one line, cut to its length: it is a note, never a prompt.

`next_job` (build/jobs.py) makes the daily job from the newest idea no job
was made from, while it is fresh (FRESH_SECS). The daily job is made at the
first desk run after UTC midnight, so the idea it builds is usually the one
kept the evening before.

Keeping an idea writes only to state/hands.db and reaches nobody, so DRY_RUN
does not hold it back (like `self` and `next`); `ship` is where DRY_RUN
applies.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Any, Mapping

from .. import hand

SCHEMA = """
CREATE TABLE IF NOT EXISTS build_ideas (
    day        TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    who        TEXT NOT NULL,
    source     TEXT NOT NULL,
    pitch      TEXT NOT NULL,
    created_at REAL NOT NULL,
    job_id     INTEGER
);
"""

#: The fields of an idea, and how long each may be.
LIMITS = {"title": 120, "who": 160, "source": 300, "pitch": 300}
#: An idea no job was made from is dropped after this long: the day it was
#: about has passed.
FRESH_SECS = 48 * 3600

Json = dict[str, Any]


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def _day() -> str:
    return _dt.datetime.fromtimestamp(hand.now(), _dt.timezone.utc).strftime("%Y-%m-%d")


def _idea(row: sqlite3.Row | None) -> Json | None:
    return None if row is None else {k: row[k] for k in row.keys() if row[k] is not None}


def _line(body: Mapping[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise hand.HandError(f'"{key}" must be a non-empty string')
    return " ".join(value.split())[: LIMITS[key]].rstrip()


def keep(body: Any) -> Json:
    """Keeps today's idea: {title, who, source, pitch}. Refused when today
    already has one (`one_a_day`, with the one kept: nothing is replaced) or
    the guard's lexicon holds its words (`guard`)."""
    if not isinstance(body, Mapping):
        raise hand.HandError("stdin must be a JSON object with title, who, source, pitch")
    unknown = sorted(set(body) - set(LIMITS))
    if unknown:
        raise hand.HandError(f"unknown field(s) in the body: {', '.join(unknown)}")
    fields = {key: _line(body, key) for key in LIMITS}
    from ..guard.screen import OUTBOUND, screen_text

    match = screen_text(" \n".join(fields.values()), OUTBOUND)
    if match is not None:
        raise hand.Refused("the guard held the idea's words", rule="guard", category=match.category)
    day = _day()
    conn = db()
    try:
        with hand.transaction(conn):
            have = conn.execute("SELECT * FROM build_ideas WHERE day = ?", (day,)).fetchone()
            if have is not None:
                raise hand.Refused("today's idea is already kept; one a day, and nothing is replaced",
                                   rule="one_a_day", idea=_idea(have))
            conn.execute(
                "INSERT INTO build_ideas (day, title, who, source, pitch, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (day, fields["title"], fields["who"], fields["source"], fields["pitch"], hand.now()),
            )
            row = conn.execute("SELECT * FROM build_ideas WHERE day = ?", (day,)).fetchone()
    finally:
        conn.close()
    return {"idea": _idea(row)}


def listing(limit: int = 30) -> Json:
    """Today's idea (or none), and the ideas kept, newest first."""
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM build_ideas ORDER BY day DESC LIMIT ?", (limit,)).fetchall()
        today = conn.execute("SELECT * FROM build_ideas WHERE day = ?", (_day(),)).fetchone()
    finally:
        conn.close()
    return {"today": _idea(today), "ideas": [_idea(r) for r in rows]}


def waiting(conn: sqlite3.Connection) -> Json | None:
    """The newest idea no job was made from, while it is fresh."""
    return _idea(conn.execute(
        "SELECT * FROM build_ideas WHERE job_id IS NULL AND created_at > ? ORDER BY day DESC LIMIT 1",
        (hand.now() - FRESH_SECS,),
    ).fetchone())


def used(conn: sqlite3.Connection, day: str, job_id: int) -> None:
    conn.execute("UPDATE build_ideas SET job_id = ? WHERE day = ? AND job_id IS NULL", (job_id, day))


def brief(idea: Mapping[str, Any]) -> str:
    """The idea as the opening of the daily job's text."""
    pitch = idea["pitch"] if idea["pitch"].endswith((".", "!", "?")) else idea["pitch"] + "."
    return (f"Today's idea: {idea['title']}. {pitch} Who wants it: {idea['who']}. "
            f"Where the need shows: {idea['source']}")
