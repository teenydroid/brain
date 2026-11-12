"""The verbs of hands/wallet (docs/hands.md, "wallet").

  R  address                       Teeny's Solana and EVM addresses (one recovery phrase)
  R  balance                       SOL and USDC on Solana; ETH and USDC on each EVM network (mainnet)
  R  ledger [--max N]              this hand's payments and the core's spend ledger
  R  costs [--days N]              what keeping Teeny running cost: models, x402, bills
  R  receipt ID                    one payment (or buy-N, one $TINY buy): amount, purpose, signature, Solscan
  R  receipts [--max N]            paid payments and buys no X post has carried the signature of yet
  R  caps                          the caps it pays under
  R  conversions [--max N]         the swaps and bridges, newest first
  R  x402 log [--max N]            the paid HTTP requests, newest first
  A  pay                           {to, amount_usdc | amount_sol | amount_eth, chain?, purpose, ref} (pay.py)
  A  swap                          {chain, from, to, amount}: one chain, through deBridge (convert.py)
  A  bridge                        {from_chain, from, to_chain, to, amount}: across chains, deBridge DLN
  A  x402 fetch                    {url, method?, headers?, body?, max_usd?}: an HTTP request that may pay (x402.py)
  J  buy-tiny                      a share of net inflows into $TINY, buy only (buy.py; bin/tiny-buy)
  O  bills add NAME --usd N --period month|year [--note T], bills list, bills remove NAME
  O  vendors add NAME ADDRESS [--chain C] [--note T], vendors list, vendors remove NAME
  O  x402 hosts add HOST [--note T], x402 hosts list, x402 hosts remove HOST

`pay`, `swap`, `bridge`, `x402 fetch` and `buy-tiny` are never a
playbook's output (bin/lib/playbook.py), and the drafting agents have no
tools. The mind reaches the first four only through its intents
(bin/lib/tick.py: `transfer`, `swap`, `bridge`, `x402_fetch`), off until
WALLET_MIND_ACTIONS=1, with the fields each takes and nothing more.
`buy-tiny` is never given to a model: only the pinned tiny-buy job runs it.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import buy, convert, pay, reads, x402


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/wallet", description="Teeny's own wallet (Solana and EVM, one recovery phrase), under hard caps.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False, under: Any = None) -> argparse.ArgumentParser:
        sub = (under or verbs).add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    verb("address", lambda a, _: reads.address(), "Teeny's Solana and EVM addresses")
    verb("balance", lambda a, _: reads.balance(), "balances on Solana and the EVM networks")
    verb("ledger", lambda a, _: reads.ledger(a.max), "payments, newest first").add_argument("--max", type=_bounded(1, 500, "--max"), default=50)
    verb("costs", lambda a, _: reads.costs(a.days), "what keeping Teeny running cost").add_argument("--days", type=_bounded(1, 366, "--days"), default=30)
    verb("receipt", lambda a, _: reads.receipt_for(a.id), "one payment's receipt, or buy-N").add_argument("id", metavar="ID")
    verb("receipts", lambda a, _: reads.receipts_due(a.max), "paid payments still owed a receipt post").add_argument("--max", type=_bounded(1, 50, "--max"), default=5)
    verb("caps", lambda a, _: reads.caps_status(), "the caps it pays under")
    verb("pay", lambda a, body: pay.pay(body), "pay a bounty winner or an allowlisted vendor", body=True)
    verb("buy-tiny", lambda a, _: buy.buy_tiny(), "buy $TINY with a share of net inflows (the pinned job only)")
    verb("swap", lambda a, body: convert.convert("swap", body), "swap on one chain, to Teeny's own address (deBridge)", body=True)
    verb("bridge", lambda a, body: convert.convert("bridge", body), "bridge to Teeny's own address on another chain (deBridge DLN)", body=True)
    verb("conversions", lambda a, _: convert.conversions(a.max), "swaps and bridges, newest first").add_argument("--max", type=_bounded(1, 500, "--max"), default=50)

    paid = verbs.add_parser("x402", help="HTTP requests that pay for themselves (x402)").add_subparsers(dest="x402_verb")
    verb("fetch", lambda a, body: x402.fetch(body), "one request that may pay, under the caps", body=True, under=paid)
    verb("log", lambda a, _: x402.log(a.max), "paid requests, newest first", under=paid).add_argument("--max", type=_bounded(1, 500, "--max"), default=50)
    hosts = paid.add_parser("hosts", help="the x402 host allowlist (operator)").add_subparsers(dest="hosts_verb")
    add = verb("add", lambda a, _: x402.hosts_add(a.host, a.note), "allow a host to be paid", under=hosts)
    add.add_argument("host", metavar="HOST")
    add.add_argument("--note")
    verb("list", lambda a, _: x402.hosts_list(), "the allowlist", under=hosts)
    verb("remove", lambda a, _: x402.hosts_remove(a.host), "remove a host", under=hosts).add_argument("host", metavar="HOST")

    bills = verbs.add_parser("bills", help="fixed bills (operator)").add_subparsers(dest="bills_verb")
    add = verb("add", lambda a, _: reads.bills_add(a.name, a.usd, a.period, a.note), "add or replace a bill", under=bills)
    add.add_argument("name", metavar="NAME")
    add.add_argument("--usd", required=True)
    add.add_argument("--period", choices=("month", "year"), default="month")
    add.add_argument("--note")
    verb("list", lambda a, _: reads.bills_list(), "the bills", under=bills)
    verb("remove", lambda a, _: reads.bills_remove(a.name), "remove a bill", under=bills).add_argument("name", metavar="NAME")

    vendors = verbs.add_parser("vendors", help="the vendor allowlist (operator)").add_subparsers(dest="vendors_verb")
    add = verb("add", lambda a, _: reads.vendors_add(a.name, a.address, a.note, a.chain), "allow a vendor's address", under=vendors)
    add.add_argument("name", metavar="NAME")
    add.add_argument("address", metavar="ADDRESS")
    add.add_argument("--chain", default="solana")
    add.add_argument("--note")
    verb("list", lambda a, _: reads.vendors_list(), "the allowlist", under=vendors)
    verb("remove", lambda a, _: reads.vendors_remove(a.name), "remove a vendor", under=vendors).add_argument("name", metavar="NAME")
    return root


def main(argv: list[str]) -> int:
    return hand.run("wallet", parser(), argv)
