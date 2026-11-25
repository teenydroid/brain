"""What the scripts did while the mind was not looking (docs/mind.md,
"Conscious and subconscious").

Most of Teeny runs without the agentic mind: the cron scripts that post,
answer, dream, build and evolve, each a fixed pipeline around a one-shot
model call. They are cheap, and they are Teeny's subconscious. The mind is
jailed to its home and cannot read their ledgers, so the broker hands it
this instead (`teeny pulse`): every job with its last run and how often it
failed, what became of what the scripts made, which is the same reading
bin/evolve proposes from (bin/lib/evolve_evidence.py), and the audience
numbers hands/stats records each day, with where each stood a day and a
week before, and, while one is open, a notes file that shrank since its last
snapshot (bin/lib/notes_keep.py, `notes_shrunk`).

Read-only. Only Teeny's own words are quoted; other people's are counted.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
import tomllib
from pathlib import Path
from typing import Any

CODE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE / "hands" / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from teeny_hands import hand  # noqa: E402
from teeny_hands.stats import board  # noqa: E402

import evolve_evidence  # noqa: E402
import notes_keep  # noqa: E402

Json = dict[str, Any]

#: The window a pulse may cover, in days.
MIN_DAYS, MAX_DAYS, DEFAULT_DAYS = 1, 7, 1
#: How many of a job's failures are given with their times, newest first.
MAX_FAILURES = 5
#: Whose failure a run's row may say it was (bin/act, `failure`).
FAILURE_CLASSES = ("provider", "playbook")
#: The mind's own wakes are its own doing, not the scripts'.
MIND_JOB, MIND_WAKES = "teeny.mind", ("mind.", "teeny.mind.")
#: The audience numbers: the stats board's own, and GitHub's.
AUDIENCE = (*board.AUDIENCE, ("github", "followers"), ("github", "stars"))
NO_AUDIENCE = "no audience number recorded yet: the pinned stats-snapshot job (hands/stats snapshot) has not written one"

JOBS_SQL = """
with jobs as materialized (
  select id, body from docs where coll = 'cron.jobs' and deleted = 0 and json_valid(body)
),
runs as materialized (
  select body from docs where coll = 'cron.runs' and deleted = 0 and json_valid(body) and body != '{}'
),
last as (
  select json_extract(body, '$.job_id') as job_id, json_extract(body, '$.status') as status,
         json_extract(body, '$.finished_ms') as finished_ms,
         row_number() over (partition by json_extract(body, '$.job_id')
                            order by json_extract(body, '$.seq') desc) as n
  from runs
),
window as (
  select json_extract(body, '$.job_id') as job_id, count(*) as runs,
         sum(json_extract(body, '$.status') = 'error') as failed
  from runs where json_extract(body, '$.finished_ms') >= ? group by job_id
)
select json_extract(jobs.body, '$.name'), json_extract(jobs.body, '$.job_type'),
       json_extract(jobs.body, '$.expression'), json_extract(jobs.body, '$.enabled'),
       last.status, last.finished_ms, window.runs, window.failed
from jobs left join last on last.job_id = jobs.id and last.n = 1
          left join window on window.job_id = jobs.id
