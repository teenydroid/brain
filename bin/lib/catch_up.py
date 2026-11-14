"""Run a cron job's missed slot once, late, instead of dropping it.

    python3 bin/lib/catch_up.py [--dry-run] [--via-oh] [--state DIR]
                                [--fresh NAME,...] [--rearmed NAME,...] < DESIRED_JSON

DESIRED_JSON is bin/lib/cron_policy.py's output. bin/seed-cron runs this at
every boot (`--via-oh`: through bin/oh, like the rest of seeding), and
bin/health calls run() on every check.

Why: the box is redeployed several times a day. A restart that cuts off a
run, or comes while a slot passes, leaves the job's next run on that slot,
held by a claim no live process renews; bin/seed-cron (at boot) and
bin/health (every 15 minutes) re-arm such a job, which moves it to its next
slot. For a job that runs every five minutes that is right. For a daily one
it drops the day: on 2026-11-13 and 11-14 a deploy cut off
`teeny.self-improve` mid-run and the boot's re-arm moved it to the next
day, twice, with no alarm; on 11-14 `teeny.moltbook-follow` lost its day
the same way to an 11:31 deploy.

So a job whose schedule says `catch_up = true` (cron_policy.py: a window
of `catch_up_hours`, default 6, always shorter than the gap between its
slots) gets its most recent slot run once now, through the core's own
`cron_run` (a queued run with its usual run record), when all of these hold:

- the slot is at most the window ago, and the job (enabled in the file and
  in the core) was defined before it: created earlier, and not added,
  changed or re-enabled by bin/seed-cron since (`--fresh`);
- no run of the job started at or after the slot (a run that failed held
  its slot: that is an error to look at, not a missed slot), and no live
  claim holds it (a run still going);
- the core has moved past the slot (its next run is later, after a re-arm)
  or GRACE_SECS have gone by without anyone taking it, so a core that has
  just booted and will take the slot itself on its next poll is left to it;
- the slot was not caught up before (the ledger, state/catch-up.json,
  written before the run is asked for, so a slot is never run twice across
  boots and checks; a run the core refused is given back and tried again);
- for a playbook (`bin/act`), it is not Teeny's quiet hours
  (bin/lib/rhythm.py): it waits for the morning while the slot is still in
  the window. The operator's pinned jobs (the dream runs at night) do not.

At most MAX_PER_RUN job is caught up per call, the oldest slot first, so a
boot after a long outage does not start every missed job at once: the rest
follow at the next checks, a quarter of an hour apart. bin/act still
applies the playbook's `max_per_day`, and the hands their own caps: a
catch-up is the same command the slot would have run.

A job whose next run still sits on the slot is re-armed first
(`cron_update` with its own schedule: the claim the dead run holds would
refuse a run for two hours), unless the caller just did that (`--rearmed`).

Prints (or returns) {caught_up: [{name, slot}], pending: [names left for
a later call], deferred: [names waiting out the quiet hours], errors:
{name: why}}; with --dry-run, what it would catch up, changing nothing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

CODE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE / "hands" / "lib"))
sys.path.insert(0, str(CODE / "bin" / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands.scrub import scrub  # noqa: E402

#: How long a slot nobody took is left to the core before it is caught up
#: (bin/health's STALE_SECS: a job this late counts as stuck).
GRACE_SECS = 600
#: A claim renewed this recently is a live run's (the core renews every 30 s).
LEASE_FRESH_SECS = 120
#: Jobs caught up per call.
MAX_PER_RUN = 1
#: Ledger entries kept for the operator.
LOG_KEEP = 50

Json = dict[str, Any]
Call = Callable[[str, Mapping[str, Any]], Any]


# --- cron arithmetic ------------------------------------------------------------------------


def _field(spec: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = int(part)
            end = high if step > 1 else start
        values.update(range(start, end + 1, step))
    return values


def _matcher(expr: str) -> Callable[[dt.datetime], bool]:
    minute, hour, dom, month, dow = expr.split()
    mins, hours, months = _field(minute, 0, 59), _field(hour, 0, 23), _field(month, 1, 12)
    doms = _field(dom, 1, 31)
    dows = {d % 7 for d in _field(dow, 0, 7)}  # 0 and 7 are both Sunday
    any_dom, any_dow = dom == "*", dow == "*"

    def match(t: dt.datetime) -> bool:
        if t.minute not in mins or t.hour not in hours or t.month not in months:
            return False
        day, weekday = t.day in doms, (t.isoweekday() % 7) in dows
        if any_dom or any_dow:
            return day and weekday
        return day or weekday  # cron: either day field, when both are restricted

    return match


def last_slot(expr: str, tz: str | None, now: float, earliest: float) -> float | None:
    """The latest minute at or before `now`, and not before `earliest`, at
    which `expr` fires in `tz`; None when there is none."""
    zone = ZoneInfo(tz or "UTC")
    match = _matcher(expr)
    t = int(now // 60) * 60
    while t >= earliest:
        if match(dt.datetime.fromtimestamp(t, zone)):
            return float(t)
        t -= 60
    return None


def _iso(seconds: float) -> str:
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- the core's storage, read-only ------------------------------------------------------------


def storage() -> Path:
    """The core's storage.db, found as bin/lib/teeny.sh finds it."""
    if os.environ.get("TEENY_STORAGE"):
        return Path(os.environ["TEENY_STORAGE"])
    workspace = os.environ.get("OPENHUMAN_WORKSPACE")
    return Path(workspace) / "storage.db" if workspace else hand.root() / "brain" / "storage.db"


