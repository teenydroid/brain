"""The verbs of hands/stimuli (docs/hands.md, "stimuli"): the one inbox
Teeny's mind reads (teeny_hands.stimuli).

  A  add {surface, kind, ref, text, who?, payload?}   one event, once
  R  take [--max N] [--chars N]                       a batch for one tick
  A  settle TICK                                      the tick read its batch
  A  release TICK                                     the tick failed; put it back
  R  counts                                           how many in each status
  R  recent [--max N] [--chars N]                     the newest, any status; --chars cuts each
                                                      text to N and leaves the payload out
  A  follow-up {about, in_hours | due_at, ref?}       a reminder to itself
  A  drop-follow-up {id | ref}                        clear a waiting follow-up
  A  due                                              raise the follow-ups now due
  R  follow-ups                                       the ones still waiting

Everything here is local state (state/hands.db): nothing is said anywhere,
so DRY_RUN does not apply.
"""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from . import hand, stimuli


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def _body(body: Any) -> Mapping[str, Any]:
    if not isinstance(body, Mapping):
        raise hand.HandError("needs a JSON object on stdin")
    return body


def _add(_: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    b = _body(body)
    payload = b.get("payload")
    if payload is not None and not isinstance(payload, Mapping):
        raise hand.HandError("payload is an object")
    added = stimuli.add(str(b.get("surface", "")), str(b.get("kind", "")), str(b.get("ref", "")),
                        b.get("text") if isinstance(b.get("text"), str) else "",
                        who=b.get("who") if isinstance(b.get("who"), str) else None, payload=payload)
    return {"added": added}


def _follow_up(_: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    b = _body(body)
    if "due_at" in b:
        due = b["due_at"]
    elif isinstance(b.get("in_hours"), (int, float)):
        due = hand.now() + float(b["in_hours"]) * 3600
    else:
        raise hand.HandError("a follow-up needs in_hours or due_at")
    ref = b.get("ref") if isinstance(b.get("ref"), str) else None
    return stimuli.follow_up(due, str(b.get("about", "")), ref=ref)


def _drop_follow_up(_: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    b = _body(body)
    fid = b.get("id")
    if fid is not None and (isinstance(fid, bool) or not isinstance(fid, int)):
        raise hand.HandError("id is a follow-up's number")
    ref = b.get("ref") if isinstance(b.get("ref"), str) else None
    return {"dropped": stimuli.drop_follow_up(id=fid, ref=ref)}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/stimuli", description="The inbox Teeny's mind reads.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    verb("add", _add, "add one event (JSON on stdin)", body=True)
    taking = verb("take", lambda a, _: stimuli.take(a.max, a.chars), "a batch for one tick")
    taking.add_argument("--max", type=_bounded(1, 200, "--max"), default=20)
    taking.add_argument("--chars", type=_bounded(500, 200_000, "--chars"), default=30_000)
    verb("settle", lambda a, _: {"settled": stimuli.settle(a.tick)}, "the tick read its batch").add_argument("tick")
    verb("release", lambda a, _: {"released": stimuli.release(a.tick)}, "put a failed tick's batch back").add_argument("tick")
    verb("counts", lambda a, _: stimuli.counts(), "how many in each status")
    recent = verb("recent", lambda a, _: stimuli.recent(a.max, a.chars), "the newest, any status")
    recent.add_argument("--max", type=_bounded(1, 500, "--max"), default=50)
    recent.add_argument("--chars", type=_bounded(50, 16_000, "--chars"), default=None)
    verb("follow-up", _follow_up, "a reminder to itself (JSON on stdin)", body=True)
    verb("drop-follow-up", _drop_follow_up, "clear a waiting follow-up by id or ref (JSON on stdin)", body=True)
    verb("due", lambda a, _: {"raised": stimuli.raise_due()}, "raise the follow-ups now due")
    verb("follow-ups", lambda a, _: stimuli.follow_ups(), "the follow-ups still waiting")
    return root


def main(argv: list[str]) -> int:
    return hand.run("stimuli", parser(), argv)
