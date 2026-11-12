"""The goal ledger, in `goals` (state/hands.db).

  goals(id, instruction, component, channel, family, step, first, why,
        check_kind, check_params, status, proposed_at, deadline_at,
        baseline, baseline_at, checked_at, resolved_at, evidence, novel,
        p_hat, source)

  status  open    proposed, its check not yet passed, its deadline not passed
          solved  its check passed (evidence says how), before the deadline
                  or at the first check after it
          failed  its deadline passed and its check never did

`propose` validates a goal and writes it open; `check` (the pinned
goals-check job, bin/goals-check) evaluates every open goal against its
check and moves each one once. A goal resolved is never evaluated again,
so running `check` twice changes nothing the second time.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from typing import Any, Mapping

from .. import hand
from . import checks, rank

Json = dict[str, Any]
DAY = 86400.0
STATUSES = ("open", "solved", "failed")
CHANNELS = ("x", "moltbook", "github", "wallet", "web", "mail", "telegram")
STEPS = ("act", "attention", "mindshare", "holding", "capability")
#: The most goals open at once.
MAX_OPEN = 5
#: A proposal is compared with the open goals and every goal proposed this
#: many days back, whatever became of it.
RECENT_DAYS = 30
MIN_DEADLINE_DAYS, MAX_DEADLINE_DAYS = 1, 30
_FAMILY = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
FIELDS = ("instruction", "component", "channel", "family", "deadline_days", "check")
OPTIONAL = ("step", "first", "why")

SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    instruction  TEXT NOT NULL,
    component    TEXT NOT NULL,
    channel      TEXT NOT NULL,
    family       TEXT NOT NULL,
    step         TEXT,
    first        INTEGER NOT NULL DEFAULT 0,
    why          TEXT,
    check_kind   TEXT NOT NULL,
    check_params TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',
    proposed_at  REAL NOT NULL,
    deadline_at  REAL NOT NULL,
    baseline     REAL,
    baseline_at  REAL,
    checked_at   REAL,
    resolved_at  REAL,
    evidence     TEXT,
    novel        INTEGER NOT NULL DEFAULT 1,
    p_hat        REAL,
    source       TEXT
);
CREATE INDEX IF NOT EXISTS goals_status ON goals (status, deadline_at);
"""


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def _goal(row: sqlite3.Row) -> Json:
    out = {k: row[k] for k in row.keys() if row[k] is not None}
    out["params"] = json.loads(out.pop("check_params"))
    out["first"] = bool(row["first"])
    out["novel"] = bool(row["novel"])
    if "evidence" in out:
        out["evidence"] = json.loads(out["evidence"])
    return out


def all_goals(conn: sqlite3.Connection | None = None) -> list[Json]:
    own = conn is None
    conn = conn or db()
    try:
        return [_goal(r) for r in conn.execute("SELECT * FROM goals ORDER BY id").fetchall()]
    finally:
        if own:
            conn.close()


def _refuse(reason: str, rule: str, **detail: Any) -> None:
    raise hand.Refused(reason, rule=rule, **detail)


def _text(body: Mapping[str, Any], key: str, low: int, high: int) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not low <= len(value.strip()) <= high:
        _refuse(f'"{key}" must be text of {low} to {high} characters', "invalid", field=key)
    return " ".join(value.split())


def _one_of(body: Mapping[str, Any], key: str, allowed: tuple[str, ...]) -> str:
    value = body.get(key)
    if value not in allowed:
        _refuse(f'"{key}" is one of {", ".join(allowed)}', "invalid", field=key)
    return value


