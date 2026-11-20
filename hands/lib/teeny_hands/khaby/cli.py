"""The verbs of hands/khaby (docs/hands.md, "khaby"). It makes no video and
posts nothing; the one thing it spends on is dissect's model call.

  R  find [--max N] [--fresh]
                        Khaby Lame's recent videos (KHABY_ACCOUNTS, listed
                        through the ingest service), the used ones left out,
                        ranked by views; kept for the hour
  R  dissect            (JSON on stdin: {id} or {url}) one video fetched and
                        sent whole to a video-input model: which seconds and
                        frames are the setup clip and which his reaction,
                        what is editable, his beats; checked by the code

  J  used               (JSON on stdin: {video_id, source, url, title?, study_id?})
                        marks a video as used (the remake ledger's
                        remake_sources), so neither find nor hands/remake
                        offers it again; a dry room only says what it would mark

A refusal is exit 2 with `rule`, one of off, model, per_day, source, fetch, ingest.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from ..remake import ledger
from ..remake.cli import _bounded
from . import dissect, find


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/khaby", description="Khaby Lame's videos, found and dissected.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    found = verb("find", lambda a, _: find.find(a.max, fresh=a.fresh), "his recent videos not used yet, by views")
    found.add_argument("--max", type=_bounded(1, 50, "--max"), default=10)
    found.add_argument("--fresh", action="store_true", help="list the accounts again even when the hour is cached")
    verb("dissect", lambda a, body: dissect.dissect(body), "one video's timeline (JSON on stdin)", body=True)
    verb("used", lambda a, body: ledger.record_use(body), "mark a video as used (JSON on stdin)", body=True)
    return root


def main(argv: list[str]) -> int:
    return hand.run("khaby", parser(), argv)
