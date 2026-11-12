"""The verbs of hands/vps (docs/hands.md, "vps"). `--provider` is `vultr`
or `eqvps` (default `VPS_PROVIDER`, else vultr).

  R  plans [--provider P] [--max-usd N] [--region R]
                                     plans, cheapest first (Vultr: in a region), and the caps
  R  account                         Vultr's credit and this month's charges
  R  servers                         this hand's orders and EQVPS top-ups
  R  server [--provider P] ID        one server's status and address (no password)
  A  order [--provider P] --plan PLAN --reason T [--region R] [--os NAME]
                                     one server, under VPS_ORDERS, VPS_MAX_MONTHLY_USD
                                     and VPS_MAX_SERVERS
  A  topup --usd N --reason T        an EQVPS checkout URL, under VPS_TOPUP_MAX_USD
  O  destroy [--provider P] ID --note T
                                     destroy a Vultr server this hand bought
  O  release ID --note T             an order stops holding a slot
  O  register --first F --last L --email E
                                     a new EQVPS account; its token is printed once

`order`, `topup` and `pay-due` spend money: they are never given to a model.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import actions, bills, client


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/vps", description="Teeny's own servers on Vultr or EQVPS, under hard caps.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, provider: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=False)
        if provider:
            sub.add_argument("--provider", choices=client.PROVIDERS, default=None)
        return sub

    listing = verb("plans", lambda a, _: actions.plans(a.provider, a.max_usd, a.region), "plans and the caps", True)
    listing.add_argument("--max-usd", type=float, default=None)
    listing.add_argument("--region", default=None)
    verb("account", lambda a, _: actions.account(), "Vultr's credit and charges")
    verb("servers", lambda a, _: actions.servers(), "this hand's orders")
    verb("server", lambda a, _: actions.server(a.provider, a.id), "one server", True).add_argument("id", metavar="ID")
    buy = verb("order", lambda a, _: actions.order(a.provider, a.plan, a.reason, a.region, a.os), "buy one server", True)
    buy.add_argument("--plan", required=True)
    buy.add_argument("--reason", required=True)
    buy.add_argument("--region", default=None)
    buy.add_argument("--os", default=None)
    top = verb("topup", lambda a, _: actions.topup(a.usd, a.reason), "an EQVPS balance checkout")
    top.add_argument("--usd", type=int, required=True)
    top.add_argument("--reason", required=True)
    gone = verb("destroy", lambda a, _: actions.destroy(a.provider, a.id, a.note), "destroy a server (operator)", True)
    gone.add_argument("id", metavar="ID")
    gone.add_argument("--note", required=True)
    rel = verb("release", lambda a, _: actions.release(a.id, a.note), "an order stops holding a slot (operator)")
    rel.add_argument("id", type=int, metavar="ID")
    rel.add_argument("--note", required=True)
    inv = verbs.add_parser("invoice", help="record a server invoice to pay (operator)").add_subparsers(dest="invoice_verb")
    rec = inv.add_parser("add", help="record one invoice; its address must be an allowlisted vendor")
    rec.set_defaults(handler=lambda a, _: bills.add(a.provider, a.invoice, a.usd, a.chain, a.pay_to, a.due, a.note), takes_body=False)
    rec.add_argument("--provider", choices=client.PROVIDERS, required=True)
    rec.add_argument("--invoice", required=True)
    rec.add_argument("--usd", required=True)
    rec.add_argument("--chain", default="solana")
    rec.add_argument("--pay-to", required=True)
    rec.add_argument("--due", required=True, help="YYYY-MM-DD")
    rec.add_argument("--note")
    verb("invoices", lambda a, _: bills.listing(), "the server invoices and what became of each")
    verb("pay-due", lambda a, _: bills.pay_due(), "pay the invoices due, under the caps (the pinned pay-bills job)")
    reg = verb("register", lambda a, _: actions.register(a.first, a.last, a.email), "a new EQVPS account (operator)")
    reg.add_argument("--first", required=True)
    reg.add_argument("--last", required=True)
    reg.add_argument("--email", required=True)
    return root


def main(argv: list[str]) -> int:
    return hand.run("vps", parser(), argv)
