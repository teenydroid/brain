"""What the scripts did while the mind was not looking (docs/mind.md,
"Conscious and subconscious").

Most of Teeny runs without the agentic mind: the cron scripts that post,
answer, dream, build and evolve, each a fixed pipeline around a one-shot
model call. They are cheap, and they are Teeny's subconscious. The mind is
jailed to its home and cannot read their ledgers, so the broker hands it
this instead (`teeny pulse`): every job with its last run and how often it
failed, and what became of what the scripts made, which is the same reading
bin/evolve proposes from (bin/lib/evolve_evidence.py).

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

import evolve_evidence  # noqa: E402

Json = dict[str, Any]

#: The window a pulse may cover, in days.
MIN_DAYS, MAX_DAYS, DEFAULT_DAYS = 1, 7, 1
#: The mind's own wakes are its own doing, not the scripts'.
MIND_JOBS = ("teeny.mind", "mind.")

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


def _storage() -> Path:
    return Path(os.environ.get("TEENY_STORAGE") or hand.root() / "brain" / "storage.db")


def _iso(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)):
        return None
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    yours = _names(hand.root() / "brain" / "cron.toml")
    out = []
    for name, kind, expr, enabled, last, finished_ms, runs, failed in rows:
        name = str(name or "")
        if not name.startswith("teeny.") or name.startswith(MIND_JOBS):
            continue
        job = name.removeprefix("teeny.")
        row: Json = {"job": job, "kind": "agent" if kind == "agent" else "script", "schedule": expr,
                     "owner": "yours (brain/cron.toml)" if job in yours else "pinned (the operator's)",
                     "last": last, "last_at": _iso(finished_ms), "runs": int(runs or 0), "failed": int(failed or 0)}
        if enabled == 0:
            row["off"] = True
        out.append(row)
    return out


def _act_failures(since: float) -> Json:
    """Failed bin/act runs per playbook since SINCE, with the last error
    (as bin/evolve counts them)."""
    out: Json = {}
    for runs in sorted((hand.state_dir() / "act").glob("*/runs.jsonl")):
        failed, last = 0, None
        try:
            lines = runs.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
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
        if failed:
            out[runs.parent.name] = {"failed": failed, "last_error": last}
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
    failures = None if listed is None else {f"teeny.{j['job']}": j["failed"] for j in listed if j["failed"]}
    out: Json = {
        "at": _iso(now * 1000), "days": days,
        "outcomes": evolve_evidence.outcomes(hand.state_dir() / "hands.db", since, now, failures, _act_failures(since),
                                             hand.root() / "brain" / "cron.toml", CODE / "evals" / "cron.pinned.toml"),
    }
    if listed is None:
        out["jobs_unreadable"] = "the core's cron store could not be read"
    else:
        out["jobs"] = listed
    return out
