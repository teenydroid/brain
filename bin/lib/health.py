"""Whether Teeny's model answers, and an alarm when it stops
(evals/cron.pinned.toml, health; DIRECTIVE.md, level 1).

    python3 bin/lib/health.py

On 2026-11-11 the provider key was revoked. For seven hours every agent
turn and every judge call failed, guard holds piled up, cron rows said
`error`, and nobody noticed: nothing was watching. This is the watcher.
It is pinned, so the brain cannot switch it off.

Every run:

1. Asks the model one tiny question through the core
   (`inference_agent_chat_simple`, as the guard's judge does). An error or
   an empty answer is a failure.
2. Counts what else the last hour shows: guard holds whose judge could not
   be reached (state/hands.db) and failed cron runs (the core's
   storage.db, read-only, as bin/crons reads it).
3. Keeps the count in state/health.json. FAILURES_TO_ALARM failures in a
   row (half an hour at the job's 15 minutes) raise one alarm: a `health ·
   alarm` stimulus for Teeny, and one issue on HEALTH_ISSUE_REPO
   (teenydroid/brain) through hands/gh, under its DRY_RUN. While the alarm
   is open nothing more is filed; an issue that could not be opened is
   tried again on the next run. The first answer after that closes both: a
   `health · recovered` stimulus, a last comment, the issue closed.
4. When the model answered, re-screens the guard holds an outage made
   (teeny_hands.guard.rescreen): at most ten, oldest first, none younger
   than two minutes. A held message the judge now finds clean goes back to
   its queue; a real verdict stays held; a held draft of Teeny's own expires
   and is never sent. A failed probe skips it: the judge is that model.
5. Looks for cron jobs stuck behind their schedule (`stale_crons`): enabled,
   a next run more than STALE_SECS in the past, and no run holding that
   slot. On 2026-11-11 the box rebooted mid-run, the dead runs' claims held
   three jobs' slots, and Teeny read no mail or mentions for 75 minutes.
   Each of Teeny's own (`teeny.*`) is re-armed: its schedule is patched back
   through the core (`cron_update`), which moves its next run to the next
   slot, as bin/seed-cron does at boot. Any stuck job raises a second alarm
   of the same kind (a `health · alarm` stimulus, one issue), cleared once
   every job is current again. It is watched whether the model answers or
   not.
6. Prints one JSON line: `ok` (the check ran), `healthy` (the model
   answered), what it did, `rescreen`, the re-screen's counts (null when it
   did not run), `stale_crons`, `rearmed` and `cron_alarm`, and
   `moltbook_unverified`: how many of the last day's Moltbook writes are
   still hidden behind an unsolved challenge, when fewer than nine in ten
   of at least three are visible (null otherwise). That one is a count on
   the line only; it raises no alarm.

Once a day (`python3 bin/lib/health.py daily`, the pinned `health-daily`
job; docs/roadmap.md, A4) it also tells Teeny how yesterday (the UTC day
before this one) went, as one `health · daily` stimulus: what the model
cost, failed cron runs by job, failed playbook runs (bin/act) by playbook,
failed beats of the mind, the doctor's checks that are not ok, and guard
holds by direction and category. Counts and names Teeny's own code chose
(job, playbook, check and category names), never the text of a hold, an
error or anything a stranger wrote. A second run the same day adds nothing.

The fallback route (docs/roadmap.md, A18; `reroute`): with
SPINE_FALLBACK_MODEL in secrets/spine.env (a second model on the same
endpoint), FAILURES_TO_FALLBACK failed probes in a row and a fallback that
answers its own probe (`model_override`) move every workload of the running
core to it (`config_update_model_settings`, as bin/spine does at boot), so
the next beat runs on it. While on the fallback each run also asks the main
model, and the first answer moves the core back. Each move leaves a line in
state/diary-notes.jsonl, which the night's reflection puts on the diary page
(bin/lib/reflect.py, `diary_notes`), and a `health · route` stimulus. A
reboot starts on the main model again. The alarm still follows the probe of
whatever model the core is on.

The stimuli wait for a mind that cannot think until the model is back; the
issue is the alarm a person sees meanwhile. So it is filed without the
model judge (`check_fields(judge=False)`; the lexicon and the links still
run): its words are this file's fixed sentences, and the judge is the
model that is down. What it quotes of the core's error is scrubbed of
anything that looks like a key or a URL first.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Mapping

CODE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE / "hands" / "lib"))

from teeny_hands import hand, stimuli  # noqa: E402
from teeny_hands.gh import actions as gh  # noqa: E402
from teeny_hands.guard import rescreen  # noqa: E402
from teeny_hands.scrub import scrub as _scrub  # noqa: E402

#: Failed probes in a row that raise the alarm.
FAILURES_TO_ALARM = 2
#: How long the model may take to say one word.
PROBE_TIMEOUT = 60.0
PROBE = "Health check: reply with the one word ok."
#: How far back the holds and cron runs are counted.
WINDOW_SECS = 3600
DEFAULT_REPO = "teenydroid/brain"
#: The judge's own words when it fails closed (teeny_hands.guard.judge).
JUDGE_UNREACHABLE = "the judge could not be reached%"
MAX_ERROR_CHARS = 300
#: How far behind its schedule an enabled cron job may fall before it counts
#: as stuck (bin/doctor --live uses the same ten minutes).
STALE_SECS = 600
#: A run in flight renews the claim on its slot (the core's occurrence lease)
#: every 30 s; a claim nobody renewed for this long is a gone process's.
LEASE_FRESH_SECS = 120
#: Moltbook hides a write until its challenge is solved: the share of the
#: last MOLTBOOK_WINDOW_SECS's writes that must be visible, once at least
#: MOLTBOOK_MIN_WRITES went out.
MOLTBOOK_VERIFIED_SHARE = 0.9
MOLTBOOK_MIN_WRITES = 3
MOLTBOOK_WINDOW_SECS = 86400

Json = dict[str, Any]

# --- the probe and the signals --------------------------------------------------------


def scrub(error: str) -> str:
    """One line of an error, safe to keep and to publish (teeny_hands.scrub)."""
    return _scrub(error, MAX_ERROR_CHARS)


def _words(reply: Any) -> str:
    """The model's text out of the core's answer (a string, or an object
    carrying it under one of the usual names)."""
    for _ in range(4):
        if isinstance(reply, str):
            return reply
        if not isinstance(reply, dict):
            return ""
        reply = next((reply[k] for k in ("response", "text", "message", "content") if k in reply), None)
    return reply if isinstance(reply, str) else ""


def probe(model: str | None = None) -> str | None:
    """None when the model answered; what went wrong when it did not. With
    MODEL, that model on the core's endpoint (`model_override`), not the
    one the core is set to."""
    params: Json = {"message": PROBE, **({"model_override": model} if model else {})}
    try:
        reply = hand.core("inference_agent_chat_simple", params, timeout=PROBE_TIMEOUT)
    except Exception as error:  # noqa: BLE001 - any failure to answer is the thing watched for
        return scrub(str(error) or type(error).__name__)
    return None if _words(reply).strip() else "the model answered with nothing"


def judge_holds(since: float) -> int:
    """Guard holds since `since` whose judge could not be reached."""
    stamp = dt.datetime.fromtimestamp(since, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        conn = hand.db()
        try:
            row = conn.execute(
                "SELECT count(*) FROM guard_holds WHERE reason LIKE ? AND at >= ?", (JUDGE_UNREACHABLE, stamp)
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.OperationalError:  # no holds table yet
        return 0
    return int(row[0])


def moltbook_unverified(since: float) -> int | None:
    """Teeny's Moltbook writes since `since` that nobody can see yet (any
    accepted status but `verified` or `posted`), when at least
    MOLTBOOK_MIN_WRITES went out and fewer than MOLTBOOK_VERIFIED_SHARE of
    them are visible; None otherwise. A count on the line, never an alarm:
    the model is fine, and the issue is filed by nothing."""
    from teeny_hands.moltbook import rules as moltbook

    counted = ",".join("?" * len(moltbook.COUNTED))
    visible = ",".join("?" * len(moltbook.ACCEPTED))
    try:
        conn = hand.db()
        try:
            total, seen = 0, 0
            for table in ("moltbook_posts", "moltbook_comments"):
                row = conn.execute(
                    f"SELECT count(*), coalesce(sum(status IN ({visible})), 0) FROM {table}"
                    f" WHERE at >= ? AND status IN ({counted})",
                    (*moltbook.ACCEPTED, since, *moltbook.COUNTED),
                ).fetchone()
                total, seen = total + int(row[0]), seen + int(row[1])
        finally:
            conn.close()
    except sqlite3.OperationalError:  # Moltbook never written to
        return None
    if total < MOLTBOOK_MIN_WRITES or seen >= MOLTBOOK_VERIFIED_SHARE * total:
        return None
    return total - seen


def _storage() -> Path:
    """The core's storage.db, found as bin/lib/teeny.sh finds it."""
    if os.environ.get("TEENY_STORAGE"):
        return Path(os.environ["TEENY_STORAGE"])
    workspace = os.environ.get("OPENHUMAN_WORKSPACE")
    return Path(workspace) / "storage.db" if workspace else hand.root() / "brain" / "storage.db"