def _lease_id(job_id: str, occurrence_ms: int) -> str:
    """tinyflows' `Key::of(["cron", Key::of([job_id, occurrence_ms])])`."""
    def part(text: str) -> str:
        return text.replace("%", "%25").replace("/", "%2F")
    return "cron/" + part(part(job_id) + "/" + str(occurrence_ms))


def _lease_live(conn: sqlite3.Connection, job_id: str, occurrence_ms: int, now_ms: int) -> bool:
    row = conn.execute("select updated_ms, body from docs where coll = 'tsd.leases' and id = ? and deleted = 0",
                       (_lease_id(job_id, occurrence_ms),)).fetchone()
    if row is None:
        return False
    try:
        lease = json.loads(row[1])
    except ValueError:
        return False
    return (isinstance(lease, dict) and not lease.get("released")
            and int(lease.get("expires_ms") or 0) > now_ms
            and int(row[0] or 0) >= now_ms - LEASE_FRESH_SECS * 1000)


def _core_jobs(conn: sqlite3.Connection) -> dict[str, Json]:
    rows = conn.execute(
        """
        with jobs as materialized (
          select id, created_ms, body from docs where coll = 'cron.jobs' and deleted = 0 and json_valid(body)
        )
        select id, created_ms, json_extract(body, '$.name'), coalesce(json_extract(body, '$.enabled'), 1),
               json_extract(body, '$.next_run_ms'), json_extract(body, '$.schedule'), json_extract(body, '$.created_ms')
        from jobs
        """).fetchall()
    out: dict[str, Json] = {}
    for job_id, created_ms, name, enabled, next_ms, schedule, body_created in rows:
        try:
            parsed = json.loads(schedule) if schedule else None
        except ValueError:
            parsed = None
        if not name or not isinstance(parsed, dict) or parsed.get("kind") != "cron" or name in out:
            continue
        out[str(name)] = {"id": str(job_id), "enabled": bool(enabled), "next_ms": int(next_ms or 0),
                          "schedule": parsed, "created_ms": int(body_created or created_ms or 0)}
    return out


def _ran_since(conn: sqlite3.Connection, job_id: str, since_ms: int) -> bool:
    row = conn.execute(
        """
        with runs as materialized (
          select body from docs where coll = 'cron.runs' and deleted = 0 and json_valid(body) and body != '{}'
        )
        select 1 from runs where json_extract(body, '$.job_id') = ? and json_extract(body, '$.started_ms') >= ?
        limit 1
        """, (job_id, since_ms)).fetchone()
    return row is not None


# --- the ledger ---------------------------------------------------------------------------------


class Ledger:
    """state/catch-up.json: per job, the last slot caught up (`done`) and when
    seeding last defined it (`since`), plus a short log. Held under a lock
    for the whole call, so a boot and a check never race for one slot."""

    def __init__(self, state: Path, readonly: bool = False):
        self.path = state / "catch-up.json"
        self.readonly = readonly
        self._lock = None
        if not readonly:
            state.mkdir(parents=True, exist_ok=True)
            self._lock = open(state / "catch-up.lock", "a+")

    def __enter__(self) -> "Ledger":
        if self._lock is not None:
            fcntl.flock(self._lock, fcntl.LOCK_EX)
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        self.data = {"done": dict(data.get("done") or {}), "since": dict(data.get("since") or {}),
                     "log": list(data.get("log") or [])}
        return self

    def save(self) -> None:
        if self.readonly:
            return
        self.data["log"] = self.data["log"][-LOG_KEEP:]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1) + "\n")
        tmp.replace(self.path)

    def __exit__(self, *exc: object) -> None:
        if self._lock is not None:
            fcntl.flock(self._lock, fcntl.LOCK_UN)
            self._lock.close()


# --- the decision -------------------------------------------------------------------------------


def _quiet(now: float) -> bool:
    import rhythm  # bin/lib/rhythm.py: TEENY_QUIET_HOURS, Teeny's own night
    return rhythm.is_night(now)


