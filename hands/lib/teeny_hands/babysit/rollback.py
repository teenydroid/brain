"""Noticing a deploy that left the box unhealthy, with BABYSIT_SELF_MERGE_ALL
(the operator's decision of 2026-11-22: every self-change merges itself on
green CI, and a bad deploy is caught after it). The sweep runs `check` first,
before it audits anything, so a judgement made now holds this run's merges.

  the deploy  the newest `deploy_ff` row (deploy.run writes it when the live
              checkout moves to a merge), watched for ROLLBACK_WINDOW_MINUTES
  bad         in code, no model: a doctor report (state/doctor.json) written
              in the window with a check whose status is `fail`, or bin/spine
              falling back to last-good (state/spine-fallback.json, which
              bin/lib/last_good.py writes) in the window. The marker matters
              on its own: the doctor reruns on the last-good boot and can
              leave a green report over the red one
  then        `deploy_bad` (the merged pull requests and the paths they
              changed), one revert issue on the repository, and, for
              ROLLBACK_BLOCK_HOURS, an operator finding (`recently_rolled_back`)
              on every self-merge candidate touching one of those paths
  ok          a deploy still healthy when its window closes is `deploy_ok`,
              once

Only the newest deploy is judged: an older one a newer deploy followed is
not. One found after its window has closed (the sweep runs every 20 minutes)
is still judged on the evidence inside it, once; past twice the window it is
history and left alone.

The revert is an issue, not a pull request: evolve-watch's revert
(bin/lib/evolve.py `revert`) builds its branch in Teeny's clone from an
evolve candidate and its config, nothing a hand can call. The issue says
what to revert and why; a person (or Teeny, on a teeny/ branch the audit
then holds for the operator) opens the revert. It goes through hands/gh's
`open_issue` (the guard, DRY_RUN, the gh ledger). One that does not open is
tried again next run; one the guard holds is not asked again.

It never restarts or rolls the box back: bin/spine already boots last-good
when the doctor fails, and deploy.py deploys nothing while that marker is
there. Under DRY_RUN it records what it would do (`rollback_dry_run`) and
nothing else: no judgement, no issue, no hold.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from typing import Any, Callable

from .. import hand
from ..gh import actions as gh_actions
from . import deploy, injection, ledger, policy
from .api import PullsApi

Json = dict[str, Any]

ROLLBACK_WINDOW_MINUTES = 60
ROLLBACK_BLOCK_HOURS = 24
DOCTOR = "doctor.json"
DOCTOR_FAILURES = "doctor-failures.jsonl"
#: The most paths an issue lists; a `deploy_bad` row keeps every one, for the hold.
LISTED_PATHS = 30
#: A doctor check's name as the issue may say it (they are bin/doctor's own
#: words; anything else is "a check").
_CHECK_NAME = re.compile(r"[A-Za-z0-9 ._/:()+-]{1,80}")


def check(api: PullsApi | None = None) -> Json | None:
    """The newest deploy judged as far as it can be now, and any revert issue
    still to open; None with the switch off or nothing to do."""
    if not policy.self_merge_all_on():
        return None
    made: list[PullsApi] = [api] if api is not None else []

    def gh() -> PullsApi:
        if not made:
            made.append(PullsApi())
        return made[0]

    out: Json = {}
    newest = ledger.actions_since("deploy_ff", hand.now() - 2 * ROLLBACK_WINDOW_MINUTES * 60)
    if newest and not _judged(newest[0]):
        out.update(_judge(newest[0], gh))
    if not hand.dry_run():
        reverts = _open_reverts(gh)
        if reverts:
            out["reverts"] = reverts
    return out or None


def blocked_places() -> set[str]:
    """The paths (as injection.place names them) of every bad deploy in the
    last ROLLBACK_BLOCK_HOURS."""
    if not policy.self_merge_all_on():
        return set()
    out: set[str] = set()
    for row in ledger.actions_since("deploy_bad", hand.now() - ROLLBACK_BLOCK_HOURS * 3600):
        out.update(str(p) for p in row["detail"].get("paths") or [])
    return out


def holds_everything() -> bool:
    """Whether every self-merge waits, whatever it touches: a deploy that looks
    bad has no recorded judgement yet (its files could not be read, so the paths
    to hold are unknown), or a judged one's changed-file listing was cut."""
    if not policy.self_merge_all_on() or hand.dry_run():
        return False
    for row in ledger.actions_since("deploy_bad", hand.now() - ROLLBACK_BLOCK_HOURS * 3600):
        if row["detail"].get("paths_cut"):
            return True
    # The look-back is the hold's own (ROLLBACK_BLOCK_HOURS), not `check`'s two
    # windows: a judgement that kept failing past those still holds, until the
    # hold would have lapsed anyway.
    newest = ledger.actions_since("deploy_ff", hand.now() - ROLLBACK_BLOCK_HOURS * 3600)
    if not newest or _judged(newest[0]):
        return False
    at = float(newest[0]["at"])
    start, end = math.floor(at), at + ROLLBACK_WINDOW_MINUTES * 60
    return bool(_doctor_failures(start, end)) or _fell_back(start, end)