def _validate(body: Any) -> Json:
    if not isinstance(body, Mapping):
        raise hand.HandError(f"stdin must be a JSON object with {', '.join(FIELDS)}")
    unknown = sorted(set(body) - {*FIELDS, *OPTIONAL})
    if unknown:
        _refuse(f"unknown field(s): {', '.join(unknown)}", "invalid", fields=unknown)
    missing = [k for k in FIELDS if k not in body]
    if missing:
        _refuse(f"missing field(s): {', '.join(missing)}", "invalid", fields=missing)
    goal: Json = {
        "instruction": _text(body, "instruction", 12, 400),
        "component": _one_of(body, "component", checks.COMPONENTS),
        "channel": _one_of(body, "channel", CHANNELS),
    }
    family = body.get("family")
    if not isinstance(family, str) or not _FAMILY.match(family):
        _refuse('"family" is a short lowercase slug (letters, digits, dashes), like x-followers', "invalid",
                field="family")
    goal["family"] = family
    days = body.get("deadline_days")
    if isinstance(days, bool) or not isinstance(days, int) or not MIN_DEADLINE_DAYS <= days <= MAX_DEADLINE_DAYS:
        _refuse(f'"deadline_days" is a whole number of days, {MIN_DEADLINE_DAYS} to {MAX_DEADLINE_DAYS}', "invalid",
                field="deadline_days")
    goal["deadline_days"] = days
    try:
        goal["check_kind"], goal["params"] = checks.validate(body.get("check"))
    except checks.BadParams as exc:
        rule = "check_kind" if "unknown check kind" in str(exc) else "check_params"
        _refuse(str(exc), rule, kinds=list(checks.KINDS))
    if body.get("step") is not None:
        goal["step"] = _one_of(body, "step", STEPS)
    first = body.get("first", False)
    if not isinstance(first, bool):
        _refuse('"first" is true or false', "invalid", field="first")
    goal["first"] = first
    if body.get("why") is not None:
        goal["why"] = _text(body, "why", 1, 600)
    return goal


def propose(body: Any, source: str | None = None) -> Json:
    """Validates one goal and writes it open. Refused: an unknown check kind
    or parameter, a near-duplicate of an open goal or one proposed in the
    last RECENT_DAYS, or MAX_OPEN goals already open."""
    goal = _validate(body)
    now = hand.now()
    goal.update(proposed_at=now, deadline_at=now + goal["deadline_days"] * DAY, status="open")
    with hand.lock("goals"):
        conn = db()
        try:
            with hand.transaction(conn):
                goals = all_goals(conn)
                open_now = [g for g in goals if g["status"] == "open"]
                if len(open_now) >= MAX_OPEN:
                    _refuse(f"{len(open_now)} goals are open; the most is {MAX_OPEN}", "too_many_open",
                            open=[g["id"] for g in open_now])
                for other in goals:
                    recent = other["status"] == "open" or now - other["proposed_at"] <= RECENT_DAYS * DAY
                    if recent and rank.near_duplicate(goal, other):
                        _refuse(f"a near-duplicate of goal {other['id']} ({other['status']})", "duplicate",
                                duplicate_of=other["id"], instruction=other["instruction"])
                novel = not any(g["status"] == "solved" and rank.near_duplicate(goal, g) for g in goals)
                family = rank.family_stats(goals, now).get(goal["family"]) or rank._blank_family(goal["family"])
                cursor = conn.execute(
                    "INSERT INTO goals (instruction, component, channel, family, step, first, why, check_kind,"
                    " check_params, status, proposed_at, deadline_at, novel, p_hat, source)"
                    " VALUES (?,?,?,?,?,?,?,?,?, 'open', ?,?,?,?,?)",
                    (goal["instruction"], goal["component"], goal["channel"], goal["family"], goal.get("step"),
                     int(goal["first"]), goal.get("why"), goal["check_kind"],
                     json.dumps(goal["params"], sort_keys=True), now, goal["deadline_at"], int(novel),
                     family["p_hat"], source),
                )
                goal_id = cursor.lastrowid
        finally:
            conn.close()
        baseline = _try_baseline(goal_id)
    out: Json = {"goal_id": goal_id, "status": "open", "deadline_at": goal["deadline_at"], "novel": novel,
                 "family": {k: family[k] for k in ("family", "p_hat", "lp", "in_band")}}
    if baseline is not None:
        out["baseline"] = baseline
    return out


