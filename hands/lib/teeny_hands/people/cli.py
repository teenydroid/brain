"""The verbs of hands/people (docs/hands.md, "people").

  R  list [--all]               regulars (and anyone with an open promise),
                                most recent first; --all lists everyone who wrote
  R  get SURFACE WHO            one person's record
  A  promise                    {surface, who, what, due_in_hours | due_at, ref?}
  A  kept                       {id, note?}: a promise kept; its follow-up cleared
  A  drop                       {id, note?}: a promise Teeny won't keep
  O  forget SURFACE WHO         a person's promises gone, at their request

It writes only state/hands.db and tells nobody, so DRY_RUN does not apply.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import records


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/people", description="Who Teeny talks to, and what it promised them.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, takes_body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=takes_body)
        return sub

    verb("list", lambda a, _: records.listing(regulars_only=not a.all), "regulars and open promises").add_argument(
        "--all", action="store_true")
    get = verb("get", lambda a, _: _get(a.surface, a.who), "one person's record")
    get.add_argument("surface", choices=records.SURFACES)
    get.add_argument("who")
    verb("promise", lambda a, body: records.promise(body), "record a promise, and its follow-up", takes_body=True)
    verb("kept", lambda a, body: records.close(body, "kept"), "a promise kept", takes_body=True)
    verb("drop", lambda a, body: records.close(body, "dropped"), "a promise dropped", takes_body=True)
    forget = verb("forget", lambda a, _: records.forget(a.surface, a.who), "forget a person's promises (operator)")
    forget.add_argument("surface", choices=records.SURFACES)
    forget.add_argument("who")
    return root


def _get(surface: str, who: str) -> dict:
    rec = records.record(surface, who)
    if rec is None:
        raise hand.Refused(f"no record of {who} on {surface}", rule="unknown")
    return {"person": rec}


def main(argv: list[str]) -> int:
    return hand.run("people", parser(), argv)