# --- the judgement ----------------------------------------------------------------------------


def _judged(row: Json) -> bool:
    verbs = ("rollback_dry_run",) if hand.dry_run() else ("deploy_ok", "deploy_bad")
    return any(ledger.acted(verb, row["repo"], row["number"], row["head_sha"]) for verb in verbs)


def _judge(row: Json, gh: Callable[[], PullsApi]) -> Json:
    at, repo, number, sha = float(row["at"]), str(row["repo"]), int(row["number"]), row["head_sha"]
    start, end = math.floor(at), at + ROLLBACK_WINDOW_MINUTES * 60
    doctor = _doctor_failures(start, end)
    fell = _fell_back(start, end)
    bad = bool(doctor) or fell
    base: Json = {"number": number, "sha": sha}
    if not bad and hand.now() < end:
        return {**base, "watching": number}
    verdict = "deploy_bad" if bad else "deploy_ok"
    numbers = [int(n) for n in row["detail"].get("numbers") or [number]]
    detail: Json = {"deploy_id": row["id"], "numbers": numbers}
    if bad:
        # Raises when GitHub cannot be read: nothing is recorded, and
        # `holds_everything` keeps every self-merge waiting until a later run
        # completes this judgement.
        paths, cut = _paths(gh(), repo, numbers)
        detail.update(paths=paths, paths_cut=cut, doctor=doctor, fallback=fell)
    if hand.dry_run():
        ledger.record_action("rollback_dry_run", repo, number, sha, would=verdict, **detail)
        return {**base, "dry_run": True, "would": verdict}
    ledger.record_action(verdict, repo, number, sha, **detail)
    return {**base, "judged": verdict}


def _when(value: Any) -> float | None:
    """bin/doctor's and last_good.py's `at` (UTC, %Y-%m-%dT%H:%M:%SZ)."""
    try:
        return dt.datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        return None


