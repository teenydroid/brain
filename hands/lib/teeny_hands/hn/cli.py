"""The verbs of hands/hn (docs/hands.md, "hn"). Every read (R) goes through
the read cache and is safe to give a drafting agent; nothing here writes to
Hacker News, which has no write API and bans bots. `watch` keeps the threads
Teeny follows in state/hands.db: `bin/tick` adds one for the mind's
`watch_hn`, the operator lists and removes them.

  R  top|new|best|ask|show [--max N]          a story list, each story hydrated
  R  item ID                                  one story or comment, its text as plain text
  R  thread ID [--depth D] [--max N]          the comments under an item, in page order,
                                              at most N read
  R  user NAME                                a user: karma, about, what they submitted
  R  search QUERY [--tags story|comment] [--max N] [--days D]
                                              Algolia's search over HN, by relevance;
                                              with --days, over the last D days only
  R  inbox [--days N] [--max N]               comments said to Teeny (direct replies to its
                                              own items) and new ones on watched threads,
                                              screened, into its mind's inbox
  A  watch add ID [--note T]                  follow a thread (bin/tick, for watch_hn)
  R  watch list                               the threads followed
  O  watch remove ID                          stop following one
"""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from .. import hand
from . import inbox, reads, watch


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def _watch(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    if args.action == "list":
        return watch.listing()
    if not args.id:
        raise hand.HandError(f"watch {args.action} needs ID")
    if args.action == "remove":
        return watch.remove(args.id)
    return watch.add(args.id, args.note)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/hn", description="Hacker News, read only.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=False)
        return sub

    for feed in reads.FEEDS:
        listing = verb(feed, lambda a, _, feed=feed: reads.stories(feed, a.max), f"the {feed} stories")
        listing.add_argument("--max", type=_bounded(1, 30, "--max"), default=10)
    verb("item", lambda a, _: reads.item(a.id), "one story or comment").add_argument("id", metavar="ID")
    thread = verb("thread", lambda a, _: reads.thread(a.id, a.depth, a.max), "the comments under an item")
    thread.add_argument("id", metavar="ID")
    thread.add_argument("--depth", type=_bounded(1, 10, "--depth"), default=3)
    thread.add_argument("--max", type=_bounded(1, 100, "--max"), default=30)
    verb("user", lambda a, _: reads.user(a.name), "a user").add_argument("name", metavar="NAME")
    search = verb("search", lambda a, _: reads.search(a.query, a.tags, a.max, days=a.days), "search through Algolia")
    search.add_argument("query", metavar="QUERY")
    search.add_argument("--tags", default=None, choices=reads.SEARCH_TAGS)
    search.add_argument("--max", type=_bounded(1, 50, "--max"), default=10)
    search.add_argument("--days", type=_bounded(1, reads.SEARCH_MAX_DAYS, "--days"), default=None)
    scanning = verb("inbox", lambda a, _: inbox.scan(a.days, a.max), "comments said to Teeny, into its mind's inbox")
    scanning.add_argument("--days", type=_bounded(1, 30, "--days"), default=7)
    scanning.add_argument("--max", type=_bounded(1, 50, "--max"), default=10)
    following = verb("watch", _watch, "the threads Teeny follows")
    following.add_argument("action", choices=("add", "list", "remove"))
    following.add_argument("id", nargs="?", metavar="ID")
    following.add_argument("--note", default=None)
    return root


def main(argv: list[str]) -> int:
    return hand.run("hn", parser(), argv)
