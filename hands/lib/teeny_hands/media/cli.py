"""The verbs of hands/image and hands/video (docs/hands.md, "image" and "video").

  hands/image
    A  generate {prompt, alt?}            one still of Teeny, from its character sheet, under 5 MB
    R  list [--max N]                     the images made, newest first, and today's spend
    R  scenes [--last N]                  the last N pictures' scenes and the words they share (default 16)
    R  get ID                             one
  hands/video
    A  generate {prompt, image_prompt?}   a still of Teeny, animated as `prompt` says
    A  skit {shots, aspect?, story, figures?, logline, look?, setting?, score?, wait_seconds?, bed?}
                                          a parody video: one text-to-video job a shot, cut
                                          with their own sound and the label (media/skits.py);
                                          off until MEDIA_SKIT_MAX_PER_DAY is set
    R  list [--max N]                     the videos made, newest first, and today's spend
    R  get ID                             one

Generating spends Surplus credit, under the caps in teeny_hands.media.assets,
and publishes nothing: a post attaches the file through hands/x upload.
"""

from __future__ import annotations

import argparse

from .. import hand
from . import assets, skits


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
    if kind == "video":
        verb("skit", lambda a, body: skits.generate_skit(body), "make one skit (JSON on stdin)", body=True)
    if kind == "image":
        verb("scenes", lambda a, _: assets.scenes(a.last), "the scenes of the last pictures, and the words they repeat").add_argument(
            "--last", type=_bounded(1, 100, "--last"), default=16
        )
    return root


def main(kind: str, argv: list[str]) -> int:
    return hand.run(kind, parser(kind), argv)
