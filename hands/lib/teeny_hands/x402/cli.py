"""The verbs of hands/x402 (docs/hands.md, "x402").

  J  fund                          top Base USDC up to the target when it is under the floor (fund.py; bin/x402-fund)
  R  fund status                   the floor and target, Base USDC now, the last runs
  R  log [--max N]                 the paid requests (`x402_requests`) and the fund runs, newest first
  A  search QUERY [--n N] [--type auto|fast|deep] [--contents text|highlights|summary]   Exa search, paid (search.py)
  A  contents URL...               Exa contents of up to ten pages, paid
  A  crawl QUERY [--n N] [--scrape]   Firecrawl's x402 search, paid; off unless X402_FIRECRAWL=1

`search`, `contents` and `crawl` spend the wallet's money, so no playbook
reaches them (bin/lib/playbook.py); the mind reaches the first two through
its `web_search` and `read_page` intents (bin/lib/tick.py), each held to
MIND_SEARCHES_PER_DAY. `fund` is the pinned x402-fund job's, never a model's.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import fund, search


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/x402", description="Teeny's x402 rail: funded from its own wallet, paying for web search.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=False)
        return sub

    f = verb("fund", lambda a, _: fund.status() if a.what == "status" else fund.fund(), "top up Base USDC (the pinned job), or `fund status`")
    f.add_argument("what", nargs="?", choices=("status",))
    verb("log", lambda a, _: fund.log(a.max), "paid requests and fund runs, newest first").add_argument(
        "--max", type=_bounded(1, 500, "--max"), default=20)

    s = verb("search", lambda a, _: search.search(a.query, a.n, a.type, a.contents), "web search through Exa, paid over x402")
    s.add_argument("query", metavar="QUERY")
    s.add_argument("--n", type=int, default=5)
    s.add_argument("--type", choices=search.TYPES, default="auto")
    s.add_argument("--contents", choices=search.CONTENTS, default="text")
    verb("contents", lambda a, _: search.contents(a.urls), "the text of up to ten pages, through Exa").add_argument(
        "urls", metavar="URL", nargs="+")
    c = verb("crawl", lambda a, _: search.crawl(a.query, a.n, a.scrape), "Firecrawl's x402 search (off by default)")
    c.add_argument("query", metavar="QUERY")
    c.add_argument("--n", type=int, default=5)
    c.add_argument("--scrape", action="store_true")
    return root


def main(argv: list[str]) -> int:
    return hand.run("x402", parser(), argv)
