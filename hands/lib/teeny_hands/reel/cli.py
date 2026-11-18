"""The verbs of hands/reel (docs/hands.md, "reel").

  A  make {kind, script, materials, aspect?, voice?, captions?, bed?, audio?, title?}
                       a clip or a song, rendered by the reel service, to state/media/
  A  music {style, lyrics?, seconds?}
                       a track bought on Surplus, to state/media/ (off until REEL_MUSIC=1)
  R  list [--max N]    the clips, songs and tracks made, newest first, and today's media spend
  R  get ID            one

Making publishes nothing: a post attaches the file through hands/x upload.
"""

from __future__ import annotations

import argparse

from .. import hand
from . import reels


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/reel", description="Clips and songs of Teeny, with voice and captions.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    verb("make", lambda a, body: reels.make(body), "render a clip or a song (JSON on stdin)", body=True)
    verb("music", lambda a, body: reels.music(body), "buy a track (JSON on stdin)", body=True)
    verb("list", lambda a, _: reels.listing(a.max), "what was made, newest first").add_argument(
        "--max", type=_bounded(1, 100, "--max"), default=20
    )
    verb("get", lambda a, _: reels.get(a.id), "one").add_argument("id", type=_bounded(1, 2**62, "ID"), metavar="ID")
    return root


def main(argv: list[str]) -> int:
    return hand.run("reel", parser(), argv)
