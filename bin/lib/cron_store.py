"""The one place Teeny's scripts read the core's cron jobs and their runs.

    python3 bin/lib/cron_store.py jobs        every job as JSON
    python3 bin/lib/cron_store.py runs        every run as JSON
    python3 bin/lib/cron_store.py crons       the table bin/crons prints
    python3 bin/lib/cron_store.py runs-of NAME   the last 20 runs of one job

bin/crons, health.py, pulse.py, catch_up.py, evolve.py, hands desk.py and
tasks/queue.py used to open the core's storage.db and select from its `docs`
table. The scheduler is OpenClaw's automations now, which keep their jobs
and run history in OpenClaw's own state database, so they all read through
here instead and get back the rows they always used:

  job: {id, name, job_type ('agent'|'shell'), expression, enabled (bool),
        next_run_ms, schedule {kind, expr, tz | at}, command, agent_id,
        prompt, created_ms, running_ms, last_run_ms}
  run: {job_id, status ('ok'|'error'|'skipped'|'running'), started_ms,
        finished_ms (None while running), seq, duration_ms, output}

Backends (TEENY_CRON_BACKEND):

- `openclaw` (default): `$OPENCLAW_BIN automations list --all --json` and
  `... runs --all --json` (docs cli/cron.md). Needs the Gateway running, as
  every automations command does; with it down the readers get Unavailable
  and do what they did when storage.db could not be read (nothing).
  OpenClaw's job fields (docs: the CronJob schema) map as: payload.kind
  `agentTurn` is an agent job (agentId, payload.message), any other payload
  is a shell job whose command is the last argument of the `env -i ... sh -c`
  wrapper bin/core's cron module stores; schedule.{expr,tz,at}; state.
  nextRunAtMs / runningAtMs / lastRunAtMs. A run's `runAtMs` is its start,
  `ts` its end, `status` ok|error|skipped, and its output is `summary` plus
  `error`.
- `sqlite`: OpenHuman's old store (the `docs` table at $TEENY_STORAGE), read
  read-only. Kept for the readers' tests, which build that table as fixtures,
  and to read a storage.db from before the move.

What replaces tsd.leases (a claim on one slot, renewed while a run lives):
OpenClaw marks a job `state.runningAtMs` while a run is in flight and clears
it when the run ends (a stale marker is cleared by the next Gateway start,
docs cli/doctor/checks.md). So a slot is claimed while `runningAtMs` is set
and under CLAIM_MAX_MS old; the fence the re-arm compares across an edit is
the pair (runningAtMs, lastRunAtMs), which changes when a run starts.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

Json = dict[str, Any]

#: A running marker older than this is a dead run's, not a live claim (the
#: shell limit is 1800 s; the core's old claims lapsed after two hours too).
CLAIM_MAX_MS = 2 * 3600 * 1000
#: One page of run history, and the most pages read.
RUNS_PAGE = 200
RUNS_MAX_PAGES = 15


class Unavailable(Exception):
    """The store could not be read (no Gateway, no storage.db, a bad answer)."""


def backend() -> str:
    return os.environ.get("TEENY_CRON_BACKEND") or "openclaw"


def storage_path() -> Path:
    """The old store, found as bin/lib/teeny.sh finds it."""
    if os.environ.get("TEENY_STORAGE"):
        return Path(os.environ["TEENY_STORAGE"])
    workspace = os.environ.get("OPENHUMAN_WORKSPACE")
    if workspace:
        return Path(workspace) / "storage.db"
    root = os.environ.get("TEENY_ROOT") or str(Path(__file__).resolve().parents[2])
    return Path(root) / "brain" / "storage.db"


# --- the OpenClaw backend ---------------------------------------------------------------------------


def _automations(*args: str) -> Any:
    bin_ = os.environ.get("OPENCLAW_BIN") or "openclaw"
    try:
        done = subprocess.run([bin_, "automations", *args, "--json"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        raise Unavailable(f"openclaw automations {args[0]}: {error}") from error
    if done.returncode != 0:
        raise Unavailable((done.stderr or done.stdout).strip()[-300:] or f"openclaw automations {args[0]} failed")
    try:
        return json.loads(done.stdout) if done.stdout.strip() else None
    except ValueError as error:
        raise Unavailable(f"openclaw automations {args[0]} printed no JSON") from error


def _items(out: Any, *keys: str) -> list[Json]:
    if isinstance(out, list):
        return [x for x in out if isinstance(x, dict)]
    if isinstance(out, dict):
        for key in keys:
            if isinstance(out.get(key), list):
                return [x for x in out[key] if isinstance(x, dict)]
    return []


def command_of(argv: Any) -> str | None:
    """The command inside the `env -i HOME=.. PATH=.. LANG=.. timeout .. sh -c CMD` wrapper
    the core's cron module stores (core/src/cron/map.ts shellArgv); another argv is joined."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return None
    if argv[0] == "env" and "sh" in argv:
        i = argv.index("sh")
        if argv[i + 1 : i + 2] == ["-c"] and len(argv) > i + 2:
            return argv[i + 2]
    return " ".join(argv)


