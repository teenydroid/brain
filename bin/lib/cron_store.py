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

- `core` (default): the core's own RPC, `cron_list` and `cron_runs`, which the
  core answers from OpenClaw automations (core/src/cron). It goes through
  `$TEENY_OH` (bin/oh) when set, else hand.core(), so it needs only the
  core's URL and token that every hand already has. It never runs the
  `openclaw` CLI: that needs the Gateway's token, which only the core holds.
  With the core down, or cron closed, the readers get Unavailable and do what
  they did when storage.db could not be read (nothing). The core maps
  OpenClaw's job (payload.kind `agentTurn` is an agent job, anything else a
  shell job whose command it unwraps from its `env -i ... sh -c` wrapper;
  schedule; state.nextRunAtMs / runningAtMs / lastRunAtMs) and a run
  (`runAtMs` start, `ts` end, status ok|error|skipped, summary + error as
  output) into the rows below.
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
#: a run can start between the caller sampling `now` and reading the jobs; a marker up to this far
#: ahead of `now` is still a live run, not a stale one
CLAIM_SKEW_MS = 60 * 1000


class Unavailable(Exception):
    """The store could not be read (no Gateway, no storage.db, a bad answer)."""


def backend() -> str:
    return os.environ.get("TEENY_CRON_BACKEND") or "core"


def storage_path() -> Path:
    """The old store, found as bin/lib/teeny.sh finds it."""
    if os.environ.get("TEENY_STORAGE"):
        return Path(os.environ["TEENY_STORAGE"])
    workspace = os.environ.get("OPENHUMAN_WORKSPACE")
    if workspace:
        return Path(workspace) / "storage.db"
    root = os.environ.get("TEENY_ROOT") or str(Path(__file__).resolve().parents[2])
    return Path(root) / "brain" / "storage.db"


# --- the core backend ----------------------------------------------------------------------------------


def _call(method: str, params: dict[str, Any]) -> Any:
    """One RPC to the core: through $TEENY_OH (bin/oh, as bin/seed-cron makes them) when set, else hand.core."""
    oh = os.environ.get("TEENY_OH")
    try:
        if oh:
            done = subprocess.run([oh, method, json.dumps(params, separators=(",", ":"))], capture_output=True,
                                  text=True, timeout=120)
            if done.returncode != 0:
                raise Unavailable((done.stderr or done.stdout).strip()[-300:] or f"{method} failed")
            return json.loads(done.stdout) if done.stdout.strip() else None
        code = Path(__file__).resolve().parents[2]
        if str(code / "hands" / "lib") not in sys.path:
            sys.path.insert(0, str(code / "hands" / "lib"))
        from teeny_hands import hand
        return hand.core(method, params, timeout=120)
    except Unavailable:
        raise
    except Exception as error:  # noqa: BLE001 - a core that is down or closed is just unavailable
        raise Unavailable(f"{method}: {error}") from error


def _rows(out: Any) -> list[Json]:
    """The dict rows of a core answer. Anything but a list is a broken answer, not an empty store:
    the readers must see Unavailable (pulse() sets jobs_unreadable), not "no jobs"."""
    if not isinstance(out, list):
        raise Unavailable(f"the core answered with {type(out).__name__}, not a list")
    return [x for x in out if isinstance(x, dict)]


def _core_job(job: Json) -> Json:
    """A job as the core's cron_list gives it (core/src/cron/map.ts view), in the readers' row."""
    schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else None
    return {
        "id": str(job.get("id")),
        "name": job.get("name"),
        "job_type": "agent" if job.get("job_type") == "agent" else "shell",
        "expression": schedule.get("expr") if schedule and schedule.get("kind") == "cron" else None,
        "enabled": job.get("enabled") is not False,
        "next_run_ms": job.get("next_run_ms"),
        "schedule": schedule,
        "command": job.get("command"),
        "agent_id": job.get("agent_id"),
        "prompt": job.get("prompt"),
        "created_ms": job.get("created_ms"),
        "running_ms": job.get("running_ms"),
        "last_run_ms": job.get("last_run_ms"),
    }


def _core_jobs() -> list[Json]:
    return [_core_job(j) for j in _rows(_call("cron_list", {})) if j.get("id") is not None]


def _core_runs() -> list[Json]:
    return [{"job_id": str(r.get("job_id")), "status": r.get("status"), "started_ms": r.get("started_ms"),
             "finished_ms": r.get("finished_ms"), "seq": r.get("seq"), "duration_ms": r.get("duration_ms"),
             "output": r.get("output")} for r in _rows(_call("cron_runs", {}))]


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


def _live_rows(conn: sqlite3.Connection, coll: str, deleted_too: bool = False) -> list[tuple[str, int, str]]:
    cols = _columns(conn)
    created = "created_ms" if "created_ms" in cols else "0"
    deleted = " and deleted = 0" if "deleted" in cols and not deleted_too else ""
    return conn.execute(
        f"with docs_ as materialized (select id, {created} as created_ms, body from docs "
        f"where coll = ?{deleted} and json_valid(body) and body != '{{}}') select id, created_ms, body from docs_",
        (coll,)).fetchall()


def _sq_jobs(deleted_too: bool = False) -> list[Json]:
    try:
        conn = _connect()
        try:
            rows = _live_rows(conn, "cron.jobs", deleted_too)
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


def jobs(include_deleted: bool = False) -> list[Json]:
    """Every job. Raises Unavailable when the store cannot be read. `include_deleted` is for readers
    that name the job behind a run on record (a deleted job's runs stay in the history); the
    scheduler-facing readers leave it off. The core's cron_list only has live jobs either way."""
    return _sq_jobs(include_deleted) if backend() == "sqlite" else _core_jobs()


def runs() -> list[Json]:
    """Every run on record, any order. Raises Unavailable."""
    return _sq_runs() if backend() == "sqlite" else _core_runs()


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
    return identity, isinstance(running, (int, float)) and -CLAIM_SKEW_MS <= now_ms - running < CLAIM_MAX_MS


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
