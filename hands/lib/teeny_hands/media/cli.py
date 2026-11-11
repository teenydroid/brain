"""The verbs of hands/image and hands/video (docs/hands.md, "image" and "video").

  hands/image
    A  generate {prompt}                  one still of Teeny, from its character sheet
    R  list [--max N]                     the images made, newest first, and today's spend
    R  get ID                             one
  hands/video
    A  generate {prompt, image_prompt?}   a still of Teeny, animated as `prompt` says
    R  list [--max N]                     the videos made, newest first, and today's spend
    R  get ID                             one

Generating spends Surplus credit, under the caps in teeny_hands.media.assets,
and publishes nothing: a post attaches the file through hands/x upload.
"""

from __future__ import annotations

import argparse

from .. import hand
from . import assets


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def parser(kind: str) -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog=f"hands/{kind}", description=f"{kind.capitalize()}s of Teeny for its posts.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    make = assets.generate_image if kind == "image" else assets.generate_video
    verb("generate", lambda a, body: make(body), f"make one {kind} (JSON on stdin)", body=True)
    verb("list", lambda a, _: assets.listing(kind, a.max), f"the {kind}s made, newest first").add_argument(
        "--max", type=_bounded(1, 100, "--max"), default=20
    )
    verb("get", lambda a, _: assets.get(a.id), f"one {kind}").add_argument("id", type=_bounded(1, 2**62, "ID"), metavar="ID")
    return root


def main(kind: str, argv: list[str]) -> int:
    return hand.run(kind, parser(kind), argv)