def cron_errors(since: float, until: float | None = None) -> dict[str, int]:
    """Failed cron runs since `since` (and before `until`), by job name.
    Read-only; bin/crons' query, narrowed to valid JSON in the cron
    collections first."""
    path = _storage()
    if not path.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            rows = conn.execute(
                """
                with jobs as materialized (
                  select id, body from docs where coll = 'cron.jobs' and deleted = 0 and json_valid(body)
                ),
                runs as materialized (
                  select body from docs where coll = 'cron.runs' and deleted = 0 and json_valid(body) and body != '{}'
                )
                select json_extract(jobs.body, '$.name') as name, count(*)
                from runs join jobs on jobs.id = json_extract(runs.body, '$.job_id')
                where json_extract(runs.body, '$.status') = 'error'
                  and json_extract(runs.body, '$.finished_ms') >= ?
                  and json_extract(runs.body, '$.finished_ms') < ?
                group by name order by name
                """,
                (int(since * 1000), int((until if until is not None else 1e12) * 1000)),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    return {str(name): int(count) for name, count in rows}


def _lease_id(job_id: str, occurrence_ms: int) -> str:
    """The id of the core's claim on one occurrence of a job: tinyflows'
    `Key::of(["cron", Key::of([job_id, occurrence_ms])])`."""
    def part(text: str) -> str:
        return text.replace("%", "%25").replace("/", "%2F")
    return "cron/" + part(part(job_id) + "/" + str(occurrence_ms))


def _running(conn: sqlite3.Connection, job_id: str, occurrence_ms: int, now_ms: int) -> bool:
    """Whether a live run holds the job's slot: its claim is unreleased,
    unexpired and was renewed lately."""
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


def _iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stale_crons(now: float) -> list[Json]:
    """Enabled cron jobs whose next run is more than STALE_SECS past with no
    live run holding it, by name: `{id, name, next_run, schedule}`. Read-only,
    from the core's storage.db as `cron_errors` reads it."""
    path = _storage()
    if not path.is_file():
        return []
    now_ms = int(now * 1000)
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            rows = conn.execute(
                """
                with jobs as materialized (
                  select id, body from docs where coll = 'cron.jobs' and deleted = 0 and json_valid(body)
                )
                select id, coalesce(json_extract(body, '$.name'), id), json_extract(body, '$.next_run_ms'),
                       json_extract(body, '$.schedule')
                from jobs
                where coalesce(json_extract(body, '$.enabled'), 1) != 0
                  and json_extract(body, '$.next_run_ms') < ?
                order by 2
                """,
                (now_ms - STALE_SECS * 1000,),
            ).fetchall()
            stale = [(job_id, name, int(next_ms), schedule) for job_id, name, next_ms, schedule in rows
                     if not _running(conn, job_id, int(next_ms), now_ms)]
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    out = []
    for job_id, name, next_ms, schedule in stale:
        try:
            parsed = json.loads(schedule) if schedule else None
        except ValueError:
            parsed = None
        out.append({"id": str(job_id), "name": str(name), "next_run": _iso(next_ms), "schedule": parsed})
    return out


def rearm(stale: list[Json]) -> tuple[list[str], dict[str, str]]:
    """Patches each stuck `teeny.*` cron job's own schedule back, which moves
    its next run to the next slot (the core's own jobs are not Teeny's to
    touch). The names re-armed, and what went wrong with the rest."""
    rearmed: list[str] = []
    errors: dict[str, str] = {}
    for job in stale:
        schedule = job["schedule"]
        if not job["name"].startswith("teeny.") or not isinstance(schedule, dict) or schedule.get("kind") != "cron":
            continue
        try:
            hand.core("cron_update", {"job_id": job["id"], "patch": {"schedule": schedule}})
        except Exception as error:  # noqa: BLE001 - one job the core refused does not stop the rest
            errors[job["name"]] = scrub(str(error) or type(error).__name__)
            continue
        print(f"[health] rearm {job['name']} (next_run was {job['next_run']})", file=sys.stderr)
        rearmed.append(job["name"])
    return rearmed, errors


# --- state ------------------------------------------------------------------------------------

FRESH: Json = {"failures": 0, "first_failed_at": None, "last_failed_at": None, "last_ok_at": None,
               "last_error": None, "alarm": None, "cron_alarm": None}


def _path() -> Path:
    return hand.state_dir() / "health.json"


def load() -> Json:
    try:
        state = json.loads(_path().read_text())
    except (FileNotFoundError, ValueError):
        return dict(FRESH)
    return {**FRESH, **state} if isinstance(state, dict) else dict(FRESH)


def save(state: Mapping[str, Any]) -> None:
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(_path())


# --- words ----------------------------------------------------------------------------------------


def _when(at: float) -> str:
    return dt.datetime.fromtimestamp(at, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _span(secs: float) -> str:
    minutes = max(0, round(secs / 60))
    if minutes < 120:
        return f"{minutes} minutes"
    return f"{minutes // 60} hours {minutes % 60} minutes"


def _crons(errors: Mapping[str, int]) -> str:
    return ", ".join(f"{name} ({n})" for name, n in errors.items()) or "none"


def _repo() -> str:
    return hand.setting("HEALTH_ISSUE_REPO", DEFAULT_REPO) or DEFAULT_REPO


def _issue(state: Mapping[str, Any], holds: int, errors: Mapping[str, int]) -> Json:
    first = state["first_failed_at"]
    return {
        "repo": _repo(),
        "title": f"Teeny's model is not answering (since {_when(first)})",
        "body": (
            f"The pinned health check (bin/health) asks the model one short question through the core every"
            f" 15 minutes. The last {state['failures']} asks in a row failed.\n\n"
            f"- failing since: {_when(first)}\n"
            f"- last error: {state['last_error']}\n"
            f"- guard holds in the last hour because the judge could not be reached: {holds}\n"
            f"- cron runs that failed in the last hour: {_crons(errors)}\n\n"
            "Until the model answers again Teeny cannot think, reply, post or judge anything. Check the model"
            " provider's key and the model URL the core is given (bin/spine), then `bin/crons` and the guard"
            " holds.\n\n"
            "This issue is closed by the same check when the model answers again."
        ),
    }


def _open_issue(alarm: Json, issue: Json) -> None:
    """Opens the alarm's issue, once; what happened goes on the alarm."""
    alarm["issue_error"] = None
    alarm["issue_dry_run"] = False
    try:
        got = gh.open_issue(issue, judge=False)
    except hand.Refused as refused:
        alarm["issue_error"] = scrub(f"refused: {refused.reason}")
        return
    except hand.HandError as error:
        alarm["issue_error"] = scrub(str(error))
        return
    if got.get("dry_run"):
        alarm["issue_dry_run"] = True
    else:
        alarm["issue"] = got.get("number")


def _issue_line(alarm: Mapping[str, Any]) -> str:
    if alarm.get("issue"):
        return f"the health check opened {_repo()}#{alarm['issue']} so the operators see it."
    if alarm.get("issue_dry_run"):
        return "no issue was opened: DRY_RUN is on."
    return f"opening an issue failed ({alarm.get('issue_error')}); the next check tries again."


def _raise(state: Json, now: float, holds: int, errors: Mapping[str, int]) -> Json:
    first = state["first_failed_at"]
    alarm: Json = {"ref": f"outage:{int(first)}", "opened_at": now, "issue": None}
    _open_issue(alarm, _issue(state, holds, errors))
    stimuli.add(
        "health", "alarm", alarm["ref"],
        f"health alarm: your model has not answered for {_span(now - first)} ({state['failures']} checks in a row"
        f" since {_when(first)}). last error: {state['last_error']}. in the last hour the guard held {holds}"
        f" draft(s) because the judge could not be reached, and these cron runs failed: {_crons(errors)}."
        f" until the model answers you cannot think, reply, post or judge. {_issue_line(alarm)}",
        payload={"failures": state["failures"], "first_failed_at": first, "error": state["last_error"],
                 "judge_holds": holds, "cron_errors": dict(errors), "issue": alarm["issue"]},
    )
    return alarm


def _close_issue(alarm: Mapping[str, Any], comment: str) -> Json:
    """Closes the alarm's issue, if it opened one; `{issue_error}` when that failed."""
    if not alarm.get("issue"):
        return {}
    try:
        gh.close_issue({"repo": _repo(), "number": alarm["issue"], "comment": comment}, judge=False)
    except (hand.Refused, hand.HandError) as error:
        return {"issue_error": scrub(getattr(error, "reason", None) or str(error))}
    return {}


def _clear(state: Json, alarm: Json, now: float, holds: int, errors: Mapping[str, int]) -> Json:
    first = state["first_failed_at"] or alarm["opened_at"]
    down = f"about {_span(now - first)}, from {_when(first)} to {_when(now)}"
    closed = _close_issue(alarm, f"The model answers again. It was not answering for {down}. Closing.")
    issue = (f"the alarm issue {_repo()}#{alarm['issue']} was closed." if alarm.get("issue") and not closed
             else "")
    stimuli.add(
        "health", "recovered", alarm["ref"],
        f"health: your model answers again. it was not answering for {down}. while it was out the guard held"
        f" what the judge could not read ({holds} in the last hour) and these cron runs failed: {_crons(errors)}."
        f" {issue}".rstrip(),
        payload={"first_failed_at": first, "recovered_at": now, "issue": alarm.get("issue")},
    )
    return closed


# --- stuck cron jobs ----------------------------------------------------------------------------


def _stuck(stale: list[Json]) -> str:
    return ", ".join(f"{job['name']} (next run was {job['next_run']})" for job in stale)


def _rearm_line(rearmed: list[str], rearm_errors: Mapping[str, str]) -> str:
    done = (f"re-armed: {', '.join(rearmed)}; they run again at their next slot." if rearmed
            else "none was re-armed.")
    failed = "".join(f" could not re-arm {name}: {why}." for name, why in rearm_errors.items())
    return done + failed


def _cron_issue(stale: list[Json], rearmed: list[str], rearm_errors: Mapping[str, str]) -> Json:
    since = min(job["next_run"] for job in stale)
    return {
        "repo": _repo(),
        "title": f"Teeny's cron jobs stopped running (since {since})",
        "body": (
            f"The pinned health check (bin/health) found enabled cron jobs whose next run is more than"
            f" {STALE_SECS // 60} minutes in the past with no run holding their slot: the core's scheduler is"
            " not running them.\n\n"
            f"- stuck: {_stuck(stale)}\n"
            f"- {_rearm_line(rearmed, rearm_errors)}\n\n"
            "A job is re-armed by patching its own schedule back through the core (`cron_update`), which moves"
            " its next run to the next slot; bin/seed-cron does the same at every boot. Look for a claim the"
            " core never released (a run cut off by a restart) with `bin/crons`.\n\n"
            "This issue is closed by the same check once every enabled job is current again."
        ),
    }


def _watch_crons(state: Json, now: float) -> Json:
    """Finds and re-arms stuck cron jobs, and raises, keeps or clears their
    alarm. What goes on the check's line."""
    stale = stale_crons(now)
    rearmed, rearm_errors = rearm(stale)
    out: Json = {"stale_crons": [{"name": job["name"], "next_run": job["next_run"]} for job in stale],
                 "rearmed": rearmed, "cron_alarm": None}
    if rearm_errors:
        out["rearm_errors"] = rearm_errors
    alarm = state.get("cron_alarm")
    if stale and alarm:
        if not alarm.get("issue"):
            _open_issue(alarm, _cron_issue(stale, rearmed, rearm_errors))
        out["cron_alarm"] = "open"
    elif stale:
        alarm = {"ref": f"cron:{int(now)}", "opened_at": now, "issue": None}
        _open_issue(alarm, _cron_issue(stale, rearmed, rearm_errors))
        stimuli.add(
            "health", "alarm", alarm["ref"],
            f"health alarm: these cron jobs stopped running: {_stuck(stale)}."
            f" {_rearm_line(rearmed, rearm_errors)} {_issue_line(alarm)}",
            payload={"stale_crons": out["stale_crons"], "rearmed": rearmed, "rearm_errors": rearm_errors,
                     "issue": alarm["issue"]},
        )
        out["cron_alarm"] = "raised"
    elif alarm:
        out.update(_close_issue(alarm, "Every enabled cron job is on schedule again. Closing."))
        closed = " the alarm issue was closed." if alarm.get("issue") and "issue_error" not in out else ""
        stimuli.add("health", "recovered", alarm["ref"], f"health: every cron job runs on schedule again.{closed}",
                    payload={"opened_at": alarm["opened_at"], "recovered_at": now, "issue": alarm.get("issue")})
        out["cron_alarm"] = "cleared"
        alarm = None
    state["cron_alarm"] = alarm
    if alarm:
        out.update(cron_issue=alarm.get("issue"), cron_issue_error=alarm.get("issue_error"))
    return out


# --- yesterday, as one stimulus (docs/roadmap.md, A4) ------------------------------------------

#: A name that may go into the stimulus as it is: a job, playbook, check or
#: category name Teeny's own code chose. Anything else is counted as "other".
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:/()=,-]{0,79}")


def _named(name: Any) -> str:
    return name if isinstance(name, str) and _NAME.fullmatch(name) else "other"


def _stamp(at: float) -> str:
    return dt.datetime.fromtimestamp(at, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _in(line: Mapping[str, Any], since: float, until: float) -> bool:
    """Whether a runs.jsonl line's `at` (ISO, UTC) falls in [since, until)."""
    at = line.get("at")
    if not isinstance(at, str):
        return False
    try:
        when = dt.datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return since <= when.timestamp() < until


def _lines(path: Path) -> list[Json]:
    try:
        raw = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in raw:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def act_failures(since: float, until: float) -> dict[str, int]:
    """Playbook runs (state/act/<name>/runs.jsonl, bin/act) that failed in
    [since, until), by playbook."""
    out: dict[str, int] = {}
    for path in sorted((hand.state_dir() / "act").glob("*/runs.jsonl")):
        n = sum(1 for line in _lines(path) if line.get("ok") is False and _in(line, since, until))
        if n:
            out[_named(path.parent.name)] = out.get(_named(path.parent.name), 0) + n
    return out


def failed_beats(since: float, until: float) -> int:
    """Beats of the mind (state/tick/runs.jsonl) that failed in [since, until)."""
    return sum(1 for line in _lines(hand.state_dir() / "tick" / "runs.jsonl")
               if line.get("kind") == "failed" and _in(line, since, until))


def doctor_checks() -> dict[str, list[str]]:
    """The last doctor report's checks that are not ok, by status (`fail`,
    `warn`): their names only, never their detail."""
    try:
        report = json.loads((hand.state_dir() / "doctor.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, list[str]] = {}
    for c in report.get("checks", []) if isinstance(report, dict) else []:
        if isinstance(c, dict) and c.get("status") not in ("ok", None):
            out.setdefault(_named(c.get("status")), []).append(_named(c.get("check")))
    return out


def holds_by_category(since: float, until: float) -> dict[str, int]:
    """Guard holds made in [since, until), as `<direction> <category>`: never
    their text or reason."""
    try:
        conn = hand.db()
        try:
            rows = conn.execute(
                "SELECT direction, category, count(*) FROM guard_holds WHERE at >= ? AND at < ?"
                " GROUP BY direction, category ORDER BY direction, category",
                (_stamp(since), _stamp(until)),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError:  # no holds table yet
        return {}
    out: dict[str, int] = {}
    for direction, category, n in rows:
        key = f"{_named(direction)} {_named(category)}"
        out[key] = out.get(key, 0) + int(n)
    return out


def model_cost() -> Json | None:
    """What the model cost over the last day, from the core's cost report
    (as hands/wallet costs reads it); None when the core could not say."""
    try:
        report = hand.core("cost_report", {"days": 1}, timeout=PROBE_TIMEOUT)
    except Exception:  # noqa: BLE001 - a cost the core cannot give is left out, not fatal
        return None
    totals = report.get("totals") if isinstance(report, Mapping) else None
    if not isinstance(totals, Mapping):
        return None
    try:
        return {"usd": round(float(totals.get("cost_usd") or 0.0), 4), "calls": int(totals.get("calls") or 0)}
    except (TypeError, ValueError):
        return None


def _counts(counts: Mapping[str, int]) -> str:
    return ", ".join(f"{name} {n}" for name, n in counts.items()) or "none"


def daily() -> Json:
    """Yesterday (UTC) as one `health · daily` stimulus, once per day."""
    with hand.lock("health"):
        until = hand.day_start()
        since = until - 86400
        day = dt.datetime.fromtimestamp(since, dt.timezone.utc).strftime("%Y-%m-%d")
        cost = model_cost()
        crons = cron_errors(since, until)
        acts = act_failures(since, until)
        beats = failed_beats(since, until)
        doctor = doctor_checks()
        holds = holds_by_category(since, until)
        trouble = bool(crons or acts or beats or doctor.get("fail") or holds)
        spent = (f"the model cost ${cost['usd']:.2f} over {cost['calls']} call(s)" if cost
                 else "what the model cost is unknown (the core did not say)")
        doctor_line = "; ".join(f"{status}: {', '.join(names)}" for status, names in doctor.items()) or "all ok"
        text = (
            f"health, yesterday ({day} UTC): {spent}. failed cron runs: {_counts(crons)}."
            f" failed playbook runs: {_counts(acts)}. failed beats of your mind: {beats}."
            f" the doctor: {doctor_line}. guard holds: {_counts(holds)}."
            " these are counts and names only, never what was held or said."
        )
        if trouble:
            text += " if one of these is yours to look into, it is in your own logs and ledgers."
        payload = {"day": day, "cost": cost, "cron_errors": crons, "act_failures": acts, "failed_beats": beats,
                   "doctor": doctor, "holds": holds, "trouble": trouble}
        added = stimuli.add("health", "daily", f"daily:{day}", text, payload=payload)
        return {"ok": True, "daily": day, "added": added, **payload}


# --- the check ------------------------------------------------------------------------------------


def _rescreen() -> Json:
    """The re-screen's counts; a re-screen that broke is reported, and the
    health check still answers."""
    try:
        return {"rescreen": rescreen.run()}
    except Exception as error:  # noqa: BLE001 - the watcher must not fall over its own errand
        return {"rescreen": None, "rescreen_error": scrub(f"{type(error).__name__}: {error}")}


# --- the fallback route (docs/roadmap.md, A18) ----------------------------------------------

#: Failed probes in a row after which the core is switched to the fallback model.
FAILURES_TO_FALLBACK = 2
#: Where a switch is noted for the night's diary page (bin/lib/reflect.py).
NOTES = "diary-notes.jsonl"
#: The workloads bin/spine points at the model, every one of them.
_PROVIDERS = ("chat_provider", "reasoning_provider", "agentic_provider", "coding_provider", "vision_provider",
              "memory_provider")


def routes() -> tuple[str | None, str | None, str | None]:
    """(the main model, the fallback model, the fallback's context window),
    from secrets/spine.env as bin/spine reads it: SPINE_MODEL, and
    SPINE_FALLBACK_MODEL, a second model on the same endpoint. No fallback
    (or one equal to the main) is no fallback."""
    try:
        env = hand._parse_env_file((hand.secrets_dir() / "spine.env").read_text(encoding="utf-8"))
    except OSError:
        return None, None, None
    main = (env.get("SPINE_MODEL") or "").strip() or None
    fallback = (env.get("SPINE_FALLBACK_MODEL") or "").strip() or None
    window = (env.get("SPINE_FALLBACK_CONTEXT_WINDOW") or "").strip() or None
    return main, (fallback if fallback != main else None), window


def use_model(model: str, window: str | None = None) -> None:
    """Points every workload of the running core at MODEL on its endpoint,
    as bin/spine does at boot. The next turn (a beat, a judge call) runs on
    it."""
    provider = f"local-openai:{model}"
    hand.core("config_update_model_settings", {"default_model": model, **{p: provider for p in _PROVIDERS}},
              timeout=PROBE_TIMEOUT)
    if window and window.isdigit() and int(window) > 0:
        hand.core("inference_update_model_settings",
                  {"model_registry": [{"id": model, "provider": "local-openai", "context_window": int(window)}]},
                  timeout=PROBE_TIMEOUT)


def note(text: str, now: float) -> None:
    """One line for the night's diary page (state/diary-notes.jsonl; the
    reflection adds the notes since the last one), written by this script,
    never by a model."""
    with (hand.state_dir() / NOTES).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": now, "text": text}, ensure_ascii=False) + "\n")


def _switch(state: Json, model: str, route: str, now: float, window: str | None, why: str) -> None:
    use_model(model, window)
    if state.get("route", "main") != route:
        text = (f"my model stopped answering, so the spine moved me to the fallback model, {model}. {why}"
                if route == "fallback" else f"my main model, {model}, answers again; the spine moved me back to it.")
        note(text, now)
        stimuli.add("health", "route", f"route:{route}:{int(now)}", text,
                    payload={"route": route, "model": model})
    state.update(route=route, route_model=model, route_since=now)


def reroute(state: Json, error: str | None, now: float) -> Json:
    """After the probe: FAILURES_TO_FALLBACK failures in a row and a
    fallback that answers move the core to it; on the fallback, a main
    model that answers moves it back. Each move is a diary note and a
    `health · route` stimulus. Nothing without SPINE_FALLBACK_MODEL. A
    reboot puts the core back on the main model (bin/spine); the next
    failures move it again."""
    main, fallback, window = routes()
    if not main or not fallback:
        return {}
    route = state.get("route", "main")
    out: Json = {"route": route}
    try:
        if error is not None and int(state.get("failures") or 0) >= FAILURES_TO_FALLBACK:
            if probe(fallback) is None:
                _switch(state, fallback, "fallback", now, window, f"the main one said: {error}")
            elif route == "fallback" and probe(main) is None:
                _switch(state, main, "main", now, None, "")
            else:
                out["route_error"] = "the fallback model did not answer either"
        elif error is None and route == "fallback" and probe(main) is None:
            _switch(state, main, "main", now, None, "")
    except Exception as exc:  # noqa: BLE001 - a failed switch is reported, never raised
        out["route_error"] = scrub(f"the switch failed: {exc}")
    out["route"] = state.get("route", "main")
    return out


def check() -> Json:
    with hand.lock("health"):
        now = hand.now()
        state = load()
        error = probe()
        holds = judge_holds(now - WINDOW_SECS)
        errors = cron_errors(now - WINDOW_SECS)
        out: Json = {"ok": True, "healthy": error is None, "judge_holds": holds, "cron_errors": errors,
                     "alarm": None, "issue": None, "rescreen": None,
                     "moltbook_unverified": moltbook_unverified(now - MOLTBOOK_WINDOW_SECS)}
        alarm = state.get("alarm")
        if error is None:
            if alarm:
                out.update(_clear(state, alarm, now, holds, errors))
                out.update(alarm="cleared", issue=alarm.get("issue"))
            state.update(failures=0, first_failed_at=None, last_ok_at=now, last_error=None, alarm=None)
            out.update(_rescreen())
        else:
            state.update(failures=int(state.get("failures") or 0) + 1, last_failed_at=now, last_error=error)
            state["first_failed_at"] = state.get("first_failed_at") or now
            out["error"] = error
            if state["failures"] >= FAILURES_TO_ALARM:
                if alarm:
                    if not alarm.get("issue"):
                        _open_issue(alarm, _issue(state, holds, errors))
                    out["alarm"] = "open"
                else:
                    alarm = _raise(state, now, holds, errors)
                    out["alarm"] = "raised"
                state["alarm"] = alarm
                out.update(issue=alarm.get("issue"), issue_error=alarm.get("issue_error"),
                           issue_dry_run=alarm.get("issue_dry_run"))
        out["failures"] = state["failures"]
        out.update(reroute(state, error, now))
        out.update(_watch_crons(state, now))
        save(state)
        return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv not in ([], ["daily"], ["runway"]):
        hand.emit({"ok": False, "error": "usage: bin/health [daily|runway]"})
        return 2
    if argv == ["runway"]:
        # Runway as a sense (docs/roadmap.md, S2): teeny_hands/runway.py.
        from teeny_hands import runway
        try:
            out = runway.daily()
        except hand.Refused as refused:
            hand.emit({"ok": False, "refused": refused.reason, **refused.detail})
            return 2
        except Exception as error:  # noqa: BLE001 - one JSON line, whatever happened
            hand.emit({"ok": False, "error": scrub(f"{type(error).__name__}: {error}")})
            return 1
        hand.emit({"ok": True, **out})
        return 0
    try:
        out = daily() if argv == ["daily"] else check()
    except Exception as error:  # noqa: BLE001 - one JSON line, whatever happened
        hand.emit({"ok": False, "error": scrub(f"{type(error).__name__}: {error}")})
        return 1
    hand.emit(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
