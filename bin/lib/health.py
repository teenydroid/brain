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
   did not run), and `stale_crons`, `rearmed` and `cron_alarm`.

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

Json = dict[str, Any]

_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_HOST = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
_KEYISH = re.compile(r"\b(?:sk|pk|ghp|gho|github_pat|xox[a-z]?|AKIA)[-_A-Za-z0-9]{6,}|[A-Za-z0-9_-]{32,}")


# --- the probe and the signals --------------------------------------------------------


def scrub(error: str) -> str:
    """One line of an error, safe to keep and to publish: no URL, no
    address, nothing that looks like a key, and not too long."""
    line = " ".join(str(error).split())
    line = _URL.sub("[url]", line)
    line = _HOST.sub("[host]", line)
    line = _KEYISH.sub("[redacted]", line)
    return line if len(line) <= MAX_ERROR_CHARS else line[:MAX_ERROR_CHARS].rstrip() + "..."


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


def probe() -> str | None:
    """None when the model answered; what went wrong when it did not."""
    try:
        reply = hand.core("inference_agent_chat_simple", {"message": PROBE}, timeout=PROBE_TIMEOUT)
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


def cron_errors(since: float) -> dict[str, int]:
    """Failed cron runs since `since`, by job name. Read-only; bin/crons'
    query, narrowed to valid JSON in the cron collections first."""
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
                group by name order by name
                """,
                (int(since * 1000),),
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


# --- the check ------------------------------------------------------------------------------------


def _rescreen() -> Json:
    """The re-screen's counts; a re-screen that broke is reported, and the
    health check still answers."""
    try:
        return {"rescreen": rescreen.run()}
    except Exception as error:  # noqa: BLE001 - the watcher must not fall over its own errand
        return {"rescreen": None, "rescreen_error": scrub(f"{type(error).__name__}: {error}")}


def check() -> Json:
    with hand.lock("health"):
        now = hand.now()
        state = load()
        error = probe()
        holds = judge_holds(now - WINDOW_SECS)
        errors = cron_errors(now - WINDOW_SECS)
        out: Json = {"ok": True, "healthy": error is None, "judge_holds": holds, "cron_errors": errors,
                     "alarm": None, "issue": None, "rescreen": None}
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
        out.update(_watch_crons(state, now))
        save(state)
        return out


def main() -> int:
    try:
        out = check()
    except Exception as error:  # noqa: BLE001 - one JSON line, whatever happened
        hand.emit({"ok": False, "error": scrub(f"{type(error).__name__}: {error}")})
        return 1
    hand.emit(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
