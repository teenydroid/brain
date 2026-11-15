"""The verbs of hands/listen (docs/hands.md, "listen"). Every verb reads (R):
nothing here writes anywhere but state/hands.db (the ledger, the read cache,
and hands/hn's watch list), so all of it is safe to give a drafting agent
and runs the same under DRY_RUN.

  R  scan [--days D] [--topic T]... [--max N] [--fresh-hours H] [--no-watch]
                                    every source at once: who mentions Teeny,
                                    the standing topics, Lobsters, GitHub, arXiv;
                                    only what is new, kept in the ledger
  R  digest [--days D] [--max N]    what the ledger heard in the last D days, by
                                    source, the loudest first; asks no source
  R  hn QUERY [--days D] [--max N]  one Hacker News search over the last D days
  R  lobsters [--max N]             Lobsters' hottest stories
  R  github [--topic T] [--days D] [--max N]
                                    repositories under a GitHub topic made in the
                                    last D days, most starred first
  R  arxiv [--max N]                the newest papers on agents and multi-agent systems
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import ledger, scan, sources


def _bounded(low: float, high: float, what: str, kind: type = int):
    def parse(value: str) -> Any:
        number = kind(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low:g}..{high:g}")
        return number

    return parse


def _items(items: list) -> dict:
    return {"note": scan.NOTE, "results": items}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/listen", description="What the outside world is saying, read only.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=False)
        return sub

    s = verb("scan", lambda a, _: scan.scan(a.days, a.topic, a.max, a.fresh_hours, not a.no_watch),
             "every source at once, only what is new")
    s.add_argument("--days", type=_bounded(1, 7, "--days"), default=2)
    s.add_argument("--topic", action="append", default=None)
    s.add_argument("--max", type=_bounded(1, 20, "--max"), default=8)
    s.add_argument("--fresh-hours", type=_bounded(0, 168, "--fresh-hours", float), default=20.0)
    s.add_argument("--no-watch", action="store_true")

    d = verb("digest", lambda a, _: ledger.digest(a.days, a.max), "what was heard lately, from the ledger")
    d.add_argument("--days", type=_bounded(1, 30, "--days"), default=2)
    d.add_argument("--max", type=_bounded(1, 30, "--max"), default=8)

    h = verb("hn", lambda a, _: _items(sources.hn(a.query, a.days, a.max)), "one Hacker News search")
    h.add_argument("query", metavar="QUERY")
    h.add_argument("--days", type=_bounded(1, 30, "--days"), default=2)
    h.add_argument("--max", type=_bounded(1, 20, "--max"), default=5)

    lo = verb("lobsters", lambda a, _: _items(sources.lobsters(a.max)), "Lobsters' hottest")
    lo.add_argument("--max", type=_bounded(1, 25, "--max"), default=10)

    g = verb("github", lambda a, _: _items(sources.github(a.max, a.topic, a.days)), "new agent repositories")
    g.add_argument("--topic", default=sources.GITHUB_TOPIC, type=_topic)
    g.add_argument("--days", type=_bounded(1, 30, "--days"), default=sources.GITHUB_DAYS)
    g.add_argument("--max", type=_bounded(1, 10, "--max"), default=5)

    ar = verb("arxiv", lambda a, _: _items(sources.arxiv(a.max)), "the newest agent papers")
    ar.add_argument("--max", type=_bounded(1, 10, "--max"), default=5)
    return root


def _topic(value: str) -> str:
    """A GitHub topic: lowercase letters, digits and dashes."""
    import re

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,49}", value):
        raise argparse.ArgumentTypeError("a GitHub topic is lowercase letters, digits and dashes")
    return value


def main(argv: list[str]) -> int:
    return hand.run("listen", parser(), argv)
