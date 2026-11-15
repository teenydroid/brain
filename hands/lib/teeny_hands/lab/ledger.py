"""The lab ledger (docs/hands.md, "lab"): Teeny's experiments on what gets
attention, one row each in `experiments`.

An experiment is a guess about one surface: "a `confession` post on
Moltbook does better than my average". It names the `format` tag its posts
carry, the metric, how many posts it needs and a deadline. Posts join it by
carrying `experiment: <id>` in their body (teeny_hands/tags.py), or by
carrying its format while it is open.

`score` is code, never a model: the tagged writes' average result against
the surface's other writes of the same kinds in the same period, from the
measured rows `hands/stats board` reads. `conclude` records the verdict and
a one-line lesson, and refuses `works` or `fails` before the planned number
of posts was measured: below it, only `unclear`. `lessons` is what the lab
has learned, newest first, for the prompts.

Local state only; nothing is said anywhere, so DRY_RUN does not apply.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from .. import hand, tags
from ..stats import board

DAY = 86400.0
SURFACES = ("x", "moltbook")
#: `result` is the board's one number per write; `views` is X's impressions.
METRICS = {"x": ("result", "views"), "moltbook": ("result",)}
VERDICTS = ("works", "fails", "unclear")
STATUSES = ("open", "concluded")
MAX_OPEN = 5
MAX_POSTS = 20
MAX_DAYS = 30
HYPOTHESIS_CHARS = 300
LESSON_CHARS = 200
MAX_LESSONS = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    surface      TEXT NOT NULL CHECK (surface IN ('x', 'moltbook')),
    hypothesis   TEXT NOT NULL,
    format       TEXT NOT NULL,
    metric       TEXT NOT NULL,
    posts        INTEGER NOT NULL,
    proposed_at  REAL NOT NULL,
    deadline_at  REAL NOT NULL,
    source       TEXT,
    status       TEXT NOT NULL DEFAULT 'open',
    n            INTEGER,
    avg          REAL,
    baseline_n   INTEGER,
    baseline_avg REAL,
    scored_at    REAL,
    verdict      TEXT,
    lesson       TEXT,
    concluded_at REAL
);
CREATE INDEX IF NOT EXISTS experiments_status ON experiments (status);
"""

Json = dict[str, Any]


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def _line(value: Any, name: str, most: int) -> str:
    if not isinstance(value, str) or not " ".join(value.split()):
        raise hand.HandError(f'"{name}" must be a non-empty string')
    text = " ".join(value.split())
    if len(text) > most:
        raise hand.HandError(f'"{name}" is {len(text)} characters; at most {most}')
    return text