def _try_baseline(goal_id: int) -> float | None:
    """Records the goal's baseline now when its kind has one and the data
    can be read; otherwise the first check records it."""
    conn = db()
    try:
        row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        goal = _goal(row)
        if checks.KINDS[goal["check_kind"]].baseline is None:
            return None
        try:
            value = checks.baseline(goal)
        except (checks.Unavailable, hand.HandError, OSError, ValueError):
            return None
        with hand.transaction(conn):
            conn.execute("UPDATE goals SET baseline = ?, baseline_at = ? WHERE id = ? AND baseline IS NULL",
                         (value, hand.now(), goal_id))
        return value
    finally:
        conn.close()


def check() -> Json:
    """Every open goal against its check: solved when it passes, failed when
    its deadline has passed and it does not, else still open. Each change is
    written once (only an open row moves) and told to the mind."""
    moved: list[Json] = []
    still: list[Json] = []
    with hand.lock("goals"):
        conn = db()
        try:
            for goal in [g for g in all_goals(conn) if g["status"] == "open"]:
                now = hand.now()
                result: Json | None = None
                note: str | None = None
                try:
                    base = goal.get("baseline")
                    if checks.KINDS[goal["check_kind"]].baseline is not None and base is None:
                        base = checks.baseline(goal)
                        goal.update(baseline=base, baseline_at=now)
                        with hand.transaction(conn):
                            conn.execute("UPDATE goals SET baseline = ?, baseline_at = ? WHERE id = ?"
                                         " AND baseline IS NULL", (base, now, goal["id"]))
                    result = checks.evaluate(goal, base)
                except (checks.Unavailable, hand.HandError, OSError, ValueError) as exc:
                    note = str(exc) or type(exc).__name__
                if result is not None and result["passed"]:
                    status = "solved"
                elif now > goal["deadline_at"]:
                    status = "failed"
                else:
                    status = "open"
                evidence = dict(result["evidence"]) if result is not None else {}
                if note:
                    evidence["unavailable"] = note[:300]
                with hand.transaction(conn):
                    changed = conn.execute(
                        "UPDATE goals SET status = ?, checked_at = ?, evidence = ?,"
                        " resolved_at = CASE WHEN ? = 'open' THEN NULL ELSE ? END WHERE id = ? AND status = 'open'",
                        (status, now, json.dumps(evidence, sort_keys=True, default=str), status, now, goal["id"]),
                    ).rowcount
                entry = {"id": goal["id"], "status": status, "evidence": evidence}
                if status == "open" or not changed:
                    still.append(entry)
                    continue
                moved.append(entry)
                _tell_the_mind(goal, status, evidence)
        finally:
            conn.close()
    return {"moved": moved, "open": still}


def _tell_the_mind(goal: Mapping[str, Any], status: str, evidence: Mapping[str, Any]) -> None:
    """A side channel: failing to write the stimulus never fails the check."""
    from .. import stimuli

    word = "solved" if status == "solved" else "failed (its deadline passed)"
    text = (f"your goal #{goal['id']} is {word}: {goal['instruction']}\n"
            f"check {goal['check_kind']} {json.dumps(goal['params'], sort_keys=True)}; "
            f"evidence {json.dumps(evidence, sort_keys=True, default=str)[:800]}")
    try:
        stimuli.add("self", "goal", f"goal:{goal['id']}:{status}", text,
                    payload={"goal_id": goal["id"], "status": status, "family": goal["family"],
                             "component": goal["component"], "channel": goal["channel"]})
    except (hand.HandError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"goals: could not tell the mind about goal {goal['id']}: {exc}", file=sys.stderr)


def listing(status: str | None = None, limit: int = 50) -> Json:
    if status is not None and status not in STATUSES:
        raise hand.HandError(f"status must be one of {', '.join(STATUSES)}")
    goals = [g for g in all_goals() if status is None or g["status"] == status]
    goals.sort(key=lambda g: -g["id"])
    return {"goals": goals[:limit], "open": sum(1 for g in all_goals() if g["status"] == "open"), "max_open": MAX_OPEN}


def ranking() -> Json:
    out = rank.rank(all_goals(), hand.now())
    out["max_open"] = MAX_OPEN
    return out


def annecs() -> Json:
    out = rank.annecs(all_goals(), CHANNELS)
    out["note"] = ("ANNECS counts solved goals, confirmed by their frozen checks, that were new when proposed "
                   "and are not near-duplicates of a goal solved before them.")
    return out
