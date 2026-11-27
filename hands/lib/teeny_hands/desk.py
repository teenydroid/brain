"""The desk: how a scheduled agent takes and hands back its work with
nothing but file tools.

A cron shell job (bin/tasks-desk) runs a hand's `desk`
verb outside any model turn. It first settles: reads the report the agent
left in state/workbench/desk/<name>-done.json and passes it to the hand's
own `done`, with every rule that applies there. Then it claims: the hand's
`next`, written to state/workbench/desk/<name>.json for the agent's next
run. The agent reads one file and writes one file, inside state/workbench/,
the only folder its file tools reach (docs/architecture.md, section 6a).

A report the hand refuses is kept beside it as <name>-done.rejected.json
with the reason, and the reason is put on the next offer, so the agent sees
why and the desk never loops on it.

A report left while the desk offered nothing (its job or task was null)
that names no job or task either only says there was nothing to do: it is
removed and noted in the desk's result as `discarded`, never refused, never
put on the next offer and never told to the mind.

Either way the outcome goes into Teeny's mind's inbox (teeny_hands.stimuli)
as an `operator` report: the mind asked for the work, so it
hears how it went. A settled report that already wrote its own stimulus
(`stimulus` in what `done` answered, as a finished news task does) is not
told twice.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from . import hand

#: The desk's name -> who reports, as the mind's inbox names them.
#: (The builder's `build` desk is retired, 2026-11-24.)
REPORTERS = {"task": "operator"}
#: The desk's name -> the key its offer puts the work under; the report
#: names it as `<key>_id`.
OFFERED = {"task": "task"}


def folder() -> Path:
    path = hand.state_dir() / "workbench" / "desk"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(hand.root().resolve()))


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def run(name: str, settle: Callable[[Any], Mapping[str, Any]], claim: Callable[[bool], Mapping[str, Any]],
        shape: str, present: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Settles `<name>-done.json` with `settle`, then writes `claim(turned_away)`
    to `<name>.json`, telling the agent where and how to report. `turned_away`
    is True when this pass refused the report the run left: that run did
    report, so its claim is still the agent's to resume. `present`, when
    given, rewrites the offer into what the agent is shown (paths relative
    to its own root, for an agent rooted below the checkout)."""
    desk = folder()
    offer_path, report_path = desk / f"{name}.json", desk / f"{name}-done.json"
    rejected_path = desk / f"{name}-done.rejected.json"
    reason_path = desk / f"{name}-done.rejected.reason.json"
    settled: dict[str, Any] | None = None
    rejected: str | None = None
    discarded: str | None = None
    if report_path.exists() and _nothing_to_report(name, offer_path, report_path):
        report_path.unlink()
        discarded = f"{report_path.name} named no {OFFERED[name]} and none was offered"
    elif report_path.exists():
        try:
            report = json.loads(report_path.read_text())
            settled = dict(settle(report))
            report_path.unlink()
            rejected_path.unlink(missing_ok=True)
            reason_path.unlink(missing_ok=True)
        except (ValueError, hand.Refused, hand.HandError) as exc:
            rejected = exc.reason if isinstance(exc, hand.Refused) else str(exc)
            os.replace(report_path, rejected_path)
            _write(reason_path, {"reason": rejected})
    if rejected is None and not rejected_path.exists():
        # A reason with no refused report beside it is about an earlier job.
        # The agent lists this folder, and on 2026-11-22 the (since retired) builder took a
        # reason eight days old as the verdict on the job in front of it.
        reason_path.unlink(missing_ok=True)
    _tell_the_mind(name, settled, rejected)
    offer = dict(claim(rejected is not None))
    offer["report_to"] = _relative(report_path)
    # The same file, absolute, for an agent rooted at the checkout. A
    # `present` that roots the agent elsewhere (as the retired builder was,
    # at state/workbench/) drops it, so the agent has one path and no second form.
    offer["report_to_path"] = str(report_path.resolve())
    offer["report_shape"] = shape
    if rejected:
        offer["last_report_rejected"] = rejected
    if present is not None:
        offer = present(offer)
    _write(offer_path, offer)
    return {"settled": settled, "rejected": rejected, "discarded": discarded, "offer": _relative(offer_path),
            "offered": offer.get("job", offer.get("task"))}


def _cron_store() -> Any:
    """bin/lib/cron_store.py: the one reader of the core's cron jobs and runs (the scheduler is
    OpenClaw's automations; bin/lib/cron_store.py says how they are read)."""
    import importlib.util
    import sys

    loaded = sys.modules.get("cron_store")
    if loaded is not None:
        return loaded
    path = Path(__file__).resolve().parents[3] / "bin" / "lib" / "cron_store.py"
    spec = importlib.util.spec_from_file_location("cron_store", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["cron_store"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def last_run(job: str, since: float) -> str | None:
    """What the newest ended run of the cron job JOB that started at or after
    SINCE said, whole: the agent's final message. None when there is none, or
    the core's cron history cannot be read. Read-only, as bin/crons reads it."""
    store = _cron_store()
    try:
        ids = {j["id"] for j in store.jobs() if j.get("name") == job}
        runs = store.runs()
    except store.Unavailable:
        return None
    ended = [r for r in runs if str(r.get("job_id")) in ids and isinstance(r.get("started_ms"), (int, float))
             and r["started_ms"] >= int(since * 1000) and r.get("finished_ms") is not None]
    if not ended:
        return None
    newest = max(ended, key=lambda r: r.get("seq") or 0)
    said = str(newest.get("output") or "").strip()
    return said or None


def _nothing_to_report(name: str, offer_path: Path, report_path: Path) -> bool:
    """True when the desk last offered no work and the report names none:
    the agent ran idle and wrote about it. Anything unreadable is not this
    case; it goes to the hand's `done` and is refused there as before."""
    key = OFFERED.get(name)
    if key is None:
        return False
    try:
        offer = json.loads(offer_path.read_text())
        report = json.loads(report_path.read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(offer, dict) or not isinstance(report, dict):
        return False
    return key in offer and offer[key] is None and report.get(f"{key}_id") is None


def _tell_the_mind(name: str, settled: Mapping[str, Any] | None, rejected: str | None) -> None:
    """A report that landed (or was turned away) becomes one stimulus. The
    inbox is a side channel: failing to write it never fails the desk."""
    if (settled is None and rejected is None) or (settled is not None and settled.get("stimulus")):
        return
    from . import stimuli

    who = REPORTERS.get(name, name)
    if who not in stimuli.SURFACES:
        return
    try:
        if settled is not None:
            work = settled.get("job_id", settled.get("task_id", "?"))
            status = settled.get("status", "done")
            detail = {k: v for k, v in settled.items() if k not in ("job_id", "task_id", "status")}
            text = f"the {who} reported on {name} {work}: {status}"
            if detail:
                text += "\n" + json.dumps(detail, ensure_ascii=False, sort_keys=True)[:1500]
            stimuli.add(who, "report", f"{name}:{work}:{status}", text, payload=dict(settled))
        else:
            stimuli.add(who, "report_rejected", f"{name}:{hand.now():.0f}",
                        f"the {who}'s report was turned away: {rejected}", payload={"reason": rejected})
    except (hand.HandError, OSError, ValueError) as exc:
        print(f"desk: could not tell the mind about the {name} report: {exc}", file=__import__("sys").stderr)
