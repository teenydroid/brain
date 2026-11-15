"""The verbs of hands/babysit (docs/hands.md, "babysit"). Reads (R) audit and
write only the babysitter's own ledger; their answers carry rule names,
paths and verdicts, never a pull request's words, so they are safe to give
a drafting agent. Acting verbs (A) take a JSON body on stdin and only go
through `actions`.

  R  queue [--repo OWNER/REPO] [--max N]   open pull requests, each audited, worst first
  R  audit OWNER/REPO NUMBER               one pull request: findings and a verdict
  R  injections [--days N] [--max N]       prompt-injection attempts logged (rule, place, digest)
  R  audits [--max N]                      the last audits
  A  triage  {repo, number}                labels and the audit comment (BABYSIT=1)
  A  merge   {repo, number, head_sha}      a ready pull request, at that head (BABYSIT_MERGE=1)
  J  sweep [--max N]                       audit everything open; triage and merge per the switches
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import actions, audit, ledger


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/babysit", description="Reviews, triages and merges Teeny's pull requests.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    listing = verb("queue", lambda a, _: audit.queue(a.repo, a.max), "open pull requests, audited")
    listing.add_argument("--repo", default=None, metavar="OWNER/REPO")
    listing.add_argument("--max", type=_bounded(1, 100, "--max"), default=20)
    one = verb("audit", lambda a, _: audit.audit(a.repo, a.number), "audit one pull request")
    one.add_argument("repo", metavar="OWNER/REPO")
    one.add_argument("number", type=_bounded(1, 10**9, "NUMBER"))
    logged = verb("injections", lambda a, _: {"injections": ledger.injections(a.days, a.max)},
                  "prompt-injection attempts logged")
    logged.add_argument("--days", type=_bounded(1, 365, "--days"), default=30)
    logged.add_argument("--max", type=_bounded(1, 500, "--max"), default=50)
    verb("audits", lambda a, _: {"audits": ledger.audits(a.max)}, "the last audits").add_argument(
        "--max", type=_bounded(1, 200, "--max"), default=20
    )
    verb("sweep", lambda a, _: actions.sweep(a.max), "audit, triage and merge per the switches").add_argument(
        "--max", type=_bounded(1, 100, "--max"), default=20
    )
    verb("triage", lambda a, body: actions.triage(body), "label and comment one pull request (JSON on stdin)", body=True)
    verb("merge", lambda a, body: actions.merge(body), "merge one ready pull request (JSON on stdin)", body=True)
    return root


def main(argv: list[str]) -> int:
    return hand.run("babysit", parser(), argv)
