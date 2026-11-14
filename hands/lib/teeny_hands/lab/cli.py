"""The verbs of hands/lab (docs/hands.md, "lab").

  A  propose [--source S]    stdin {surface, hypothesis, format, metric?, posts,
                             deadline_days | deadline}; at most 5 open, one per
                             surface and format
  J  score [--id N]          the open experiments (or one) against the surface's
                             average for the same period, recorded
  A  conclude                stdin {id, verdict: works|fails|unclear, lesson}; works
                             or fails only once the planned posts are measured
  R  list [--status S] [--limit N]   the experiments, newest first (open by default)
  R  lessons [--max N]       the concluded lessons, newest first (10, at most 20)

Local state only (state/hands.db); nothing is said anywhere, so DRY_RUN does
not apply.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import ledger


def _bounded(low: int, high: int, name: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{name} is {low}..{high}")
        return number

    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/lab", description="The lab ledger.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    proposing = verb("propose", lambda a, body: ledger.propose(body, source=a.source), "propose one experiment", body=True)
    proposing.add_argument("--source", default=None, help="who proposed it: dream, mind, a playbook, operator")
    verb("score", lambda a, _: ledger.score(a.id), "score the open experiments").add_argument(
        "--id", type=_bounded(1, 10**9, "--id"), default=None)
    verb("conclude", lambda a, body: ledger.conclude(body), "conclude one experiment", body=True)
    listing = verb("list", lambda a, _: ledger.listing(None if a.status == "all" else a.status, a.limit),
                   "the experiments, newest first")
    listing.add_argument("--status", choices=(*ledger.STATUSES, "all"), default="open")
    listing.add_argument("--limit", type=_bounded(1, 100, "--limit"), default=20)
    verb("lessons", lambda a, _: ledger.lessons(a.max), "what the lab has learned").add_argument(
        "--max", type=_bounded(1, ledger.MAX_LESSONS, "--max"), default=10)
    return root


def main(argv: list[str]) -> int:
    return hand.run("lab", parser(), argv)