def _read(name: str) -> Json | None:
    try:
        value = json.loads((hand.state_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _inside(value: Json | None, start: float, end: float) -> bool:
    when = _when(value.get("at")) if value else None
    return when is not None and start <= when <= end


def _doctor_failures(start: float, end: float) -> list[str]:
    """The failing checks of the doctor's reports written inside the window:
    the latest (state/doctor.json) and every red run bin/doctor kept since
    (state/doctor-failures.jsonl, which a later green run cannot overwrite).
    Names only (bin/doctor's), never their detail."""
    names: list[str] = []
    report = _read(DOCTOR)
    if _inside(report, start, end):
        names += [str(c.get("check")) for c in report.get("checks") or []
                  if isinstance(c, dict) and c.get("status") == "fail"]
        if not names and report.get("ok") is False:
            names = ["doctor"]
    try:
        lines = (hand.state_dir() / DOCTOR_FAILURES).read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            kept = json.loads(line)
        except ValueError:
            continue
        if isinstance(kept, dict) and _inside(kept, start, end):
            found = [str(c.get("check")) for c in kept.get("checks") or [] if isinstance(c, dict)]
            names += found or ["doctor"]
    names = list(dict.fromkeys(names))
    return [name if _CHECK_NAME.fullmatch(name) else "a check" for name in names]


def _fell_back(start: float, end: float) -> bool:
    return _inside(_read(deploy.FALLBACK), start, end)


def _paths(api: PullsApi, repo: str, numbers: list[int]) -> tuple[list[str], bool]:
    """Every path the merged pull requests changed, each as the audit names it,
    and whether any listing was cut (then the set is incomplete)."""
    owner, name, _ = policy.watched(repo)
    out: set[str] = set()
    cut = False
    for number in numbers:
        files, was_cut = api.files(owner, name, number)
        cut = cut or was_cut
        for f in files:
            for raw in (f.get("filename"), f.get("previous_filename")):
                if raw:
                    out.add(injection.place(str(raw)))
    return sorted(out), cut


# --- the revert issue -----------------------------------------------------------------------------


def _open_reverts(gh: Callable[[], PullsApi]) -> list[Json]:
    out = []
    for bad in reversed(ledger.actions_since("deploy_bad", hand.now() - ROLLBACK_BLOCK_HOURS * 3600)):
        repo, number, sha = str(bad["repo"]), int(bad["number"]), bad["head_sha"]
        if any(ledger.acted(verb, repo, number, sha) for verb in ("deploy_revert", "deploy_revert_held")):
            continue
        out.append(_open_revert(gh, bad))
    return out


def _open_revert(gh: Callable[[], PullsApi], bad: Json) -> Json:
    repo, number, sha = str(bad["repo"]), int(bad["number"]), bad["head_sha"]
    try:
        opened = gh_actions.open_issue({"repo": repo, "title": title(number), "body": body(bad)}, gh().gh,
                                       judge=False)
    except hand.Refused as refused:
        if "hold_id" in refused.detail and not refused.detail.get("judge_failed"):
            # The guard's verdict on these fixed words: asking again only adds a hold.
            ledger.record_action("deploy_revert_held", repo, number, sha, hold_id=refused.detail.get("hold_id"))
            return {"number": None, "held": refused.reason}
        return {"number": None, "refused": refused.reason}
    except hand.HandError as exc:
        return {"number": None, "error": str(exc)[:300]}
    ledger.record_action("deploy_revert", repo, number, sha, issue=opened.get("number"), url=opened.get("html_url"))
    return {"number": opened.get("number"), "html_url": opened.get("html_url")}


def title(number: int) -> str:
    return f"revert #{number}: the deploy left the box unhealthy"


def body(bad: Json) -> str:
    """The babysitter's own words, the doctor's check names and the paths as
    the audit names them; never a word of the pull request."""
    detail, sha = bad["detail"], str(bad["head_sha"] or "")
    numbers = ", ".join(f"#{n}" for n in detail.get("numbers") or [bad["number"]])
    lines = [f"The babysitter self-merged {numbers} and deployed it to the live checkout at `{sha[:12]}`; "
             f"within {ROLLBACK_WINDOW_MINUTES} minutes the box was unhealthy.", "", "## What it saw", ""]
    doctor = detail.get("doctor") or []
    if doctor:
        lines.append("- the doctor (state/doctor.json) failed after the deploy: " + ", ".join(f"`{c}`" for c in doctor))
    if detail.get("fallback"):
        lines.append("- bin/spine could not boot it and fell back to last-good (state/spine-fallback.json)")
    paths = detail.get("paths") or []
    if detail.get("paths_cut"):
        lines.append("- the list of changed files was cut short, so the hold is on every self-merge, not on paths")
    listed = ", ".join(f"`{p}`" for p in paths[:LISTED_PATHS]) or "none read"
    more = f" and {len(paths) - LISTED_PATHS} more" if len(paths) > LISTED_PATHS else ""
    lines += [f"- what the merge changed: {listed}{more}", "", "## What to do", "",
              f"- revert merge commit `{sha}` on main in a pull request, or fix it forward",
              f"- self-merge is held for {ROLLBACK_BLOCK_HOURS} hours on any pull request touching those paths "
              "(`recently_rolled_back`: it needs the operator's approval)",
              "- the babysitter restarts and rolls back nothing itself; while state/spine-fallback.json is there "
              "the box runs last-good and no deploy runs. Clear it once main is good again.",
              "", "_automated by hands/babysit (BABYSIT_SELF_MERGE_ALL)._"]
    return "\n".join(lines)
