"""The verbs of hands/deploy (docs/hands.md, "deploy").

  A  publish JOB                 a shipped build's site folder, static files only, at https://<slug>.<DEPLOY_DOMAIN>/
  R  list [--max N]              what is published, newest first
  O  unpublish SITE --note T     take a site down

`publish` is off until DEPLOY=1 and keeps DRY_RUN. No playbook outputs it yet:
the operator (or a pinned job) publishes.
"""

from __future__ import annotations

import argparse

from .. import hand
from . import publish


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/deploy", description="Teeny's static sites, on subdomains of its own site.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler, help: str) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=False)
        return sub

    verb("publish", lambda a, _: publish.publish(a.job), "publish a shipped build's site").add_argument("job", type=_bounded(1, 2**62, "JOB"), metavar="JOB")
    verb("list", lambda a, _: publish.listing(a.max), "what is published").add_argument("--max", type=_bounded(1, 500, "--max"), default=50)
    down = verb("unpublish", lambda a, _: publish.unpublish(a.site, a.note), "take a site down (operator)")
    down.add_argument("site", metavar="SITE")
    down.add_argument("--note", required=True)
    return root


def main(argv: list[str]) -> int:
    return hand.run("deploy", parser(), argv)
