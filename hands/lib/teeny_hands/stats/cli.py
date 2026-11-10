"""The verbs of hands/stats (docs/hands.md, "stats").

  J  snapshot              today's numbers from X, Moltbook and GitHub into `stats`
  R  report [--days N]     every metric's latest value and its change over N days (7)
  R  get METRIC            one metric's latest value: source.metric (x.followers) or a
                           metric only one source keeps (karma)

A post may only quote a number that came from here.
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
    root = argparse.ArgumentParser(prog="hands/stats", description="Teeny's numbers, as the services report them.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=False)
        return sub

    verb("snapshot", lambda a, _: collect.snapshot(), "today's numbers into the stats table")
    verb("report", lambda a, _: collect.report(a.days), "latest values and their change").add_argument("--days", type=_days, default=7)
    verb("get", lambda a, _: collect.get(a.metric), "one metric's latest value").add_argument("metric", metavar="METRIC")
    return root


def main(argv: list[str]) -> int:
    return hand.run("stats", parser(), argv)
