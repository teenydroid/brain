"""The verbs of hands/vps (docs/hands.md, "vps").

  R  products [--max-usd N]          EQVPS's plans, cheapest first, and the caps
  R  servers                         this hand's orders and top-ups
  R  service ID                      one server's status and SSH access (no password)
  A  topup --usd N --reason T        a checkout URL for the balance, under VPS_TOPUP_MAX_USD
  A  order --product SLUG --reason T [--os NAME]
                                     one server from the balance, under VPS_ORDERS,
                                     VPS_MAX_MONTHLY_USD and VPS_MAX_SERVERS
  O  release ID --note T             an order stops holding a slot
  O  register --first F --last L --email E
                                     a new account; its token is printed once

`topup` and `order` spend money: they are never given to a model.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import actions


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/vps", description="Teeny's own servers on EQVPS, under hard caps.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=False)
        return sub

    verb("products", lambda a, _: actions.products(a.max_usd), "plans and the caps").add_argument("--max-usd", type=float, default=None)
    verb("servers", lambda a, _: actions.servers(), "orders and top-ups")
    verb("service", lambda a, _: actions.service(a.id), "one server").add_argument("id", metavar="ID")
    top = verb("topup", lambda a, _: actions.topup(a.usd, a.reason), "a balance checkout")
    top.add_argument("--usd", type=int, required=True)
    top.add_argument("--reason", required=True)
    buy = verb("order", lambda a, _: actions.order(a.product, a.reason, a.os), "buy one server")
    buy.add_argument("--product", required=True)
    buy.add_argument("--reason", required=True)
    buy.add_argument("--os", default=None)
    rel = verb("release", lambda a, _: actions.release(a.id, a.note), "an order stops holding a slot (operator)")
    rel.add_argument("id", type=int, metavar="ID")
    rel.add_argument("--note", required=True)
    reg = verb("register", lambda a, _: actions.register(a.first, a.last, a.email), "a new account (operator)")
    reg.add_argument("--first", required=True)
    reg.add_argument("--last", required=True)
    reg.add_argument("--email", required=True)
    return root


def main(argv: list[str]) -> int:
    return hand.run("vps", parser(), argv)
