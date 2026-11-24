"""The verbs of hands/build (docs/hands.md, "build"). The builder that
worked a queue here is retired (2026-11-24); these three stay.

  R  jobs [--status S] [--limit N]   the builder's jobs, newest first (history)
  R  frozen                   the frozen paths (evals/frozen.txt) and where the frozen things a
                              change to Teeny most often wants live, with what is Teeny's to change instead
  J  self-sync                fetch Teeny's own clone (state/workbench/brain) and fast-forward
                              it to origin/main when nothing unshipped is on it; recorded in
                              state/self/sync.json (bin/evolve runs the same at the start of a run)
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import ledger, selfclone


def _limit(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 500:
        raise argparse.ArgumentTypeError("--limit is 1..500")
    return number


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/build", description="The builder's history, and Teeny's own clone.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=False)
        return sub

    listing = verb("jobs", lambda a, _: ledger.listing(a.status, a.limit), "the builder's jobs, newest first")
    listing.add_argument("--status", choices=ledger.STATUSES, default=None)
    listing.add_argument("--limit", type=_limit, default=50)
    verb("frozen", lambda a, _: selfclone.frozen_listing(), "the frozen paths, and where the often-wanted ones live")
    verb("self-sync", lambda a, _: selfclone.sync(), "sync Teeny's own clone with origin/main")
    return root


def main(argv: list[str]) -> int:
    return hand.run("build", parser(), argv)
