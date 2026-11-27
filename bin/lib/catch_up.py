"""Run a cron job's missed slot once, late, instead of dropping it.

    python3 bin/lib/catch_up.py [--dry-run] [--via-oh] [--state DIR]
                                [--fresh NAME,...] [--rearmed NAME,...] < DESIRED_JSON
    python3 bin/lib/catch_up.py live      ids of jobs a recent claim holds (JSON)
    python3 bin/lib/catch_up.py rearm [--dry-run] [--via-oh] [--state DIR] < STUCK_JSON

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

(The scheduler is OpenClaw's automations now, with `cron.skipMissedJobs` on, so it never replays
a slot itself: "a claim a dead run holds" is the job's `state.runningAtMs` marker, read through
bin/lib/cron_store.py, and what is said below about the core taking a slot on its first poll no
longer happens. The rest holds.)

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
A job a recent claim holds is never re-armed or caught up: the core serves
before bin/seed-cron runs and may claim, on its first poll, a slot that
passed while the box was down; an edit would fence that claim (the run goes
on, unrecorded, its queued record deleted) and a catch-up would start a
second copy beside it. "Recent" means renewed in the last LEASE_FRESH_SECS,
so a claim of a process killed seconds before this boot also counts, and
that job waits for bin/health's next check. The core's scheduler owner id
would tell the two apart, but it is a random id the core keeps in memory and
writes nowhere we can read at boot.

The re-arm itself (rearm(): bin/seed-cron's `catch_up.py rearm`, and
bin/health) reads the slot's claim right before each `cron_update` and again
right after. The core can still claim the slot in between (a window of one
RPC); then the claim's holder and epoch have changed, the slot goes in the
ledger as done ("raced") and is never caught up: the core's run carries on
unrecorded rather than run twice. What is left is a run that is not
recorded, never a run that happens twice.

What it does not cover: a catch-up is a manual run, and the core keys a
manual run's claim on the job's *next* occurrence (`claim_job_now`) and holds
it for two hours past the run's last renewal. A catch-up a deploy cuts off
is not tried again (its slot is in the ledger), and its dead claim keeps the
core from taking that next slot until it lapses; if the job is a catch-up
job, bin/health re-arms and catches up that slot in turn. cron_policy.py
keeps the window two hours shorter than the gap between slots, so the next
slot always falls after such a claim could lapse, for runs under the core's
own limits.

A slot lost to the model provider is made up the same way, once, by
bin/health only (`provider_back`: its probe answered, and the latest
playbook draft after any provider failure did too). That is a playbook job
with a `make_up_secs` window (cron_policy.py: `make_up_hours`, or its
catch-up window) whose slot did have runs, every one of which bin/act
recorded as `failure: "provider"` in state/act/<playbook>/runs.jsonl. A
slot a skip, a refusal, a cap or a playbook error held is not made up, and
neither is a slot with no line in the log (a run cut off before it wrote
one). On 2026-11-14 a 402 from the provider failed every draft from 06:10
to 09:15 UTC, the noon Moltbook post among them, and nothing ran it again.
The rest is as for a missed slot: the window, the ledger, the quiet hours,
one job per call, and bin/act's `max_per_day`.

A schedule with names (MON) or with both day fields set (the core's cron
library and classic cron disagree on what that means) is not guessed at:
cron_policy.py refuses catch-up on one, and this skips it, said in `errors`.

Prints (or returns) {caught_up: [{name, slot}] (a make-up adds `lost:
"provider"`), pending: [names left for a later call], deferred: [names
waiting out the quiet hours], errors: {name: why}}; with --dry-run, what it
would catch up, changing nothing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

CODE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE / "hands" / "lib"))
sys.path.insert(0, str(CODE / "bin" / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands.scrub import scrub  # noqa: E402

import cron_store  # noqa: E402  (bin/lib: the one reader of the core's cron jobs and runs)

#: How long a slot nobody took is left to the core before it is caught up
#: (bin/health's STALE_SECS: a job this late counts as stuck).
GRACE_SECS = 600
#: A claim renewed this recently is a live run's (the core renews every 30 s).
LEASE_FRESH_SECS = 120
#: Jobs caught up per call.
MAX_PER_RUN = 1
#: Ledger entries kept for the operator.
LOG_KEEP = 50
#: The core's occurrence claim (tinyflows-schedule OCCURRENCE_LEASE): each
#: renewal sets its expiry this far ahead.
OCCURRENCE_LEASE_MS = 2 * 3600 * 1000
#: How long to wait for the ledger before saying it is busy.
LOCK_WAIT_SECS = 10.0

Json = dict[str, Any]
Call = Callable[[str, Mapping[str, Any]], Any]


# --- cron arithmetic ------------------------------------------------------------------------


def _field(spec: str, low: int, high: int) -> set[int]:
    """One numeric cron field; a name (MON) or `?` raises ValueError."""
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
    if not any_dom and not any_dow:
        raise ValueError(f"{expr!r} sets both day fields, which cron implementations read differently")

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


# --- the core's jobs and runs, read-only (bin/lib/cron_store.py) ---------------------------------


def _claim(job: Json, occurrence_ms: int, now_ms: int) -> tuple[Any, bool]:
    """(identity, live) of the claim on one slot of a job: cron_store.claim. On OpenClaw a slot is
    claimed while the job's `runningAtMs` marker is set; the old core's tsd.leases rows are the same
    thing for a storage.db read through the `sqlite` backend."""
    return cron_store.claim(job, occurrence_ms, now_ms)


def live_job_ids() -> list[str]:
    """The jobs whose next run a live run holds right now (read-only)."""
    try:
        jobs = cron_store.jobs()
    except cron_store.Unavailable:
        return []
    now_ms = int(hand.now() * 1000)
    return sorted(str(job["id"]) for job in jobs if job["next_run_ms"] and _claim(job, int(job["next_run_ms"]), now_ms)[1])


def _core_jobs(jobs: Iterable[Json]) -> dict[str, Json]:
    """The cron-scheduled jobs by name, as the catch-up reads them."""
    out: dict[str, Json] = {}
    for job in jobs:
        name, schedule = job.get("name"), job.get("schedule")
        if not name or not isinstance(schedule, dict) or schedule.get("kind") != "cron" or name in out:
            continue
        out[str(name)] = {"id": str(job["id"]), "enabled": bool(job["enabled"]), "next_ms": int(job["next_run_ms"] or 0),
                          "schedule": schedule, "created_ms": int(job.get("created_ms") or 0), "job": job}
    return out


def _ran_since(runs: Iterable[Json], job_id: str, since_ms: int) -> bool:
    return any(str(run.get("job_id")) == job_id and isinstance(run.get("started_ms"), (int, float))
               and run["started_ms"] >= since_ms for run in runs)


# --- the ledger ---------------------------------------------------------------------------------


class Busy(Exception):
    pass


class Ledger:
    """state/catch-up.json: per job, the last slot caught up (`done`) and when
    seeding last defined it (`since`), plus a short log. Held under a lock
    for the whole call, so a boot and a check never race for one slot."""

    def __init__(self, state: Path, readonly: bool = False, wait: float = LOCK_WAIT_SECS):
        self.path = state / "catch-up.json"
        self.readonly = readonly
        self.wait = wait
        self._lock = None
        if not readonly:
            state.mkdir(parents=True, exist_ok=True)
            self._lock = open(state / "catch-up.lock", "a+")

    def __enter__(self) -> "Ledger":
        if self._lock is not None:
            deadline = time.monotonic() + self.wait
            while True:
                try:
                    fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        self._lock.close()
                        self._lock = None
                        raise Busy("the catch-up ledger is busy (another boot or check holds it)") from None
                    time.sleep(0.1)
        self.note = ""
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError):
            data, self.note = {}, "unreadable"
        if not isinstance(data, dict):
            data, self.note = {}, "not an object"

        def stamps(value: Any) -> dict[str, int]:
            if not isinstance(value, dict):
                return {}
            return {str(k): int(v) for k, v in value.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)}

        log = data.get("log")
        self.data = {"done": stamps(data.get("done")), "since": stamps(data.get("since")),
                     "log": [e for e in log if isinstance(e, dict)] if isinstance(log, list) else []}
        kept = sum(len(self.data[k]) for k in ("done", "since", "log"))
        given = sum(len(v) for k in ("done", "since", "log") if isinstance(v := data.get(k), (dict, list)))
        if not self.note and (kept != given or any(k in data and not isinstance(data[k], (dict, list))
                                                   for k in ("done", "since", "log"))):
            self.note = "had entries of the wrong type"
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


def _lost_to_provider(state: Path, command: str, slot: float) -> bool:
    """Whether a playbook job's runs since `slot` all failed on the model
    provider: at least one line in its run log, and every one `failure:
    "provider"` (bin/act)."""
    argv = command.split()
    if argv[:1] != ["bin/act"] or len(argv) != 2:
        return False
    try:
        raw = (state / "act" / argv[1] / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    since = []
    for text in raw[-200:]:
        try:
            line = json.loads(text)
            at = dt.datetime.fromisoformat(str(line["at"]).replace("Z", "+00:00")).timestamp()
        except (ValueError, KeyError, TypeError):
            continue
        if at >= slot:
            since.append(line)
    return bool(since) and all(line.get("failure") == "provider" for line in since)


def _quiet(now: float) -> bool:
    import rhythm  # bin/lib/rhythm.py: TEENY_QUIET_HOURS, Teeny's own night
    return rhythm.is_night(now)


def _candidates(jobs: list[Json], runs: list[Json], desired: Iterable[Mapping[str, Any]], ledger: Json, now: float,
                rearmed: set[str], errors: dict[str, str], state: Path | None = None,
                provider_back: bool = False) -> tuple[list[Json], list[str]]:
    """The missed slots to catch up, and with `provider_back` the slots lost
    to the model provider to make up, oldest first, and the names waiting out
    the quiet hours. A job whose schedule cannot be read goes in `errors`."""
    now_ms = int(now * 1000)
    core = _core_jobs(jobs)
    found: list[Json] = []
    deferred: list[str] = []
    for want in desired:
        window = int(want.get("catch_up_secs") or 0)
        make_up = int(want.get("make_up_secs") or 0) if provider_back and state is not None else 0
        name = str(want.get("name"))
        have = core.get(name)
        if max(window, make_up) <= 0 or not want.get("enabled", True) or have is None or not have["enabled"]:
            continue
        schedule = have["schedule"]
        earliest = max(now - max(window, make_up), have["created_ms"] / 1000,
                       float(ledger["since"].get(name) or 0) / 1000)
        try:
            slot = last_slot(str(schedule.get("expr") or ""), schedule.get("tz"), now, earliest)
        except (ValueError, KeyError) as error:  # one odd schedule never stops the others
            errors[name] = scrub(f"schedule not read: {error}")
            continue
        if slot is None:
            continue
        slot_ms = int(slot * 1000)
        if int(ledger["done"].get(name) or 0) >= slot_ms:
            continue
        # A run that started (or was queued) at the slot holds it; so does a
        # live claim on it, and on the job's current next run (a run the core
        # took after a re-arm moved it on).
        if _claim(have["job"], slot_ms, now_ms)[1] or _claim(have["job"], have["next_ms"], now_ms)[1]:
            continue
        lost = False
        if _ran_since(runs, have["id"], slot_ms):
            # A run held the slot, unless every run of it failed on the
            # model provider and it is back: then it is made up.
            lost = (make_up > 0 and now - slot <= make_up and state is not None
                    and _lost_to_provider(state, str(want.get("command") or ""), slot))
            if not lost:
                continue
        elif now - slot > window:
            continue
        moved_on = lost or name in rearmed or have["next_ms"] > slot_ms
        if not moved_on and now - slot < GRACE_SECS:
            continue
        if str(want.get("command") or "").startswith("bin/act ") and _quiet(now):
            deferred.append(name)
            continue
        found.append({"name": name, "id": have["id"], "slot": slot, "schedule": schedule, "lost": lost,
                      "rearm": not moved_on and have["next_ms"] <= now_ms})
    found.sort(key=lambda c: (c["slot"], c["name"]))
    return found, deferred


def run(desired: Iterable[Mapping[str, Any]], call: Call, fresh: Iterable[str] = (), rearmed: Iterable[str] = (),
        dry_run: bool = False, state: Path | None = None, by: str = "health",
        lock_wait: float = LOCK_WAIT_SECS, provider_back: bool = False) -> Json:
    """Catches up at most MAX_PER_RUN missed slot, or with `provider_back`
    makes up one the model provider lost (see the module docs)."""
    now = hand.now()
    out: Json = {"caught_up": [], "pending": [], "deferred": [], "errors": {}}
    state = _state_dir(state)
    try:
        with Ledger(state, readonly=dry_run, wait=lock_wait) as ledger:
            if ledger.note:
                out["errors"]["ledger"] = f"state/catch-up.json {ledger.note}; started afresh without the bad part"
            return _run(ledger, out, desired, call, fresh, rearmed, dry_run, now, by, state, provider_back)
    except Busy as busy:  # raised only while taking the lock
        out["errors"]["ledger"] = str(busy)
        return out


def _run(ledger: Ledger, out: Json, desired: Iterable[Mapping[str, Any]], call: Call, fresh: Iterable[str],
         rearmed: Iterable[str], dry_run: bool, now: float, by: str, state: Path | None = None,
         provider_back: bool = False) -> Json:
    if not dry_run:
        for name in fresh:
            ledger.data["since"][name] = int(now * 1000)
        ledger.save()
    if cron_store.backend() == "sqlite" and not cron_store.storage_path().is_file():
        return out
    try:
        jobs, runs = cron_store.jobs(), cron_store.runs()
    except cron_store.Unavailable as error:
        out["errors"]["storage"] = scrub(str(error))
        return out
    found, out["deferred"] = _candidates(jobs, runs, desired, ledger.data, now, set(rearmed), out["errors"],
                                         state, provider_back)
    out["pending"] = [c["name"] for c in found[MAX_PER_RUN:]]
    for job in found[:MAX_PER_RUN]:
        name, slot_ms = job["name"], int(job["slot"] * 1000)
        entry = {"name": name, "slot": _iso(job["slot"]), **({"lost": "provider"} if job["lost"] else {})}
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


# --- the guarded re-arm (bin/seed-cron at boot, bin/health) -----------------------------------


def _state_dir(state: Path | None) -> Path:
    return state or (Path(os.environ["TEENY_STATE"]) if os.environ.get("TEENY_STATE") else hand.root() / "state")


def _epoch_ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def rearm(jobs: Iterable[Mapping[str, Any]], call: Call, dry_run: bool = False, state: Path | None = None) -> Json:
    """Patches each stuck job's own schedule back (`cron_update`), which moves
    its next run to the next slot, unless a recent claim holds its slot.
    `jobs`: [{id, name, next_run (ISO), schedule}]. Returns {rearmed, running,
    raced, errors}; a job the core claimed during its update is `raced`,
    and its slot is put in the ledger so no catch-up runs it again."""
    out: Json = {"rearmed": [], "running": [], "raced": [], "errors": {}}

    def claim(job_id: str, occurrence_ms: int) -> tuple[Any, bool]:
        if cron_store.backend() == "sqlite" and not cron_store.storage_path().is_file():
            return None, False
        return cron_store.claim_now(job_id, occurrence_ms, int(hand.now() * 1000))

    for job in jobs:
        name, job_id = str(job["name"]), str(job["id"])
        try:
            occurrence_ms = int(job["next_ms"]) if job.get("next_ms") else _epoch_ms(str(job["next_run"]))
        except (KeyError, ValueError, TypeError):
            out["errors"][name] = "no next run to read"
            continue
        try:
            before, live = claim(job_id, occurrence_ms)
        except cron_store.Unavailable as error:
            before, live = None, False
            out["errors"]["claims"] = scrub(f"could not read the claims, re-armed without checking: {error}")
        if live:
            out["running"].append(name)
            continue
        if dry_run:
            out["rearmed"].append(name)
            continue
        try:
            call("cron_update", {"job_id": job_id, "patch": {"schedule": job["schedule"]}})
        except Exception as error:  # noqa: BLE001 - one job the core refused does not stop the rest
            out["errors"][name] = scrub(str(error) or type(error).__name__)
            continue
        try:
            after, _ = claim(job_id, occurrence_ms)
        except cron_store.Unavailable:
            after = before
        if after != before:
            out["raced"].append(name)
            try:
                with Ledger(_state_dir(state)) as ledger:
                    ledger.data["done"][name] = max(occurrence_ms, int(ledger.data["done"].get(name) or 0))
                    ledger.data["log"].append({"at": _iso(hand.now()), "by": "raced", "name": name,
                                               "slot": _iso(occurrence_ms / 1000)})
                    ledger.save()
            except Busy as busy:
                out["errors"][name] = f"claimed while re-armed, and not recorded: {busy}"
            continue
        out["rearmed"].append(name)
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
    if argv == ["live"]:
        print(json.dumps(live_job_ids()))
        return 0
    if argv[:1] == ["rearm"]:
        sub = argparse.ArgumentParser(prog="catch_up.py rearm")
        sub.add_argument("--dry-run", action="store_true")
        sub.add_argument("--via-oh", action="store_true")
        sub.add_argument("--state")
        args = sub.parse_args(argv[1:])
        out = rearm(json.load(sys.stdin), via_oh if args.via_oh else hand.core, dry_run=args.dry_run,
                    state=Path(args.state) if args.state else None)
        print(json.dumps(out))
        return 0
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
