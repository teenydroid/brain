"""Asking the judge again about the holds it only made because it failed.

The judge fails closed: a core that is restarting, a revoked model key, or an
answer cut off mid-JSON is a hold ("the judge could not be reached: ...",
"the judge answered in an unreadable shape"). That is right in the moment.
Left there, it is permanent: on 2026-11-11 real mentions and Moltbook
comments sat held after the outage was over, and nobody answered them.

`run` takes those holds, and only those, and does what the guard would have
done had the judge answered:

- an inbound message on a surface whose item can go back to its queue (an
  X mention, a Moltbook comment, a mail) is screened again through the
  guard's own inbound path (`gate.gate_inbound`: the lexicon, then the
  judge, with the stored text). A fresh `clean` returns the item to the
  state its intake offers from (RELEASERS) and the hold is `cleared`. A
  real verdict is written on the hold, which keeps holding, now for that
  reason, and is never asked about again. Another failure leaves the hold as
  it was, for the next run; an unreachable judge ends the run there.
- the account's own draft (any direction but inbound) `expired`: it was
  written for a moment that has passed, and is never sent late. Teeny
  drafts afresh.

A hold any check made from a real verdict (the lexicon, the links, the judge
naming a reason) is never touched, nor one an operator has decided, nor one
younger than GRACE_SECS (its intake may still be at work on it). Nothing is
released without a fresh `clean` from the judge: with `SAFETY_JUDGE` off,
nothing is asked at all. Every outcome is written on the hold
(`rescreened_at`, `rescreen`). bin/lib/health.py runs it, at most LIMIT
holds a run, right after the model has answered its probe.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .. import hand
from . import gate, holds
from .judge import UNREACHABLE, UNREADABLE
from .screen import INBOUND

log = logging.getLogger(__name__)

#: Holds looked at per run, at most.
LIMIT = 10
#: A hold younger than this is left alone.
GRACE_SECS = 120.0
#: What a run can come to for one hold (the `rescreen` column).
OUTCOMES = ("cleared", "prohibited", "unreachable", "unreadable", "expired", "release_failed")


def _release_x(held: dict[str, Any]) -> bool:
    from ..x import mentions

    return mentions.release_held(str(held["context"]["post_id"]), held["id"])


def _release_moltbook(held: dict[str, Any]) -> bool:
    from ..moltbook import inbox

    return inbox.release_held(held["context"], held["text"])


def _release_mail(held: dict[str, Any]) -> bool:
    from ..mail import inbox

    return inbox.release_held(str(held["context"]["message_id"]), held["id"])


# surface -> (the context key naming the held item, what returns it to its
# queue and says whether it did). A surface not here is never asked about:
# a telegram message cannot be answered after the fact, and an operator's
# task is the operator's.
RELEASERS: dict[str, tuple[str, Callable[[dict[str, Any]], bool]]] = {
    "x": ("post_id", _release_x),
    "moltbook": ("comment_id", _release_moltbook),
    "mail": ("message_id", _release_mail),
}

_RELEASED = {
    "x": "the mention went back to pending, for the next ingest to offer",
    "moltbook": "the comment went into Teeny's inbox",
    "mail": "the message went back to pending, for the next ingest to offer",
}
_NOT_RELEASED = {
    "x": "nothing released: the mention is no longer held",
    "moltbook": "nothing released: the comment is already in Teeny's inbox",
    "mail": "nothing released: the message is no longer held",
}
_EXPIRED = (
    "expired: held only because the judge could not answer; a stale draft is never sent, Teeny drafts afresh"
)


def _counts() -> dict[str, Any]:
    return {"checked": 0, "released": 0, **{outcome: 0 for outcome in OUTCOMES}}


def _failure(held: gate.Held) -> str | None:
    """"unreachable" or "unreadable" when the judge failed again; None for a
    verdict."""
    if held.by != "judge" or held.category != "unknown":
        return None
    if held.reason.startswith(UNREACHABLE):
        return "unreachable"
    if held.reason == UNREADABLE:
        return "unreadable"
    return None


def _inbound(hold: dict[str, Any]) -> str:
    """Screens one held message again and acts on the answer; its outcome."""
    again = gate.gate_inbound(hold["text"])  # no context: nothing new is recorded
    if again is not None:
        failure = _failure(again)
        if failure is not None:
            holds.note_rescreen(hold["id"], failure)
            return failure
        holds.reclassify(hold["id"], again.category, again.reason, again.by)
        return "prohibited"
    claimed = holds.claim_release(hold["id"])
    if claimed is None:  # an operator took it meanwhile
        return ""
    surface = claimed["context"]["surface"]
    try:
        released = RELEASERS[surface][1](claimed)
    except Exception as error:  # noqa: BLE001 - the hold goes back, tried again next run
        log.warning("guard: re-screen could not release hold %s: %s", hold["id"], error)
        holds.unclaim_release(hold["id"], str(error))
        holds.note_rescreen(hold["id"], "release_failed")
        return "release_failed"
    holds.clear(hold["id"], _RELEASED[surface] if released else _NOT_RELEASED[surface])
    return "released" if released else "cleared"


def run(limit: int = LIMIT, grace: float = GRACE_SECS) -> dict[str, Any]:
    """One pass over the holds an outage made: counts of what came of them."""
    out = _counts()
    if not gate.judge_enabled():
        out["skipped"] = "the judge is off"
        return out
    before = holds.iso(hand.now() - grace)
    keys = {surface: key for surface, (key, _) in RELEASERS.items()}
    for hold in holds.infra_holds(before, limit, keys):
        out["checked"] += 1
        if hold["direction"] != INBOUND:
            if holds.expire(hold["id"], _EXPIRED) is not None:
                out["expired"] += 1
            continue
        outcome = _inbound(hold)
        if outcome == "released":
            out["released"] += 1
            outcome = "cleared"
        if outcome:
            out[outcome] += 1
        if outcome == "unreachable":
            break  # the rest would fail the same way
    return out
