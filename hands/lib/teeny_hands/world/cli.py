"""The verbs of hands/world (docs/hands.md, "world").

  J  snapshot              today's W, one row per component, into `world_scores`
  R  report [--days N]     the latest W, each component's growth over N days (7),
                           its weight and cap, the penalty, and J
  R  history [--days N]    the rows of the last N days (7), one entry per day

The score is computed here, in frozen code, never reported by a model.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import collect


def _days(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 365:
        raise argparse.ArgumentTypeError("--days is 1..365")
    return number


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/world", description="Teeny's world score W and growth score J.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=False)
        return sub

    verb("snapshot", lambda a, _: collect.snapshot(), "today's W into the world_scores table")
    verb("report", lambda a, _: collect.report(a.days), "latest W, its growth, and J").add_argument("--days", type=_days, default=7)
    verb("history", lambda a, _: collect.history(a.days), "the rows of the last N days").add_argument("--days", type=_days, default=7)
    return root


def main(argv: list[str]) -> int:
    return hand.run("world", parser(), argv)
