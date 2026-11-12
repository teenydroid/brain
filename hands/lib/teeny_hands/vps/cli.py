"""The verbs of hands/vps (docs/hands.md, "vps").

  R  plans [--max-usd N] [--region R]   Vultr's plans in a region, cheapest first, and the caps
  R  account                            the account's credit and this month's charges
  R  servers                            this hand's orders
  R  server ID                          one server's status and address (no password)
  A  order --plan P --reason T [--region R] [--os NAME]
                                        one server, under VPS_ORDERS, VPS_MAX_MONTHLY_USD
                                        and VPS_MAX_SERVERS
  O  destroy ID --note T                destroy a server this hand bought; it stops billing
  O  release ID --note T                an order stops holding a slot

`order` spends money: it is never given to a model.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import actions


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/vps", description="Teeny's own servers on Vultr, under hard caps.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=False)
        return sub

    listing = verb("plans", lambda a, _: actions.plans(a.max_usd, a.region), "plans and the caps")
    listing.add_argument("--max-usd", type=float, default=None)
    listing.add_argument("--region", default=None)
    verb("account", lambda a, _: actions.account(), "credit and charges")
    verb("servers", lambda a, _: actions.servers(), "this hand's orders")
    verb("server", lambda a, _: actions.server(a.id), "one server").add_argument("id", metavar="ID")
    buy = verb("order", lambda a, _: actions.order(a.plan, a.reason, a.region, a.os), "buy one server")
    buy.add_argument("--plan", required=True)
    buy.add_argument("--reason", required=True)
    buy.add_argument("--region", default=None)
    buy.add_argument("--os", default=None)
    gone = verb("destroy", lambda a, _: actions.destroy(a.id, a.note), "destroy a server (operator)")
    gone.add_argument("id", metavar="ID")
    gone.add_argument("--note", required=True)
    rel = verb("release", lambda a, _: actions.release(a.id, a.note), "an order stops holding a slot (operator)")
    rel.add_argument("id", type=int, metavar="ID")
    rel.add_argument("--note", required=True)
    return root


def main(argv: list[str]) -> int:
    return hand.run("vps", parser(), argv)
