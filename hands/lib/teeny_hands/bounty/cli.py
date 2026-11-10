"""The verbs of hands/bounty (docs/hands.md, "bounty").

  O  open                {title, rules, prize_usdc, deadline, channels: [x, moltbook]}
  R  list [--status S]   the bounties, newest first
  R  get ID              one bounty, its entry counts and winner
  R  announcements       what the bounty playbooks owe a post for
  J  entries ID          link its posts, collect replies and comments, notice a claim
  A  pick                {bounty_id, entry_id, reasons, text}: after the deadline;
                         `text` is the announcement, posted through hands/x
  J  claim               {bounty_id, address, source_id}: a reply by the winning account
  J  pay ID              the prize, once the winner has been public long enough
  J  pay-due             every bounty: collect, notice claims, pay what is due (bin/bounty-pay)
  O  link ID [--x POST] [--moltbook POST]   name the bounty's posts by hand

Only `pick` may be a playbook's output (bin/lib/playbook.py); `pay` goes
through hands/wallet's rules, which check the bounty again from its rows.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import actions

STATUSES = ("open", "picked", "claimed", "paid", "in_doubt")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/bounty", description="Bounties Teeny opens, judges and pays.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    verb("open", lambda a, body: actions.open_bounty(body), "open a bounty (operator)", body=True)
    verb("list", lambda a, _: actions.listing(a.status), "the bounties").add_argument("--status", choices=STATUSES)
    verb("get", lambda a, _: actions.get(a.id), "one bounty").add_argument("id", type=int, metavar="ID")
    verb("announcements", lambda a, _: actions.announcements(), "what the playbooks owe a post for")
    verb("entries", lambda a, _: actions.entries(a.id), "collect entries").add_argument("id", type=int, metavar="ID")
    verb("pick", lambda a, body: actions.pick(body), "pick and announce the winner", body=True)
    verb("claim", lambda a, body: actions.claim(body), "the winner's address", body=True)
    verb("pay", lambda a, _: actions.pay(a.id), "pay the prize").add_argument("id", type=int, metavar="ID")
    verb("pay-due", lambda a, _: actions.pay_due(), "collect, claim and pay what is due")
    link = verb("link", lambda a, _: actions.link(a.id, a.x, a.moltbook), "name the bounty's posts (operator)")
    link.add_argument("id", type=int, metavar="ID")
    link.add_argument("--x")
    link.add_argument("--moltbook")
    return root


def main(argv: list[str]) -> int:
    return hand.run("bounty", parser(), argv)
