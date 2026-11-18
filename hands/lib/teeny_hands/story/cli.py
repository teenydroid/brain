"""The verbs of hands/story (docs/hands.md, "story").

  R  state                 the active storylines, each with its last 3 beats
  R  due [--days N]        storylines whose open question has waited N days (default 3)
  R  desk [--hours N]      what happened in the last N hours (1..168, default 24), with stakes
  R  audit [--storyline K] every beat's refs resolve to a real row or file
  A  beat                  record a posted beat (JSON on stdin); DRY_RUN writes nothing
  O  seed                  insert the seed storylines the ledger lacks
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import audit, desk, store


def _bounded(low: int, high: int, name: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{name} is {low}..{high}")
        return number
    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/story", description="The story so far, and the studio's desk.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, takes_body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=takes_body)
        return sub

    verb("state", lambda a, _: store.state(), "the active storylines with their last beats")
    verb("due", lambda a, _: store.due(a.days), "storylines waiting too long for a beat").add_argument(
        "--days", type=_bounded(1, 30, "--days"), default=store.DUE_DAYS)
    verb("desk", lambda a, _: desk.desk(a.hours), "what happened lately, each with its stake").add_argument(
        "--hours", type=_bounded(1, 168, "--hours"), default=24)
    verb("audit", lambda a, _: audit.audit(a.storyline), "check every beat's refs resolve").add_argument(
        "--storyline", default=None)
    verb("beat", lambda a, body: store.beat(body), "record a posted beat (JSON on stdin)", takes_body=True)
    verb("seed", lambda a, _: store.seed(), "insert the seed storylines the ledger lacks")
    return root


def main(argv: list[str]) -> int:
    return hand.run("story", parser(), argv)
