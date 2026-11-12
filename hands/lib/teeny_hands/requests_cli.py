"""The verbs of hands/requests (docs/hands.md, "requests"): the build
requests every channel records (teeny_hands.requests), and the one Teeny
picks for the builder.

  R  list [--status S] [--max N]   the requests, newest first
  R  get ID                        one request and its ledger
  A  pick  {id, reason}            the one to build next; one at a time
  A  skip  {id, reason}            not doing it
  O  reset ID [--note T]           back to pending, whatever it was
  R  vote                          the weekly vote: open with the count so far,
                                   or its result, and the winner pick takes next
  A  vote-open {text, options}     post a vote on 2-4 pending requests (hands/x)
  R  prices                        what a paid build request costs (evals/prices.toml)

`pick` and `skip` write only state/hands.db and tell nobody, so DRY_RUN does
not stop them; whatever is built from a pick goes out through hands that keep
DRY_RUN. Nothing here answers the person who asked.
"""

from __future__ import annotations

import argparse

from . import hand
from . import requests, votes


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/requests", description="Build requests, and the one Teeny picked.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    listing = verb("list", lambda a, _: requests.listing(a.status, a.max), "the requests, newest first")
    listing.add_argument("--status", choices=requests.STATUSES, default=None)
    listing.add_argument("--max", type=_bounded(1, 100, "--max"), default=20)
    verb("get", lambda a, _: requests.get(a.id), "one request and its ledger").add_argument(
        "id", type=_bounded(1, 2**62, "ID"), metavar="ID"
    )
    verb("pick", lambda a, body: requests.pick(body), "pick a pending request to build (JSON on stdin)", body=True)
    verb("skip", lambda a, body: requests.skip(body), "skip a pending request (JSON on stdin)", body=True)
    verb("vote", lambda a, _: votes.current(), "the weekly vote and its count")
    verb("vote-open", lambda a, body: votes.open_vote(body), "post a weekly vote (JSON on stdin)", body=True)
    def price_list(a, _):
        from .wallet import sales

        return sales.price_list()

    verb("prices", price_list, "what a paid build request costs, and how to pay")
    resetting = verb("reset", lambda a, _: requests.reset(a.id, a.note), "put a request back to pending (operator)")
    resetting.add_argument("id", type=_bounded(1, 2**62, "ID"), metavar="ID")
    resetting.add_argument("--note", default=None)
    return root


def main(argv: list[str]) -> int:
    return hand.run("requests", parser(), argv)
