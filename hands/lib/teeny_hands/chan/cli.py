"""The verbs of hands/chan (docs/hands.md, "chan"). Every verb is a read (R)
through the read cache; nothing here writes to 4chan, whose API is read
only. Only the boards in `client.SAFE_BOARDS` are ever read.

  R  boards                                    the boards Teeny may read, and the default ones
  R  catalog BOARD [--max N]                   a board's liveliest threads, each as a premise
  R  thread BOARD NO [--max N]                 a thread's first post and first replies
  R  sparks [--boards a,b] [--max N] [--seed S]
                                               N random premises across the boards, for the
                                               backroom and the studio's wildcard
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import reads


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/chan", description="4chan, read only, for absurd premises.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=False)
        return sub

    verb("boards", lambda a, _: reads.boards(), "the boards Teeny may read")
    listing = verb("catalog", lambda a, _: reads.catalog(a.board, a.max), "a board's liveliest threads")
    listing.add_argument("board", metavar="BOARD")
    listing.add_argument("--max", type=_bounded(1, 50, "--max"), default=15)
    one = verb("thread", lambda a, _: reads.thread(a.board, a.no, a.max), "one thread")
    one.add_argument("board", metavar="BOARD")
    one.add_argument("no", metavar="NO")
    one.add_argument("--max", type=_bounded(1, 100, "--max"), default=20)
    drawn = verb("sparks", lambda a, _: reads.sparks(a.boards, a.max, a.seed), "random premises across the boards")
    drawn.add_argument("--boards", default=None, help="comma-separated; default CHAN_BOARDS")
    drawn.add_argument("--max", type=_bounded(1, 20, "--max"), default=3)
    drawn.add_argument("--seed", default=None)
    return root


def main(argv: list[str]) -> int:
    return hand.run("chan", parser(), argv)
