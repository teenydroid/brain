"""The verb of hands/call (docs/hands.md, "call").

  A  send    stdin {service, method, path, query?, body?}: one call to a
             service in evals/services.toml, with its key, after every rule
             (teeny_hands.call.send)
"""

from __future__ import annotations

import argparse

from .. import hand
from . import send


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/call", description="The key proxy.")
    verbs = root.add_subparsers(dest="verb")
    sending = verbs.add_parser("send", help="one call to a listed service (JSON on stdin)")
    sending.set_defaults(handler=lambda a, body: send.send(body), takes_body=True)
    return root


def main(argv: list[str]) -> int:
    return hand.run("call", parser(), argv)