def _oc_job(job: Json) -> Json:
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    sched = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
    state = job.get("state") if isinstance(job.get("state"), dict) else {}
    agent = payload.get("kind") == "agentTurn"
    schedule: Json | None = None
    if sched.get("kind") == "cron":
        schedule = {"kind": "cron", "expr": sched.get("expr"), "tz": sched.get("tz") or "UTC"}
    elif sched.get("kind") == "at":
        schedule = {"kind": "at", "at": sched.get("at")}
    elif sched:
        schedule = {"kind": sched.get("kind")}
    return {
        "id": str(job.get("id")),
        "name": job.get("name"),
        "job_type": "agent" if agent else "shell",
        "expression": sched.get("expr") if sched.get("kind") == "cron" else None,
        "enabled": job.get("enabled") is not False,
        "next_run_ms": state.get("nextRunAtMs"),
        "schedule": schedule,
        "command": None if agent else command_of(payload.get("argv")),
        "agent_id": job.get("agentId") if agent else None,
        "prompt": payload.get("message") if agent else None,
        "created_ms": job.get("createdAtMs"),
        "running_ms": state.get("runningAtMs"),
        "last_run_ms": state.get("lastRunAtMs"),
    }


def _oc_run(entry: Json) -> Json:
    started = entry.get("runAtMs")
    finished = entry.get("ts")
    if finished is None and started is not None and entry.get("durationMs") is not None:
        finished = started + entry["durationMs"]
    status = entry.get("status")
    output = "\n".join(str(entry[k]) for k in ("summary", "error") if entry.get(k))
    return {"job_id": str(entry.get("jobId")), "status": status, "started_ms": started, "finished_ms": finished,
            "seq": started if started is not None else finished, "duration_ms": entry.get("durationMs"), "output": output}


def _oc_jobs() -> list[Json]:
    return [_oc_job(j) for j in _items(_automations("list", "--all"), "jobs") if j.get("id") is not None]


def _oc_runs() -> list[Json]:
    out: list[Json] = []
    for page in range(RUNS_MAX_PAGES):
        got = _items(_automations("runs", "--all", "--limit", str(RUNS_PAGE), "--offset", str(page * RUNS_PAGE)),
                     "entries", "runs", "items")
        out.extend(_oc_run(e) for e in got if e.get("jobId") is not None)
        if len(got) < RUNS_PAGE:
            break
    return out


# --- the old storage.db backend ---------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    path = storage_path()
    if not path.is_file():
        raise Unavailable(f"no storage at {path}")
    try:
        return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as error:
        raise Unavailable(str(error)) from error


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("pragma table_info(docs)")}


def _live_rows(conn: sqlite3.Connection, coll: str) -> list[tuple[str, int, str]]:
    cols = _columns(conn)
    created = "created_ms" if "created_ms" in cols else "0"
    deleted = " and deleted = 0" if "deleted" in cols else ""
    return conn.execute(
        f"with docs_ as materialized (select id, {created} as created_ms, body from docs "
        f"where coll = ?{deleted} and json_valid(body) and body != '{{}}') select id, created_ms, body from docs_",
        (coll,)).fetchall()


def _sq_jobs() -> list[Json]:
    try:
        conn = _connect()
        try:
            rows = _live_rows(conn, "cron.jobs")
        finally:
            conn.close()
    except sqlite3.Error as error:
        raise Unavailable(str(error)) from error
    out = []
    for job_id, created_ms, text in rows:
        body = json.loads(text)
        if not isinstance(body, dict):
            continue
        enabled = body.get("enabled")
        schedule = body.get("schedule")
        out.append({
            "id": str(job_id), "name": body.get("name"), "job_type": body.get("job_type"),
            "expression": body.get("expression"), "enabled": enabled is None or bool(enabled),
            "next_run_ms": body.get("next_run_ms"), "schedule": schedule if isinstance(schedule, dict) else None,
            "command": body.get("command"), "agent_id": body.get("agent_id"), "prompt": body.get("prompt"),
            "created_ms": body.get("created_ms") or created_ms or None, "running_ms": None, "last_run_ms": None})
    return out


def _sq_runs() -> list[Json]:
    try:
        conn = _connect()
        try:
            rows = _live_rows(conn, "cron.runs")
        finally:
            conn.close()
    except sqlite3.Error as error:
        raise Unavailable(str(error)) from error
    out = []
    for _id, _created, text in rows:
        body = json.loads(text)
        if isinstance(body, dict):
            out.append({"job_id": body.get("job_id"), "status": body.get("status"), "started_ms": body.get("started_ms"),
                        "finished_ms": body.get("finished_ms"), "seq": body.get("seq"),
                        "duration_ms": body.get("duration_ms"), "output": body.get("output")})
    return out


def lease_id(job_id: str, occurrence_ms: int) -> str:
    """tinyflows' `Key::of(["cron", Key::of([job_id, occurrence_ms])])`."""
    def part(text: str) -> str:
        return text.replace("%", "%25").replace("/", "%2F")
    return "cron/" + part(part(job_id) + "/" + str(occurrence_ms))