def _whole(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise hand.HandError(f'"{name}" is a whole number from {low} to {high}')
    return value


def _deadline(body: Mapping[str, Any], now: float) -> float:
    if body.get("deadline_days") is not None:
        return now + _whole(body["deadline_days"], "deadline_days", 1, MAX_DAYS) * DAY
    raw = body.get("deadline")
    if not isinstance(raw, str):
        raise hand.HandError('give "deadline_days" (1 to 30) or "deadline" (YYYY-MM-DD, UTC)')
    try:
        day = datetime.strptime(raw.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise hand.HandError('"deadline" is a date, YYYY-MM-DD') from None
    at = day.timestamp() + DAY  # the end of that day
    if not now + DAY / 2 <= at <= now + (MAX_DAYS + 1) * DAY:
        raise hand.HandError(f'"deadline" is between tomorrow and {MAX_DAYS} days from now')
    return at


def propose(body: Any, source: str | None = None) -> Json:
    """One experiment. Refused with `too_many_open` past MAX_OPEN, and with
    `duplicate` while another open one tests the same format on the same
    surface (its posts would be counted twice)."""
    if not isinstance(body, Mapping):
        raise hand.HandError("stdin must be a JSON object with surface, hypothesis, format, posts and a deadline")
    allowed = {"surface", "hypothesis", "format", "metric", "posts", "deadline", "deadline_days"}
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise hand.HandError(f"unknown field(s) in the body: {', '.join(unknown)}")
    surface = body.get("surface")
    if surface not in SURFACES:
        raise hand.HandError(f'"surface" is one of {", ".join(SURFACES)}')
    hypothesis = _line(body.get("hypothesis"), "hypothesis", HYPOTHESIS_CHARS)
    _, tagged = tags.split({"format": body.get("format")})
    if not tagged["format"]:
        raise hand.HandError('"format" is required: the tag the experiment\'s posts carry')
    metric = body.get("metric") or "result"
    if metric not in METRICS[surface]:
        raise hand.HandError(f'"metric" on {surface} is one of {", ".join(METRICS[surface])}')
    posts = _whole(body.get("posts"), "posts", 1, MAX_POSTS)
    now = hand.now()
    deadline_at = _deadline(body, now)
    conn = db()
    try:
        with hand.transaction(conn):
            open_rows = conn.execute("SELECT id, surface, format FROM experiments WHERE status = 'open'").fetchall()
            if len(open_rows) >= MAX_OPEN:
                raise hand.Refused(f"{MAX_OPEN} experiments are already open; conclude one first", rule="too_many_open",
                                   open=[r["id"] for r in open_rows])
            same = next((r for r in open_rows if r["surface"] == surface and r["format"] == tagged["format"]), None)
            if same is not None:
                raise hand.Refused(f"experiment {same['id']} already tests {tagged['format']} on {surface}",
                                   rule="duplicate", id=same["id"])
            row_id = conn.execute(
                "INSERT INTO experiments (surface, hypothesis, format, metric, posts, proposed_at, deadline_at, source)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (surface, hypothesis, tagged["format"], metric, posts, now, deadline_at, source),
            ).lastrowid
    finally:
        conn.close()
    return {"proposed": row_id, "surface": surface, "format": tagged["format"], "metric": metric, "posts": posts,
            "deadline": _date(deadline_at)}


def _date(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%d")


def _num(value: float | None) -> int | float | None:
    if value is None:
        return None
    value = round(float(value), 2)
    return int(value) if value.is_integer() else value


def measure(conn: sqlite3.Connection, exp: Mapping[str, Any]) -> Json:
    """The experiment's numbers now: its writes (tagged with its id, or
    untagged with its format) measured between its proposal and its
    deadline, against the surface's other writes of the same kinds."""
    until = min(hand.now(), float(exp["deadline_at"]))
    since = float(exp["proposed_at"])
    read = board.x_writes if exp["surface"] == "x" else board.moltbook_writes
    writes = read(conn, since, until + 1)
    mine = [w for w in writes if w["experiment"] == exp["id"]
            or (w["experiment"] is None and w["format"] == exp["format"])]
    kinds = {w["kind"] for w in mine}
    rest = [w for w in writes if w not in mine and (not kinds or w["kind"] in kinds)]

    def values(items: list[Json]) -> list[float]:
        if exp["metric"] == "views":
            return [float(w["views"]) for w in items if "views" in w]
        return [float(w["result"]) for w in items]

    got, base = values(mine), values(rest)
    return {
        "n": len(got),
        "avg": _num(sum(got) / len(got)) if got else None,
        "baseline_n": len(base),
        "baseline_avg": _num(sum(base) / len(base)) if base else None,
        "enough": len(got) >= int(exp["posts"]),
        "past_deadline": hand.now() >= float(exp["deadline_at"]),
    }


def _row(conn: sqlite3.Connection, exp_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM experiments WHERE id = ?", (exp_id,)).fetchone()
    if row is None:
        raise hand.Refused(f"there is no experiment {exp_id}", rule="unknown", id=exp_id)
    return row


def _store(conn: sqlite3.Connection, exp_id: int, got: Mapping[str, Any]) -> None:
    conn.execute(
        "UPDATE experiments SET n = ?, avg = ?, baseline_n = ?, baseline_avg = ?, scored_at = ? WHERE id = ?",
        (got["n"], got["avg"], got["baseline_n"], got["baseline_avg"], hand.now(), exp_id),
    )


def score(exp_id: int | None = None) -> Json:
    """Scores the open experiments (or one) and records the numbers. `due`
    is true once the planned posts are measured or the deadline has passed:
    then it is time to conclude."""
    conn = db()
    try:
        if exp_id is not None:
            rows = [_row(conn, exp_id)]
        else:
            rows = conn.execute("SELECT * FROM experiments WHERE status = 'open' ORDER BY id").fetchall()
        out = []
        for row in rows:
            got = measure(conn, row)
            if row["status"] == "open":
                _store(conn, row["id"], got)
            out.append({"id": row["id"], "surface": row["surface"], "format": row["format"], "metric": row["metric"],
                        "posts": row["posts"], **got, "due": got["enough"] or got["past_deadline"]})
    finally:
        conn.close()
    return {"scored": out}


def conclude(body: Any) -> Json:
    """The verdict on one open experiment and its lesson. `works` or
    `fails` needs the planned posts measured (`too_few_posts` otherwise)."""
    if not isinstance(body, Mapping):
        raise hand.HandError("stdin must be a JSON object with id, verdict and lesson")
    unknown = sorted(set(body) - {"id", "verdict", "lesson"})
    if unknown:
        raise hand.HandError(f"unknown field(s) in the body: {', '.join(unknown)}")
    exp_id = _whole(body.get("id"), "id", 1, 10**9)
    verdict = body.get("verdict")
    if verdict not in VERDICTS:
        raise hand.HandError(f'"verdict" is one of {", ".join(VERDICTS)}')
    lesson = _line(body.get("lesson"), "lesson", LESSON_CHARS)
    conn = db()
    try:
        with hand.transaction(conn):
            row = _row(conn, exp_id)
            if row["status"] != "open":
                raise hand.Refused(f"experiment {exp_id} is already concluded ({row['verdict']})", rule="concluded")
            got = measure(conn, row)
            if verdict != "unclear" and not got["enough"]:
                raise hand.Refused(
                    f"experiment {exp_id} planned {row['posts']} posts and {got['n']} are measured: too few for"
                    " a verdict; conclude it as unclear, or wait", rule="too_few_posts", n=got["n"], posts=row["posts"])
            _store(conn, exp_id, got)
            conn.execute("UPDATE experiments SET status = 'concluded', verdict = ?, lesson = ?, concluded_at = ? WHERE id = ?",
                         (verdict, lesson, hand.now(), exp_id))
    finally:
        conn.close()
    return {"concluded": exp_id, "verdict": verdict, "lesson": lesson, "n": got["n"], "avg": got["avg"],
            "baseline_avg": got["baseline_avg"]}


def _brief(row: Mapping[str, Any], live: Mapping[str, Any] | None = None) -> Json:
    item: Json = {"id": row["id"], "surface": row["surface"], "format": row["format"], "metric": row["metric"],
                  "hypothesis": row["hypothesis"], "posts": row["posts"], "status": row["status"],
                  "proposed": _date(row["proposed_at"]), "deadline": _date(row["deadline_at"])}
    source = live if live is not None else row
    numbers = {k: source[k] for k in ("n", "avg", "baseline_n", "baseline_avg")}
    item.update({k: v for k, v in numbers.items() if v is not None})
    if live is not None:
        item["due"] = bool(live["enough"] or live["past_deadline"])
    if row["status"] == "concluded":
        item.update(verdict=row["verdict"], lesson=row["lesson"], concluded=_date(row["concluded_at"]))
    return item


def listing(status: str | None = "open", limit: int = 20) -> Json:
    """The experiments, newest first; an open one with its numbers as of
    now (computed, not stored)."""
    conn = db()
    try:
        where, params = ("WHERE status = ?", (status,)) if status else ("", ())
        rows = conn.execute(f"SELECT * FROM experiments {where} ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
        items = [_brief(r, measure(conn, r) if r["status"] == "open" else None) for r in rows]
    finally:
        conn.close()
    return {"experiments": items}


def lessons(limit: int = 10) -> Json:
    """What the concluded experiments taught, newest first."""
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM experiments WHERE status = 'concluded' ORDER BY concluded_at DESC, id DESC"
                            " LIMIT ?", (min(limit, MAX_LESSONS),)).fetchall()
    finally:
        conn.close()
    return {"lessons": [{"id": r["id"], "surface": r["surface"], "format": r["format"], "verdict": r["verdict"],
                         "lesson": r["lesson"], "n": r["n"], "avg": r["avg"], "baseline_avg": r["baseline_avg"],
                         "concluded": _date(r["concluded_at"])} for r in rows]}
