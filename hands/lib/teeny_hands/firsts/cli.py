"""The verbs of hands/firsts (docs/hands.md, "firsts").

  R  list       every first in brain/narrative/FIRSTS.md, with its proof's
                status from bin/proofs and whether it was announced
  R  due        the firsts whose proof is met and never announced, and the
                round numbers crossed this week in stats
  A  announce   {text, first} or {text, round}: checked again, then posted
                through hands/x post; recorded only when it went out
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import calendar


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/firsts", description="Teeny's firsts, posted only when proven.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, takes_body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=takes_body)
        return sub

    verb("list", lambda a, _: calendar.listing(), "every first and its proof")
    verb("due", lambda a, _: calendar.due(), "what is proven and not yet announced")
    verb("announce", lambda a, body: calendar.announce(body), "post one due item through hands/x", takes_body=True)
    return root


def main(argv: list[str]) -> int:
    return hand.run("firsts", parser(), argv)
