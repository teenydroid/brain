"""The verbs of hands/chart (docs/hands.md, "chart").

  R  read [--coin COIN] [--days N]
       fetch the candles, draw the PNG, answer the numbers a post may quote.
       A post attaches the PNG at `path` and quotes only the numbers here:
       last_close, change_pct, high, low. No key, no wallet, no trade.

Defaults: the solana token Teeny holds, over the last 7 days.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import candles, render


def _coin(value: str) -> str:
    if not value or not all(ch.isalnum() or ch in "-_" for ch in value):
        raise argparse.ArgumentTypeError("--coin is a bare name: letters, digits, - and _")
    return value


def _days(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 365:
        raise argparse.ArgumentTypeError("--days is 1..365")
    return number


def _read(args: argparse.Namespace, _body: Any) -> dict[str, Any]:
    fetched = candles.fetch(args.coin, args.days)
    out = render.render(candles.rows(args.coin, args.days), render.path_for(args.coin, args.days))
    return {**out, "coin": fetched["coin"], "days": fetched["days"]}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/chart", description="The candles of the token Teeny holds, drawn.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=False)
        return sub

    read = verb("read", _read, "fetch, render, and answer the numbers a post may quote")
    read.add_argument("--coin", type=_coin, default="tiny")
    read.add_argument("--days", type=_days, default=7)
    return root


def main(argv: list[str]) -> int:
    return hand.run("chart", parser(), argv)
