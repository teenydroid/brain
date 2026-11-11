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
5. Prints one JSON line: `ok` (the check ran), `healthy` (the model
   answered), what it did, and `rescreen`, the re-screen's counts (null
   when it did not run).

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


# --- state ------------------------------------------------------------------------------------

FRESH: Json = {"failures": 0, "first_failed_at": None, "last_failed_at": None, "last_ok_at": None,
               "last_error": None, "alarm": None}


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


def _open_issue(alarm: Json, state: Mapping[str, Any], holds: int, errors: Mapping[str, int]) -> None:
    """Opens the alarm's issue, once; what happened goes on the alarm."""
    alarm["issue_error"] = None
    alarm["issue_dry_run"] = False
    try:
        got = gh.open_issue(_issue(state, holds, errors), judge=False)
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
    _open_issue(alarm, state, holds, errors)
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


def _clear(state: Json, alarm: Json, now: float, holds: int, errors: Mapping[str, int]) -> Json:
    first = state["first_failed_at"] or alarm["opened_at"]
    down = f"about {_span(now - first)}, from {_when(first)} to {_when(now)}"
    closed: Json = {}
    if alarm.get("issue"):
        try:
            gh.close_issue({"repo": _repo(), "number": alarm["issue"],
                            "comment": f"The model answers again. It was not answering for {down}. Closing."},
                           judge=False)
        except (hand.Refused, hand.HandError) as error:
            closed["issue_error"] = scrub(getattr(error, "reason", None) or str(error))
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
                        _open_issue(alarm, state, holds, errors)
                    out["alarm"] = "open"
                else:
                    alarm = _raise(state, now, holds, errors)
                    out["alarm"] = "raised"
                state["alarm"] = alarm
                out.update(issue=alarm.get("issue"), issue_error=alarm.get("issue_error"),
                           issue_dry_run=alarm.get("issue_dry_run"))
        out["failures"] = state["failures"]
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
