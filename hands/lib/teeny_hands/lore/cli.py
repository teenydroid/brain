"""The verbs of hands/lore (docs/hands.md, "lore").

  R  events [--days N]    the proof events of the last N days (1..14, default 1)
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import events


def _days(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 14:
        raise argparse.ArgumentTypeError("--days is 1..14")
    return number


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/lore", description="Proof events for lore episodes.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=False)
        return sub

    verb("events", lambda a, _: events.recent(a.days), "the proof events of the last days").add_argument(
        "--days", type=_days, default=1)
    return root


def main(argv: list[str]) -> int:
    return hand.run("lore", parser(), argv)
