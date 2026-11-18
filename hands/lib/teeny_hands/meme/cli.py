"""The verbs of hands/meme (docs/hands.md, "meme").

  A  caption {image, top?, bottom?, style?, alt?}     words drawn on one picture: meme-<id>.jpg|png
  A  comic {panels: [{image, caption?}], layout?, captions?, alt?}
                                                      2 to 4 pictures in one frame, lettered: comic-<id>.jpg|png
  R  gif-search QUERY [--max N]                       Giphy candidates {id, title, url, rating} for a post's gif_id
  R  list [--max N]                                   the memes and comics made, newest first
  R  get ID                                           one

Making a meme or a comic costs nothing and publishes nothing: a post
attaches the file through `hands/x upload --file PATH --alt TEXT`, and a
GIF by its id through `gif_id` on `hands/x post`.
"""

from __future__ import annotations

import argparse

from .. import hand
from . import giphy, memes


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/meme", description="Teeny's memes, comics and reaction GIFs.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    verb("caption", lambda a, body: memes.caption(body), "words drawn on one picture (JSON on stdin)", body=True)
    verb("comic", lambda a, body: memes.comic(body), "2 to 4 pictures in one frame, lettered (JSON on stdin)", body=True)
    searching = verb("gif-search", lambda a, _: giphy.search(a.query, a.max), "reaction GIFs on Giphy, rated g or pg")
    searching.add_argument("query")
    searching.add_argument("--max", type=_bounded(1, giphy.MAX_RESULTS, "--max"), default=giphy.DEFAULT_RESULTS)
    verb("list", lambda a, _: memes.listing(a.max), "the memes and comics made, newest first").add_argument(
        "--max", type=_bounded(1, 100, "--max"), default=20
    )
    verb("get", lambda a, _: memes.get(a.id), "one meme or comic").add_argument(
        "id", type=_bounded(1, 2**62, "ID"), metavar="ID"
    )
    return root


def main(argv: list[str]) -> int:
    return hand.run("meme", parser(), argv)