def _sq_lease(job_id: str, occurrence_ms: int) -> tuple[int, Json] | None:
    try:
        conn = _connect()
        try:
            row = conn.execute("select updated_ms, body from docs where coll = 'tsd.leases' and id = ? and deleted = 0",
                               (lease_id(job_id, occurrence_ms),)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as error:
        raise Unavailable(str(error)) from error
    if row is None:
        return None
    try:
        body = json.loads(row[1])
    except ValueError:
        return None
    return (int(row[0] or 0), body) if isinstance(body, dict) else None


# --- what the readers call ----------------------------------------------------------------------------


def jobs() -> list[Json]:
    """Every job. Raises Unavailable when the store cannot be read."""
    return _sq_jobs() if backend() == "sqlite" else _oc_jobs()


def runs() -> list[Json]:
    """Every run on record, any order. Raises Unavailable."""
    return _sq_runs() if backend() == "sqlite" else _oc_runs()


def last_runs(all_runs: list[Json]) -> dict[str, Json]:
    """Each job's newest run (highest seq)."""
    newest: dict[str, Json] = {}
    for run in all_runs:
        key = str(run.get("job_id"))
        if key not in newest or (run.get("seq") or 0) > (newest[key].get("seq") or 0):
            newest[key] = run
    return newest


#: occurrence leases (OCCURRENCE_LEASE_MS, LEASE_FRESH_SECS in catch_up.py)
_LEASE_MS = 2 * 3600 * 1000
_LEASE_FRESH_SECS = 120


def claim(job: Json, occurrence_ms: int, now_ms: int) -> tuple[Any, bool]:
    """(identity, live) of the claim on JOB's slot at OCCURRENCE_MS. `live`: a recent run holds
    it. `identity` is what a re-arm compares before and after an edit to see a run start in
    between: (holder, epoch) of the old lease, or (runningAtMs, lastRunAtMs) on OpenClaw."""
    if backend() == "sqlite":
        found = _sq_lease(str(job["id"]), int(occurrence_ms))
        if found is None:
            return None, False
        updated_ms, lease = found
        identity = (lease.get("holder"), lease.get("epoch"))
        if lease.get("released"):
            return identity, False
        expires_ms = int(lease.get("expires_ms") or 0)
        renewed_ms = max(updated_ms, expires_ms - _LEASE_MS)
        return identity, expires_ms > now_ms and renewed_ms >= now_ms - _LEASE_FRESH_SECS * 1000
    running = job.get("running_ms")
    identity = (running, job.get("last_run_ms"))
    return identity, isinstance(running, (int, float)) and 0 <= now_ms - running < CLAIM_MAX_MS


def claim_now(job_id: str, occurrence_ms: int, now_ms: int) -> tuple[Any, bool]:
    """claim() for a job read afresh by id (the re-arm reads it again right after an edit)."""
    if backend() == "sqlite":
        return claim({"id": job_id}, occurrence_ms, now_ms)
    for job in jobs():
        if job["id"] == job_id:
            return claim(job, occurrence_ms, now_ms)
    return None, False


# --- bin/crons ------------------------------------------------------------------------------------------


def _utc(ms: Any) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if isinstance(ms, (int, float)) else ""


def _table(rows: list[list[str]], header: list[str]) -> str:
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    line = lambda r: "  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip()  # noqa: E731
    return "\n".join([line(header), line(["-" * w for w in widths]), *(line(r) for r in rows)])


def main(argv: list[str]) -> int:
    try:
        if argv == ["jobs"]:
            print(json.dumps(jobs()))
        elif argv == ["runs"]:
            print(json.dumps(runs()))
        elif argv == ["crons"]:
            last = last_runs(runs())
            rows = []
            for job in sorted(jobs(), key=lambda j: str(j["name"])):
                run = last.get(job["id"]) or {}
                rows.append([job["name"] or "", job["job_type"] or "", job["expression"] or "",
                             (job["schedule"] or {}).get("tz") or "", "yes" if job["enabled"] else "no",
                             _utc(job["next_run_ms"]), run.get("status") or "", _utc(run.get("finished_ms"))])
            print(_table(rows, ["name", "type", "expr", "tz", "on_", "next_run", "last", "last_at"]))
        elif argv[:1] == ["runs-of"] and len(argv) == 2:
            name = argv[1]
            ids = {j["id"] for j in jobs() if j["name"] == name}
            mine = sorted((r for r in runs() if r["job_id"] in ids), key=lambda r: r.get("seq") or 0, reverse=True)[:20]
            rows = [[_utc(r["started_ms"]), r.get("duration_ms") if r.get("duration_ms") is not None else "",
                     r.get("status") or "", str(r.get("output") or "")[:120].replace("\n", " ")] for r in mine]
            print(_table(rows, ["started", "ms", "status", "output"]))
        else:
            print("usage: cron_store.py jobs | runs | crons | runs-of NAME", file=sys.stderr)
            return 2
    except Unavailable as error:
        print(f"cron_store: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
