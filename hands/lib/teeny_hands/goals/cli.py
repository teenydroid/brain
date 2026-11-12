"""The verbs of hands/goals (docs/hands.md, "goals").

  A  propose                 stdin {instruction, component, channel, family, deadline_days,
                             check: {kind, params}, step?, first?, why?}; refused for an
                             unknown check kind, a near-duplicate, or too many open
  J  check                   every open goal against its check: solved, failed or open
  R  list [--status S] [--limit N]   the goals, newest first
  R  rank                    open goals by LP * band * (1 - p_hat) * interest * E[dJ] * first,
                             and every family's numbers
  R  annecs                  ANNECS and the (component x channel) archive

Everything here is local state (state/hands.db) and reads; nothing is said
anywhere, so DRY_RUN does not apply.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import ledger


def _limit(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 500:
        raise argparse.ArgumentTypeError("--limit is 1..500")
    return number


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/goals", description="The goal ledger.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    proposing = verb("propose", lambda a, body: ledger.propose(body, source=a.source), "propose one goal (JSON on stdin)",
                     body=True)
    proposing.add_argument("--source", default=None, help="who proposed it: a playbook name, 'operator', ...")
    verb("check", lambda a, _: ledger.check(), "evaluate every open goal against its check")
    listing = verb("list", lambda a, _: ledger.listing(a.status, a.limit), "the goals, newest first")
    listing.add_argument("--status", choices=ledger.STATUSES, default=None)
    listing.add_argument("--limit", type=_limit, default=50)
    verb("rank", lambda a, _: ledger.ranking(), "open goals ranked, and the families")
    verb("annecs", lambda a, _: ledger.annecs(), "ANNECS and the niche archive")
    return root


def main(argv: list[str]) -> int:
    return hand.run("goals", parser(), argv)
