"""Wants: what Teeny said on its memory page it will build, drawn in code,
filed at most once a day for its builder, and scored after it merges
(docs/evolve.md, "1c. Wants").

The memory page holds items like "the first thing i build myself is a $TINY
chart renderer ... not built yet". Evolve is the wrong place for them: it
makes small edits to prompts and playbooks, and it settled such an item as
`already` on the strength of the page itself. A want is a feature, so it goes
to the builder (`hands/build self`), through the guard, as a fixed brief with
the item quoted. Nothing here asks a model.

  draw     the wants of brain/MEMORY.md and where each stands (read-only)
  file     file the top open want, at most one per call
  watch    score each merged feature, seven days after it merged: did a
           scheduled playbook run its hand (evolve_outcomes, `hand_used`)
  run      watch, then file: the pinned job (evals/cron.pinned.toml)
  status   the wants, their standing and the last run

What is a want: an item of "what i am in the middle of" or "promises and open
threads" that says its author will build a thing (wants_rules.is_build_intent,
shared with bin/evolve, which no longer picks them). Only that page is read:
the other places a want could come from (failed builds, frozen files, a
stranger's request, browser notes) are not things a new hand fixes, or are
strangers' words (docs/evolve.md says why). The want's text reaches the brief
only through evolve_findings.scrub: no link, email, handle or id.

Where a want stands comes from the ledgers, never from the page:

  open       nothing filed for this text, or a build that failed with tries left
  in-flight  its request is waiting, its build is running, or its pull request
             is open. A pull request GitHub cannot be asked about is in flight
  merged     its pull request merged. Held until the text materially changes
  declined   its pull request closed unmerged. Held the same way
  gave-up    WANTS_MAX_TRIES failed builds, or one that failed on a frozen
             path (the change cannot be shipped). Held the same way

State lives in state/wants/ (filed.jsonl, outcomes.jsonl, last.json), all
append-only but the last. Settings are in secrets/evolve.env, next to
evolve's: WANTS (0..1, off by default; the process environment can only set
it to 0), WANTS_MAX_TRIES (1..5, 2), WANTS_USED_DAYS (1..30, 7) and
WANTS_MIN_USES (1..100, 4). A value that does not parse or is out of range
refuses the run.

Frozen with bin/: a candidate cannot change what a want is, how it is filed or
how a feature is scored. Standard library only, plus the evolve modules.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import evolve  # noqa: E402  (puts hands/lib on the path)
import evolve_findings  # noqa: E402
import evolve_outcomes as eo  # noqa: E402
import wants_rules  # noqa: E402
from teeny_hands import hand, stimuli  # noqa: E402

Json = dict[str, Any]

DAY = 86400.0
#: The longest a want's text may be in a brief (scrub cuts it there).
MAX_TEXT = 600
#: The most of a brief the build desk keeps (build/jobs.py cuts the rest).
BRIEF_CHARS = 2000
#: A request the builder refuses to run twice at once: not a failure.
ONE_AT_A_TIME = "one_at_a_time"
#: A build that failed here can never ship, so it is not tried again.
FROZEN = re.compile(r"frozen path|evals/frozen\.txt|is frozen", re.I)
#: A pull request of Teeny's own, as `build_jobs.pr_url` has it.
PR_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
#: A hand's name, as the builder's rubric records it.
HAND_NAME = re.compile(r"[a-z][a-z0-9_-]{0,39}")
#: A want's id, as the build hand takes it (`^w:[a-z0-9:_-]{1,60}$`).
WANT_ID = re.compile(r"[^a-z0-9:_-]")

#: name -> (default, low, high)
SETTINGS = {
    "WANTS": (0, 0, 1),
    "WANTS_MAX_TRIES": (2, 1, 5),
    "WANTS_USED_DAYS": (7, 1, 30),
    "WANTS_MIN_USES": (4, 1, 100),
}

BRIEF = """want: {id}

Build it: a new hand of your own that does what you said you would build, in your own words from your memory page ({subject}):

> {text}

