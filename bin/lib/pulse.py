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

import cron_store  # noqa: E402
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
    try:
        core_jobs, core_runs = cron_store.jobs(), cron_store.runs()
    except cron_store.Unavailable:
        return None
    lo = int(since * 1000)
    last = cron_store.last_runs(core_runs)
    names = {job["id"]: job.get("name") for job in core_jobs}
    # (name, kind, expr, enabled, last status, last finished_ms, runs in the window, failed in the window)
    rows = []
    for job in sorted(core_jobs, key=lambda j: str(j.get("name") or "")):
        in_window = [r for r in core_runs if str(r.get("job_id")) == job["id"]
                     and isinstance(r.get("finished_ms"), (int, float)) and r["finished_ms"] >= lo]
        run = last.get(job["id"]) or {}
        rows.append((job.get("name"), job.get("job_type"), job.get("expression"), job["enabled"], run.get("status"),
                     run.get("finished_ms"), len(in_window), sum(1 for r in in_window if r.get("status") == "error")))
    # the newest failures of each job in the window, MAX_FAILURES a job
    failed_by_job: dict[str, list[Any]] = {}
    for run in core_runs:
        if (run.get("status") == "error" and isinstance(run.get("finished_ms"), (int, float)) and run["finished_ms"] >= lo
                and str(run.get("job_id")) in names):
            failed_by_job.setdefault(str(run["job_id"]), []).append(run["finished_ms"])
    failed_rows = [(names[job_id], ms) for job_id, mss in failed_by_job.items()
                   for ms in sorted(mss, reverse=True)[:MAX_FAILURES]]
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
        if not enabled:
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


def inbox_passes(since: float) -> Json | None:
    """How the mind's wakes did with their inbox pass (teeny-guardrails writes
    one line per mind run to state/mind/inbox-pass.jsonl): the wakes given the
    block, and those that neither replied nor marked anything (`owed`), with
    the times of the latest. None when no run was recorded in the window."""
    path = hand.state_dir() / "mind" / "inbox-pass.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-2000:]
    except (OSError, ValueError):  # missing, or not UTF-8: no record, never a broken pulse
        return None
    given, owed = 0, []
    for line in lines:
        try:
            row = json.loads(line)
            at = dt.datetime.fromisoformat(str(row["at"]).replace("Z", "+00:00")).timestamp()
        except (ValueError, KeyError, TypeError):
            continue
        if at < since or row.get("agent") != "mind" or not row.get("block"):
            continue
        given += 1
        if row.get("owed"):
            owed.append(row["at"])
    if not given:
        return None
    return {"wakes_given_the_block": given, "wakes_that_skipped_it": len(owed), "last_skipped": owed[-MAX_FAILURES:][::-1]}


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
    passes = inbox_passes(since)
    if passes:
        out["inbox_pass"] = passes
    shrunk = notes_keep.alerts()
    if shrunk:
        out["notes_shrunk"] = shrunk
    return out
