"""The verbs of hands/wallet (docs/hands.md, "wallet").

  R  address                       Teeny's Solana address and its Solscan page
  R  balance                       SOL (the core), USDC and $TINY (Solana RPC), raw and display
  R  tiny                          Teeny's $TINY: {mint, owner, raw, decimals, amount}, read only
  R  ledger [--max N]              this hand's payments and the core's spend ledger
  R  costs [--days N]              what keeping Teeny running cost: models, x402, bills
  R  receipt ID                    one payment: amount, recipient, purpose, signature, Solscan
  R  receipts [--max N]            paid payments no X post has carried the signature of yet
  R  caps                          the caps it pays under
  A  pay                           {to, amount_usdc | amount_sol, purpose, ref} (pay.py)
  O  bills add NAME --usd N --period month|year [--note T], bills list, bills remove NAME
  O  vendors add NAME ADDRESS [--note T], vendors list, vendors remove NAME

`pay` is never given to a model: bin/lib/playbook.py refuses it as a
playbook's output, and the drafting agents have no tools.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import pay, reads


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/wallet", description="Teeny's own Solana wallet, under hard caps.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False, under: Any = None) -> argparse.ArgumentParser:
        sub = (under or verbs).add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    verb("address", lambda a, _: reads.address(), "Teeny's Solana address")
    verb("balance", lambda a, _: reads.balance(), "SOL, USDC and $TINY, raw and display")
    verb("tiny", lambda a, _: reads.tiny(), "Teeny's $TINY balance, in tokens")
    verb("ledger", lambda a, _: reads.ledger(a.max), "payments, newest first").add_argument("--max", type=_bounded(1, 500, "--max"), default=50)
    verb("costs", lambda a, _: reads.costs(a.days), "what keeping Teeny running cost").add_argument("--days", type=_bounded(1, 366, "--days"), default=30)
    verb("receipt", lambda a, _: reads.receipt(a.id), "one payment's receipt").add_argument("id", type=int, metavar="ID")
    verb("receipts", lambda a, _: reads.receipts_due(a.max), "paid payments still owed a receipt post").add_argument("--max", type=_bounded(1, 50, "--max"), default=5)
    verb("caps", lambda a, _: reads.caps_status(), "the caps it pays under")
    verb("pay", lambda a, body: pay.pay(body), "pay a bounty winner or an allowlisted vendor", body=True)

    bills = verbs.add_parser("bills", help="fixed bills (operator)").add_subparsers(dest="bills_verb")
    add = verb("add", lambda a, _: reads.bills_add(a.name, a.usd, a.period, a.note), "add or replace a bill", under=bills)
    add.add_argument("name", metavar="NAME")
    add.add_argument("--usd", required=True)
    add.add_argument("--period", choices=("month", "year"), default="month")
    add.add_argument("--note")
    verb("list", lambda a, _: reads.bills_list(), "the bills", under=bills)
    verb("remove", lambda a, _: reads.bills_remove(a.name), "remove a bill", under=bills).add_argument("name", metavar="NAME")

    vendors = verbs.add_parser("vendors", help="the vendor allowlist (operator)").add_subparsers(dest="vendors_verb")
    add = verb("add", lambda a, _: reads.vendors_add(a.name, a.address, a.note), "allow a vendor's address", under=vendors)
    add.add_argument("name", metavar="NAME")
    add.add_argument("address", metavar="ADDRESS")
    add.add_argument("--note")
    verb("list", lambda a, _: reads.vendors_list(), "the allowlist", under=vendors)
    verb("remove", lambda a, _: reads.vendors_remove(a.name), "remove a vendor", under=vendors).add_argument("name", metavar="NAME")
    return root


def main(argv: list[str]) -> int:
    return hand.run("wallet", parser(), argv)
