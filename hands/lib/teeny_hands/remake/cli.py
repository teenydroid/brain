"""The verbs of hands/remake (docs/hands.md, "remake"). Both are reads (R):
they write only their caches, the study's files under state/media/remake/ and
the study's own ledger rows, and post nothing.

  R  charts [--slot KEY] [--max N] [--fresh]
                        popular short videos worth remaking (TikTok accounts'
                        recent videos, YouTube's most-popular chart),
                        filtered and ranked by views; kept for the slot
  R  study              (JSON on stdin: {id} or {url}) one video fetched,
                        its shots studied by a vision model: who and what is
                        in each, and whether it is usable at all

  W  used               (JSON on stdin: {video_id, source, url, title?, study_id?})
                        marks a video as made into a remake, so charts leaves
                        it out and study refuses it; a dry room only says
                        what it would mark

A refusal is exit 2 with `rule`, one of off, per_day, source, fetch, ingest.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import charts, ledger, study


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/remake", description="Popular short videos to study and remake.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    top = verb("charts", lambda a, _: charts.top(a.slot, a.max, fresh=a.fresh), "popular videos, ranked by views")
    top.add_argument("--slot", default=None, help="the cache key (bin/studio passes <date>:<slot>)")
    top.add_argument("--max", type=_bounded(1, 50, "--max"), default=15)
    top.add_argument("--fresh", action="store_true", help="ask the providers again even when the slot is cached")
    verb("study", lambda a, body: study.study(body), "study one video (JSON on stdin)", body=True)
    verb("used", lambda a, body: ledger.record_use(body), "mark a video as made into a remake (JSON on stdin)", body=True)
    return root


def main(argv: list[str]) -> int:
    return hand.run("remake", parser(), argv)