order by 1
"""

#: The newest failures of each cron job in the window, MAX_FAILURES a job.
FAILURES_SQL = """
with failed as (
  select json_extract(body, '$.job_id') as job_id, json_extract(body, '$.finished_ms') as finished_ms
  from docs where coll = 'cron.runs' and deleted = 0 and json_valid(body) and body != '{}'
    and json_extract(body, '$.status') = 'error' and json_extract(body, '$.finished_ms') >= ?
),
newest as (
  select job_id, finished_ms, row_number() over (partition by job_id order by finished_ms desc) as n from failed
)
select json_extract(jobs.body, '$.name'), newest.finished_ms
from newest join docs jobs on jobs.id = newest.job_id and jobs.coll = 'cron.jobs' and jobs.deleted = 0 and json_valid(jobs.body)
where n <= ?
"""


def _storage() -> Path:
    return Path(os.environ.get("TEENY_STORAGE") or hand.root() / "brain" / "storage.db")


def _iso(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)):
        return None
    try:
        return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, ValueError, OSError):  # no date: a nan, or far out of range
        return None


def _newest(failures: list[Json]) -> list[Json]:
    """The newest MAX_FAILURES of FAILURES. ISO times in UTC sort as text."""
    return sorted(failures, key=lambda f: f["at"], reverse=True)[:MAX_FAILURES]


def _failure(when: float, row: Json) -> list[Json]:
    """When ROW failed and, where it says so, whose failure it was; nothing
    when WHEN is no date (a time is never guessed)."""
    at = _iso(when * 1000)
    if at is None:
        return []
    return [{"at": at, **({"failure": row["failure"]} if row.get("failure") in FAILURE_CLASSES else {})}]


def _names(path: Path) -> set[str]:
    try:
        with open(path, "rb") as fh:
            return set(tomllib.load(fh).get("jobs") or {})
    except (OSError, tomllib.TOMLDecodeError):
        return set()


def jobs(since: float) -> list[Json] | None:
    """Every script and agent job of Teeny's but the mind's own wakes, with
    its last run and its failures since SINCE. None when the core's store
    cannot be read."""
    path = _storage()
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            rows = conn.execute(JOBS_SQL, (int(since * 1000),)).fetchall()
            failed_rows = conn.execute(FAILURES_SQL, (int(since * 1000), MAX_FAILURES)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    yours = _names(hand.root() / "brain" / "cron.toml")
    beats = _tick_failures(since)
    when: dict[str, list[Json]] = {}
    for name, finished_ms in failed_rows:
        at = _iso(finished_ms)
        if at is not None:
            when.setdefault(str(name or ""), []).append({"at": at})
    out = []
    for name, kind, expr, enabled, last, finished_ms, runs, failed in rows:
        name = str(name or "")
        if not name.startswith("teeny.") or name == MIND_JOB or name.startswith(MIND_WAKES):
            continue
        job = name.removeprefix("teeny.")
        row: Json = {"job": job, "kind": "agent" if kind == "agent" else "script", "schedule": expr,
                     "owner": "yours (brain/cron.toml)" if job in yours else "pinned (the operator's)",
                     "last": last, "last_at": _iso(finished_ms), "runs": int(runs or 0), "failed": int(failed or 0)}
        if when.get(name):
            row["last_failures"] = _newest(when[name])
        if enabled == 0:
            row["off"] = True
        if job == "tick" and beats["failed"]:
            # `bin/tick --detach` exits at once, so cron saw a launch; the beat's own result is in its ledger
            row["beats_failed"], row["last_beat_error"] = beats["failed"], beats["last_error"]
            row["last_beat_failures"] = beats["last_failures"]
        out.append(row)
    return out


def _tick_failures(since: float) -> Json:
    """Beats of the detached tick that ended `failed` since SINCE, from
    state/tick/runs.jsonl (cron records only that the beat was launched)."""
    failed, last, at = 0, None, []
    try:
        with (hand.state_dir() / "tick" / "runs.jsonl").open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    beat = json.loads(line)
                except ValueError:
                    continue
                when = evolve_evidence.epoch(beat.get("at")) if isinstance(beat, dict) else None
                if when is not None and when >= since and beat.get("kind") == "failed":
                    failed, last = failed + 1, str(beat.get("error") or "")[:200]
                    at = _newest(at + _failure(when, beat))
    except (OSError, UnicodeDecodeError):
        pass
    return {"failed": failed, "last_error": last, "last_failures": at}


def _act_failures(since: float) -> Json:
    """Failed bin/act runs per playbook since SINCE, with the last error
    (as bin/evolve counts them)."""
    out: Json = {}
    for runs in sorted((hand.state_dir() / "act").glob("*/runs.jsonl")):
        failed, last, at = 0, None, []
        try:
            fh = runs.open(encoding="utf-8")
        except OSError:
            continue
        with fh:  # streamed: a long-lived log never sits in memory whole
            try:
                for line in fh:
                    try:
                        run = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(run, dict):
                        continue
                    when = next((w for w in (evolve_evidence.epoch(run.get(k)) for k in ("at", "ts", "date", "time"))
                                 if w is not None), None)
                    if when is None or when < since:
                        continue
                    errors = [a for a in run.get("actions") or [] if isinstance(a, dict) and a.get("status") == "error"]
                    if run.get("ok") is False or errors:
                        failed += 1
                        last = str(run.get("error") or (errors[0].get("action") if errors else ""))[:200]
                        at = _newest(at + _failure(when, run))
            except (OSError, UnicodeDecodeError):
                pass
        if failed:
            out[runs.parent.name] = {"failed": failed, "last_error": last, "last_failures": at}
    return out


def _num(value: float) -> int | float:
    value = round(float(value), 1)
    return int(value) if value.is_integer() else value


def _value(row: Any) -> float | None:
    """A stats value as a raw number, or None when the row holds something
    else. Rounded only when shown, so a change is worked out from the raw
    values."""
    if row is None or isinstance(row[0], bool) or not isinstance(row[0], (int, float)):
        return None
    return float(row[0])


def audience() -> list[Json]:
    """Each AUDIENCE number's latest `stats` value, when it was read, and
    where it stood about a day and a week before that reading (null when no
    snapshot is that old), as the stats board reads it (stats/board.py,
    `audience`). Only the fixed metrics are listed and only numbers are
    given: a latest row that is not a number leaves its metric out, an older
    one only its comparison. [] when there is none."""
    path = hand.state_dir() / "hands.db"
    if not path.is_file():
        return []
    out: list[Json] = []
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return []
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'stats'").fetchone():
            return []
        for source, metric in AUDIENCE:
            latest = conn.execute("SELECT value, date, at FROM stats WHERE source = ? AND metric = ?"
                                  " ORDER BY date DESC LIMIT 1", (source, metric)).fetchone()
            value = _value(latest)
            if value is None:
                continue
            row: Json = {"metric": f"{source}.{metric}", "value": _num(value),
                         "read_at": _iso(latest[2] * 1000) if isinstance(latest[2], (int, float)) else None}
            try:
                day = dt.datetime.strptime(str(latest[1]), "%Y-%m-%d")
            except ValueError:
                day = None
            for was, change, back in (("day_before", "change_1d", 1), ("week_before", "change_7d", 7)):
                before = None if day is None else _value(conn.execute(
                    "SELECT value FROM stats WHERE source = ? AND metric = ? AND date <= ? ORDER BY date DESC LIMIT 1",
                    (source, metric, (day - dt.timedelta(days=back)).strftime("%Y-%m-%d"))).fetchone())
                row[was] = None if before is None else _num(before)
                row[change] = None if before is None else _num(value - before)
            out.append(row)
    except sqlite3.Error:
        return out
    finally:
        conn.close()
    return out


def days_of(value: Any) -> int:
    if value is None:
        return DEFAULT_DAYS
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_DAYS <= value <= MAX_DAYS:
        raise ValueError(f"`days` is a whole number from {MIN_DAYS} to {MAX_DAYS}")
    return value


def pulse(days: int = DEFAULT_DAYS) -> Json:
    now = hand.now()
    since = now - days * 86400
    listed = jobs(since)
    failures = None if listed is None else {f"teeny.{j['job']}": j["failed"] + j.get("beats_failed", 0)
                                             for j in listed if j["failed"] or j.get("beats_failed")}
    acts = _act_failures(since)
    out: Json = {
        "at": _iso(now * 1000), "since": _iso(since * 1000), "days": days,
        "outcomes": evolve_evidence.outcomes(hand.state_dir() / "hands.db", since, now, failures, acts,
                                             hand.root() / "brain" / "cron.toml", CODE / "evals" / "cron.pinned.toml"),
    }
    # Added to the listed entries after the outcomes are cut to their size, so
    # a time never pushes an entry out, and what bin/evolve reads is unchanged.
    times = {f"bin/act {book}": info["last_failures"] for book, info in acts.items()}
    for j in listed or []:
        times[f"teeny.{j['job']}"] = _newest(j.get("last_failures", []) + j.get("last_beat_failures", []))
    for entry in out["outcomes"].get("failing", []):
        if times.get(entry["job"]):
            entry["last_failures"] = times[entry["job"]]
    if listed is None:
        out["jobs_unreadable"] = "the core's cron store could not be read"
    else:
        out["jobs"] = listed
    out["audience"] = audience()
    if not out["audience"]:
        out["audience_unread"] = NO_AUDIENCE
    # A notes file the mind's own file_write destroyed (bin/lib/notes_keep.py):
    # which one, by how much, and its last full copy. Only while one is open.
    shrunk = notes_keep.alerts()
    if shrunk:
        out["notes_shrunk"] = shrunk
    return out
