"""The verbs of hands/trend (docs/hands.md, "trend"). Both are reads (R): they
write only the cache and the tool's home under state/trend/, and are safe to
give a drafting agent.

  R  top [--slot KEY] [--limit N] [--leading N] [--posts N] [--fresh]
                        Digg's AI and tech stories, merged by Digg Score,
                        the leading N with their first X posts; kept for
                        the slot, so a rerun asks Digg nothing
  R  story CLUSTER_ID [--max N]
                        one story's sources (X posts and pages), by Digg's rank

A failure is {"ok": false, "error", "reason"}, exit 1, `reason` one of
missing, timeout, rate_limited, failed, bad_json (teeny_hands.trend.digg),
or empty for a leaderboard with no story.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import top as reads


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/trend", description="What AI X is talking about today, read only.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=False)
        return sub

    top = verb("top", lambda a, _: reads.top(a.slot, a.limit, a.leading, a.posts, a.fresh),
               "the AI and tech leaderboards, merged")
    top.add_argument("--slot", default=None, help="the cache key (bin/studio passes <date>:<slot>)")
    top.add_argument("--limit", type=_bounded(1, 30, "--limit"), default=10)
    top.add_argument("--leading", type=_bounded(0, 10, "--leading"), default=4)
    top.add_argument("--posts", type=_bounded(1, 10, "--posts"), default=3)
    top.add_argument("--fresh", action="store_true", help="read Digg again even when the slot is cached")
    story = verb("story", lambda a, _: reads.story(a.cluster, a.max), "one story's sources")
    story.add_argument("cluster", metavar="CLUSTER_ID")
    story.add_argument("--max", type=_bounded(1, 50, "--max"), default=10)
    return root


def main(argv: list[str]) -> int:
    return hand.run("trend", parser(), argv)