This is a feature, not an edit. In one change: `hands/<name>` (its entry point, running `teeny_hands.<name>.cli.main` through `teeny_hands.hand.run`), `hands/lib/teeny_hands/<name>/`, `hands/tests/test_<name>.py` (standard library only, no network), its own section in `docs/hands.md` (a ``## `<name>` `` heading), and a playbook in `brain/playbooks/`, scheduled in `brain/cron.toml`, whose input or output calls it. Read-only public data only: no wallet, no key, no trade, no account. Choose `<name>` yourself. When you report ok the desk checks every part, and in the week after it merges the code counts how often a scheduled playbook ran it."""


class WantsError(Exception):
    pass


# --- settings ---------------------------------------------------------------------------


@dataclass
class Settings:
    wants: int
    max_tries: int
    used_days: int
    min_uses: int


def load_settings() -> Settings:
    """secrets/evolve.env over the defaults. The process environment can only
    switch the lane off (WANTS=0). A bad value refuses the run."""
    raw = evolve._read_env(evolve.secrets_dir() / "evolve.env")
    values: dict[str, int] = {}
    for name, (default, low, high) in SETTINGS.items():
        text = raw.get(name) or str(default)
        try:
            value = int(text)
        except ValueError:
            raise WantsError(f"secrets/evolve.env: {name}={text!r} is not an int") from None
        if not low <= value <= high:
            raise WantsError(f"secrets/evolve.env: {name}={text} is outside {low}..{high}")
        values[name] = value
    if os.environ.get("WANTS", "").strip() == "0":
        values["WANTS"] = 0
    return Settings(values["WANTS"], values["WANTS_MAX_TRIES"], values["WANTS_USED_DAYS"], values["WANTS_MIN_USES"])


# --- the hands ------------------------------------------------------------------------------


def run_hand(name: str, args: list[str], body: Json) -> tuple[int, Json]:
    """One call to a hand: its exit code and its last line of JSON. The only
    place this file reaches a hand. DRY_RUN stays on (evolve.hand_env): `build
    self` writes only hands.db, and everything downstream keeps its own."""
    try:
        done = subprocess.run([str(evolve.hands_dir() / name), *args], input=json.dumps(body) if body else "",
                              capture_output=True, text=True, env=evolve.hand_env(), timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, {"ok": False, "error": f"hands/{name} could not be run: {type(exc).__name__}"}
    lines = done.stdout.strip().splitlines()
    try:
        out = json.loads(lines[-1]) if lines else {}
    except ValueError:
        out = {"raw": done.stdout[-300:]}
    return done.returncode, out if isinstance(out, dict) else {"raw": out}


# --- the wants ------------------------------------------------------------------------------


@dataclass
class Want:
    id: str
    kind: str
    subject: str
    section: str
    text: str
    hash: str
    order: int


def read_wants() -> list[Want]:
    """The build-intent items of the live memory page, in page order."""
    try:
        page = (evolve.brain_dir() / "MEMORY.md").read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[Want] = []
    for item in evolve.page_learnings(page):
        if item.section not in wants_rules.WANT_SECTIONS or not wants_rules.is_build_intent(item.text):
            continue
        text = evolve_findings.scrub(item.text, MAX_TEXT)
        out.append(Want(id=("w:" + WANT_ID.sub("-", item.id.lower()))[:62], kind="memory", subject=item.id,
                        section=item.section, text=text, hash=evolve.learning_hash(text), order=len(out)))
    return out


def brief(want: Want) -> str:
    """What the builder is asked to build: fixed words, with the want's
    scrubbed text quoted."""
    return BRIEF.format(id=want.id, subject=want.subject, text=want.text)[:BRIEF_CHARS]


# --- state -------------------------------------------------------------------------------------


def wants_dir() -> Path:
    return evolve.state_dir() / "wants"


def _read_jsonl(path: Path) -> list[Json]:
    out: list[Json] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    out.append(value)
    except FileNotFoundError:
        pass
    return out


def _append(name: str, record: Json) -> None:
    wants_dir().mkdir(parents=True, exist_ok=True)
    with open(wants_dir() / name, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def read_filed() -> list[Json]:
    return _read_jsonl(wants_dir() / "filed.jsonl")


def _open_db() -> eo._Db:
    return eo._Db(evolve.state_dir() / "hands.db")


# --- where a want stands ----------------------------------------------------------------------


@dataclass
class Standing:
    state: str  # open, in-flight, merged, declined, gave-up
    tries: int = 0
    by: str = ""


def _build_state(db: eo._Db, request_id: Any) -> Json:
    """Where one filed request is in the builder's ledgers: `in-flight`,
    `shipped` (with its job's pr_url and rubric), `failed` or `frozen`. What
    cannot be read or placed is in flight: a want is never reopened on a
    guess."""
    unknown: Json = {"state": "in-flight", "by": "the build desk could not be read"}
    if not db.ok or not isinstance(request_id, int):
        return unknown
    request = db.rows("SELECT status FROM build_requests WHERE id = ?", (request_id,))
    if not request:
        return unknown
    status = request[0][0]
    rubric = "rubric" in db.columns("build_jobs")
    jobs = db.rows(f"SELECT id, status, pr_url, summary, ship_error, {'rubric' if rubric else 'NULL'} "
                   "FROM build_jobs WHERE request_id = ?", (request_id,))
    if jobs:
        job_id, job, pr_url, summary, ship_error, rubric_json = jobs[0]
        found = {"job_id": job_id, "pr_url": pr_url, "rubric": rubric_json}
        if job in ("claimed", "ok"):
            return {**found, "state": "in-flight", "by": f"job {job_id} is {job}"}
        if job == "shipped":
            if pr_url:
                return {**found, "state": "shipped", "by": f"job {job_id} shipped"}
            return {**found, "state": "in-flight", "by": f"job {job_id} shipped without a pull request"}
        if job in ("failed", "gave_up"):
            if FROZEN.search(f"{ship_error or ''} {summary or ''}"):
                return {**found, "state": "frozen", "by": f"job {job_id} needs a frozen path"}
            return {**found, "state": "failed", "by": f"job {job_id} {job}"}
        return {**found, "state": "in-flight", "by": f"job {job_id} is {job}"}
    if status in ("pending", "picked"):
        return {"state": "in-flight", "by": f"request {request_id} is {status}"}
    if status in ("failed", "skipped"):
        return {"state": "failed", "by": f"request {request_id} was {status}"}
    return unknown


def _epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        when = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.timestamp()


def read_pr(url: Any) -> Json:
    """The pull request's fate through `hands/gh pr` (a cached read):
    {"state": "merged", "merged_at": epoch | None}, "declined" or "in-flight".
    GitHub not answering, or answering out of shape, is in flight."""
    match = PR_URL.fullmatch(str(url or ""))
    if not match:
        return {"state": "in-flight"}
    code, got = run_hand("gh", ["pr", match.group(1), match.group(2)], {})
    if code != 0 or got.get("ok") is not True:
        return {"state": "in-flight"}
    if got.get("merged_at") or got.get("merged") is True:
        return {"state": "merged", "merged_at": _epoch(got.get("merged_at"))}
    if got.get("state") == "closed":
        return {"state": "declined"}
    return {"state": "in-flight"}


def want_standing(want: Want, filed: list[Json], db: eo._Db, max_tries: int = 2) -> Standing:
    """WANT's standing from what was filed for it (`filed`, oldest first) and
    the ledgers. A filing for text that has since materially changed is no
    longer this want's."""
    mine = [f for f in filed if f.get("want") == want.id
            and not evolve.materially_differs(str(f.get("text") or ""), want.text)]
    if not mine:
        return Standing("open")
    states = [_build_state(db, f.get("request_id")) for f in mine]
    tries = sum(1 for s in states if s["state"] in ("failed", "frozen"))
    last = states[-1]
    if last["state"] == "frozen":
        return Standing("gave-up", tries, last["by"])
    if last["state"] == "failed":
        return Standing("gave-up" if tries >= max_tries else "open", tries, last["by"])
    if last["state"] == "shipped":
        pr = read_pr(last["pr_url"])
        return Standing({"merged": "merged", "declined": "declined"}.get(pr["state"], "in-flight"), tries,
                        f"pull request {pr['state']}")
    return Standing("in-flight", tries, last["by"])


def standings(cfg: Settings, db: eo._Db) -> list[tuple[Want, Standing]]:
    filed = read_filed()
    return [(want, want_standing(want, filed, db, cfg.max_tries)) for want in read_wants()]


# --- verbs -------------------------------------------------------------------------------------


def draw(cfg: Settings) -> Json:
    db = _open_db()
    try:
        rows = standings(cfg, db)
    finally:
        db.close()
    return {"ok": True, "wants": [{**asdict(want), "standing": asdict(standing)} for want, standing in rows]}


def _held(verdict: Json) -> str:
    return evolve_findings.scrub(verdict.get("refused") or verdict.get("error") or "it did not pass", 200)


def file_one(cfg: Settings) -> Json:
    """File the top open want as a `self` build request: at most one."""
    if not cfg.wants:
        return {"ok": True, "skipped": "WANTS=0"}
    db = _open_db()
    try:
        rows = standings(cfg, db)
    finally:
        db.close()
    openings = sorted(((standing.tries, want.order, want) for want, standing in rows if standing.state == "open"),
                      key=lambda row: row[:2])
    if not openings:
        return {"ok": True, "skipped": "no open want"}
    want = openings[0][2]
    code, verdict = run_hand("guard", ["check"], {"text": brief(want), "surface": "github"})
    if code != 0 or not isinstance(verdict.get("text"), str):
        return {"ok": True, "held": _held(verdict)}
    code, made = run_hand("build", ["self"], {"text": verdict["text"], "want": want.id})
    if code == 2 and made.get("rule") == ONE_AT_A_TIME:
        return {"ok": True, "waiting": evolve_findings.scrub(made.get("refused") or "one change at a time", 200)}
    if code != 0 or not isinstance(made.get("id"), int):
        return {"ok": False, "error": "hands/build self: " + evolve_findings.scrub(
            made.get("error") or made.get("refused") or "it answered without a request id", 200)}
    _append("filed.jsonl", {"want": want.id, "hash": want.hash, "text": want.text, "at": evolve.now(),
                            "request_id": made["id"]})
    return {"ok": True, "filed": want.id, "request_id": made["id"]}


def _hands_of(rubric: Any) -> list[str]:
    """The names of the hands the builder's rubric says the feature added."""
    try:
        listed = json.loads(rubric)["hands"]
        names = [h["name"] for h in listed]
    except (TypeError, ValueError, KeyError):
        return []
    return [n for n in names if isinstance(n, str) and HAND_NAME.fullmatch(n)]


def _outcome_text(record: Json, min_uses: int) -> str:
    names = ", ".join(record["hands"]) or "its hand"
    if record["verdict"] == "no-data":
        return f"The feature built from your want {record['want']} ({names}) could not be scored: {record['why']}."
    used = "it is used" if record["verdict"] == "used" else "it is not used"
    return (f"The feature built from your want {record['want']} ({names}) merged {record['days']} days ago. "
            f"Scheduled playbooks ran it {record['uses']} times since (it takes {min_uses} to count): {used}.")


def watch(cfg: Settings) -> Json:
    """Score each merged feature once its week is over."""
    if not cfg.wants:
        return {"ok": True, "skipped": "WANTS=0"}
    now = evolve.now()
    scored = {(o.get("want"), o.get("request_id")) for o in _read_jsonl(wants_dir() / "outcomes.jsonl")}
    todo = [f for f in read_filed() if (f.get("want"), f.get("request_id")) not in scored]
    out: list[Json] = []
    src = None
    db = _open_db()
    try:
        for filing in todo:
            state = _build_state(db, filing.get("request_id"))
            if state["state"] != "shipped":
                continue
            pr = read_pr(state["pr_url"])
            if pr["state"] != "merged" or pr["merged_at"] is None:
                continue
            until = pr["merged_at"] + cfg.used_days * DAY
            if now < until:
                continue
            record: Json = {"want": filing["want"], "request_id": filing["request_id"], "job_id": state["job_id"],
                            "pr_url": state["pr_url"], "merged_at": evolve.iso(pr["merged_at"]),
                            "hands": _hands_of(state["rubric"]), "days": cfg.used_days, "uses": None,
                            "verdict": "no-data", "at": now}
            if not record["hands"]:
                record["why"] = "no rubric on the job"
            else:
                src = src or evolve.outcome_sources(evolve.load_config())
                counts = [eo.count({"kind": "hand_used", "hand": name, "direction": "rise"}, src, pr["merged_at"],
                                   until) for name in record["hands"]]
                if None in counts:
                    if now < until + eo.GRACE_DAYS * DAY:
                        continue  # not readable yet: try again tomorrow
                    record["why"] = "the count could not be read"
                else:
                    record["uses"] = sum(counts)
                    record["verdict"] = "used" if record["uses"] >= cfg.min_uses else "unused"
            stimuli.add("self", "outcome", f"want:{record['want']}:{record['request_id']}",
                        _outcome_text(record, cfg.min_uses),
                        payload={k: record[k] for k in ("want", "hands", "uses", "verdict")}, at=now)
            _append("outcomes.jsonl", record)
            out.append(record)
    finally:
        db.close()
    return {"ok": True, "scored": out}


def run(cfg: Settings) -> Json:
    """The pinned job: score, then file. One failing does not stop the other,
    but the run is not ok."""
    got: Json = {"at": evolve.now()}
    for name, step in (("watch", watch), ("file", file_one)):
        try:
            got[name] = step(cfg)
        except (WantsError, evolve.EvolveError, OSError, hand.HandError) as exc:
            got[name] = {"ok": False, "error": evolve_findings.scrub(f"{type(exc).__name__}: {exc}", 200)}
    got["ok"] = all(got[name].get("ok") for name in ("watch", "file"))
    wants_dir().mkdir(parents=True, exist_ok=True)
    (wants_dir() / "last.json").write_text(json.dumps(got, sort_keys=True) + "\n", encoding="utf-8")
    return got


def status(cfg: Settings) -> Json:
    try:
        last = json.loads((wants_dir() / "last.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = None
    return {**draw(cfg), "enabled": bool(cfg.wants), "last": last}


VERBS = {"draw": draw, "file": file_one, "watch": watch, "run": run, "status": status}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in VERBS:
        print(json.dumps({"ok": False, "error": f"usage: bin/wants {'|'.join(VERBS)}"}))
        return 1
    try:
        got = VERBS[argv[0]](load_settings())
    except (WantsError, evolve.EvolveError) as exc:
        got = {"ok": False, "error": str(exc)}
    print(json.dumps(got, sort_keys=True))
    return 0 if got.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
