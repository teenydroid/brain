"""The desk: how a scheduled agent takes and hands back its work with
nothing but file tools.

A cron shell job (bin/build-ship, bin/tasks-desk) runs a hand's `desk`
verb outside any model turn. It first settles: reads the report the agent
left in state/workbench/desk/<name>-done.json and passes it to the hand's
own `done`, with every rule that applies there. Then it claims: the hand's
`next`, written to state/workbench/desk/<name>.json for the agent's next
run. The agent reads one file and writes one file, inside state/workbench/,
the only folder its file tools reach (docs/architecture.md, section 6a).

A report the hand refuses is kept beside it as <name>-done.rejected.json
with the reason, and the reason is put on the next offer, so the agent sees
why and the desk never loops on it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from . import hand


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


def run(name: str, settle: Callable[[Any], Mapping[str, Any]], claim: Callable[[], Mapping[str, Any]],
        shape: str) -> dict[str, Any]:
    """Settles `<name>-done.json` with `settle`, then writes `claim()` to
    `<name>.json`, telling the agent where and how to report."""
    desk = folder()
    offer_path, report_path = desk / f"{name}.json", desk / f"{name}-done.json"
    rejected_path = desk / f"{name}-done.rejected.json"
    settled: dict[str, Any] | None = None
    rejected: str | None = None
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text())
            settled = dict(settle(report))
            report_path.unlink()
            rejected_path.unlink(missing_ok=True)
        except (ValueError, hand.Refused, hand.HandError) as exc:
            rejected = exc.reason if isinstance(exc, hand.Refused) else str(exc)
            os.replace(report_path, rejected_path)
            _write(desk / f"{name}-done.rejected.reason.json", {"reason": rejected})
    offer = dict(claim())
    offer["report_to"] = _relative(report_path)
    offer["report_shape"] = shape
    if rejected:
        offer["last_report_rejected"] = rejected
    _write(offer_path, offer)
    return {"settled": settled, "rejected": rejected, "offer": _relative(offer_path),
            "offered": offer.get("job", offer.get("task"))}
