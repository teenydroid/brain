"""The verbs of hands/giveaway (docs/hands.md, "giveaway").

  A  open                {format, pool_tiny, winners, best_reply_tiny, prompt, deadline,
                         threshold?, text}: every cap checked, `text` posted through hands/x
  R  status [ID]         the season against the caps, and the rounds (or one)
  R  announcements       what the giveaway playbooks owe a post for
  A  judge               {round_id, entry_id, reasons, text}: the best reply, after the deadline
  J  entries [ID]        replies to the round post from hands.db only (no X call)
  J  draw [ID]           the seeded draw, past the deadline
  J  settle [ID]         the tree, the round file in the diary, the distribution
  J  clawback [ID]       close a distribution past its clawback time
  J  due                 entries, draw, settle and clawback for every round due

Only `open` and `judge` may be a playbook's output, and an input runs only
`status` and `announcements` (bin/lib/playbook.py, the `giveaway-pool` fence).
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import rounds


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/giveaway", description="Tiny Drops: public $TINY rounds paid by code.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False, round_arg: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        if round_arg:
            sub.add_argument("id", type=int, nargs="?", metavar="ID")
        return sub

    verb("open", lambda a, body: rounds.open_round(body), "open a round and post it", body=True)
    verb("status", lambda a, _: rounds.status(a.id), "the season and the rounds", round_arg=True)
    verb("announcements", lambda a, _: rounds.announcements(), "what the playbooks owe a post for")
    verb("judge", lambda a, body: rounds.judge(body), "the best reply, announced", body=True)
    verb("entries", lambda a, _: rounds.entries(a.id), "collect entries from hands.db", round_arg=True)
    verb("draw", lambda a, _: rounds.draw_due(a.id), "the seeded draw", round_arg=True)
    verb("settle", lambda a, _: rounds.settle_due(a.id), "the tree, the diary and the distribution", round_arg=True)
    verb("clawback", lambda a, _: rounds.clawback_due(a.id), "close a distribution past its clawback", round_arg=True)
    verb("due", lambda a, _: rounds.due(), "every step every round is due for")
    return root


def main(argv: list[str]) -> int:
    return hand.run("giveaway", parser(), argv)