def _candidates(conn: sqlite3.Connection, desired: Iterable[Mapping[str, Any]], ledger: Json, now: float,
                rearmed: set[str]) -> tuple[list[Json], list[str]]:
    """The missed slots to catch up, oldest first, and the names waiting out
    the quiet hours."""
    now_ms = int(now * 1000)
    core = _core_jobs(conn)
    found: list[Json] = []
    deferred: list[str] = []
    for want in desired:
        window = int(want.get("catch_up_secs") or 0)
        name = str(want.get("name"))
        have = core.get(name)
        if window <= 0 or not want.get("enabled", True) or have is None or not have["enabled"]:
            continue
        schedule = have["schedule"]
        earliest = max(now - window, have["created_ms"] / 1000, float(ledger["since"].get(name) or 0) / 1000)
        slot = last_slot(str(schedule.get("expr") or ""), schedule.get("tz"), now, earliest)
        if slot is None:
            continue
        slot_ms = int(slot * 1000)
        if int(ledger["done"].get(name) or 0) >= slot_ms:
            continue
        if _ran_since(conn, have["id"], slot_ms) or _lease_live(conn, have["id"], slot_ms, now_ms):
            continue
        moved_on = name in rearmed or have["next_ms"] > slot_ms
        if not moved_on and now - slot < GRACE_SECS:
            continue
        if str(want.get("command") or "").startswith("bin/act ") and _quiet(now):
            deferred.append(name)
            continue
        found.append({"name": name, "id": have["id"], "slot": slot, "schedule": schedule,
                      "rearm": not moved_on and have["next_ms"] <= now_ms})
    found.sort(key=lambda c: (c["slot"], c["name"]))
    return found, deferred


def run(desired: Iterable[Mapping[str, Any]], call: Call, fresh: Iterable[str] = (), rearmed: Iterable[str] = (),
        dry_run: bool = False, state: Path | None = None, by: str = "health") -> Json:
    """Catches up at most MAX_PER_RUN missed slot (see the module docs)."""
    now = hand.now()
    out: Json = {"caught_up": [], "pending": [], "deferred": [], "errors": {}}
    state = state or (Path(os.environ["TEENY_STATE"]) if os.environ.get("TEENY_STATE") else hand.root() / "state")
    with Ledger(state, readonly=dry_run) as ledger:
        if not dry_run:
            for name in fresh:
                ledger.data["since"][name] = int(now * 1000)
            ledger.save()
        path = storage()
        if not path.is_file():
            return out
        try:
            conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
            try:
                found, out["deferred"] = _candidates(conn, desired, ledger.data, now, set(rearmed))
            finally:
                conn.close()
        except sqlite3.Error as error:
            out["errors"]["storage"] = scrub(str(error))
            return out
        out["pending"] = [c["name"] for c in found[MAX_PER_RUN:]]
        for job in found[:MAX_PER_RUN]:
            name, slot_ms = job["name"], int(job["slot"] * 1000)
            entry = {"name": name, "slot": _iso(job["slot"])}
            if dry_run:
                out["caught_up"].append(entry)
                continue
            before = ledger.data["done"].get(name)
            # Written before the run is asked for: never the same slot twice.
            ledger.data["done"][name] = slot_ms
            ledger.save()
            try:
                if job["rearm"]:
                    call("cron_update", {"job_id": job["id"], "patch": {"schedule": job["schedule"]}})
                call("cron_run", {"job_id": job["id"]})
            except Exception as error:  # noqa: BLE001 - refused: give the slot back, try at the next call
                out["errors"][name] = scrub(str(error) or type(error).__name__)
                if before is None:
                    ledger.data["done"].pop(name, None)
                else:
                    ledger.data["done"][name] = before
                ledger.save()
                continue
            ledger.data["log"].append({"at": _iso(now), "by": by, **entry})
            ledger.save()
            out["caught_up"].append(entry)
    return out


# --- the command (bin/seed-cron) ------------------------------------------------------------------


def via_oh(method: str, params: Mapping[str, Any]) -> Any:
    """One call through bin/oh (or $TEENY_OH), as bin/seed-cron makes them."""
    oh = os.environ.get("TEENY_OH") or str(CODE / "bin" / "oh")
    done = subprocess.run([oh, method, json.dumps(params, separators=(",", ":"))],
                          capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise hand.HandError((done.stderr or done.stdout).strip() or f"{method} failed")
    return json.loads(done.stdout) if done.stdout.strip() else None


def _names(text: str) -> list[str]:
    return [n for n in text.split(",") if n]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="catch_up.py")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--via-oh", action="store_true")
    parser.add_argument("--state")
    parser.add_argument("--fresh", default="")
    parser.add_argument("--rearmed", default="")
    args = parser.parse_args(argv)
    desired = json.load(sys.stdin)
    call: Call = via_oh if args.via_oh else hand.core
    out = run(desired, call, fresh=_names(args.fresh), rearmed=_names(args.rearmed), dry_run=args.dry_run,
              state=Path(args.state) if args.state else None, by="boot")
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
