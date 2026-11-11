"""The verbs of hands/git (docs/hands.md, "git"): how every git command
Teeny runs is set up (teeny_hands.git).

  R  check      git is installed and can commit as Teeny: its version and the
                identity it committed as, from a scratch repository under
                state/ that is removed again
  R  whoami     the identity Teeny commits as (GIT_NAME / GIT_EMAIL)

Holds no token: pushes stay with hands/gh and bin/receipts, which hand theirs to
`git.run` for that one command.
"""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from . import git, hand


def _check(_: argparse.Namespace, __: Any) -> Mapping[str, Any]:
    return git.check()


def _whoami(_: argparse.Namespace, __: Any) -> Mapping[str, Any]:
    name, email = git.identity()
    return {"name": name, "email": email}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="hands/git", description=__doc__.split("\n\n")[0])
    verbs = parser.add_subparsers()
    for name, handler, help_text in (
        ("check", _check, "prove git can commit as Teeny here"),
        ("whoami", _whoami, "the identity Teeny commits as"),
    ):
        verbs.add_parser(name, help=help_text).set_defaults(handler=handler)
    return hand.run("git", parser, argv)
