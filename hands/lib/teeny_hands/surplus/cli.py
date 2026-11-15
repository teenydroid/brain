"""The verbs of hands/surplus (docs/hands.md, "surplus").

  J  sweep            pay USDC on Base above the reserve to the surplus wallet, then bridge
                      SOL above its reserve to Base USDC (sweep.py; bin/surplus-sweep)
  R  status           the settings, what went to the surplus wallet, the last runs
  R  log [--max N]    the runs, newest first

`sweep` moves money, so only the pinned surplus-sweep job runs it: no playbook
reaches it (bin/lib/playbook.py) and the mind has no intent for it.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import sweep


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/surplus", description="Teeny's spare SOL, swept to the surplus wallet as USDC on Base.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=False)
        return sub

    verb("sweep", lambda a, _: sweep.sweep(), "pay out what landed on Base, bridge more SOL (the pinned job)")
    verb("status", lambda a, _: sweep.status(), "the settings and the last runs")
    verb("log", lambda a, _: {"runs": sweep.runs(a.max)}, "the runs, newest first").add_argument(
        "--max", type=_bounded(1, 500, "--max"), default=20)
    return root


def main(argv: list[str]) -> int:
    return hand.run("surplus", parser(), argv)
