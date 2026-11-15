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
- the account's own draft (any direction but inbound) on a surface that
  can send it again (RESENDERS: Moltbook posts and comments, Reddit posts,
  comments and replies, which go back on the browser desk), younger than
  RESEND_SECS, whose hold says how to send it: judged again the same way
  (the links, the lexicon, the judge, on each stored field). A fresh
  `clean` sends it through its hand (`hands/moltbook post` or `comment`),
  which runs the guard again and keeps every posting rule; the hold is
  `cleared` with where it went. A real verdict is written on the hold, which
  keeps holding. Another failure leaves it for the next run. A re-send the
  hand refuses (its gap, its caps, already answered), a post when Teeny
  has posted on Moltbook since the hold, or one the guard holds again
  closes the hold as `expired`, and is never tried again.
- any other draft of the account's own `expired`: it was written for a
  moment that has passed, and is never sent late. Teeny drafts afresh.

A hold any check made from a real verdict (the lexicon, the links, the judge
naming a reason) is never touched, nor one an operator has decided, nor one
younger than GRACE_SECS (its intake may still be at work on it). Nothing is
released without a fresh `clean` from the judge: with `SAFETY_JUDGE` off,
nothing is asked at all. Every outcome is written on the hold
(`rescreened_at`, `rescreen`). bin/lib/health.py runs it, at most LIMIT
holds a run, right after the model has answered its probe, and the pinned
`guard-rescreen` job (`hands/guard rescreen`) every five minutes, so a
comment held by an outage reaches Teeny's inbox within minutes of the judge
answering again. One run at a time (the `guard-rescreen` lock).
"""

from __future__ import annotations

import json
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
#: A draft of Teeny's own older than this is never sent again, only expired
#: (bin/lib/tick.py gives a deferred reply the same hour).
RESEND_SECS = 3600.0
#: How long a run waits for another to finish before it skips.
LOCK_WAIT = 1.0
#: What a run can come to for one hold (the `rescreen` column). `sent` is a
#: draft of Teeny's own judged clean and sent again; `refused` one whose
#: re-send its hand refused or the guard held again; `would_send` one judged
#: clean under DRY_RUN, left pending.
OUTCOMES = ("cleared", "prohibited", "unreachable", "unreadable", "expired", "release_failed", "sent", "refused",
            "would_send")


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


def _fields(held: dict[str, Any]) -> dict[str, str] | None:
    """The stored draft's fields ({"title", "body"}), or None when the hold
    is not a draft of several fields."""
    try:
        fields = json.loads(held["text"])
    except (TypeError, ValueError):
        return None
    if not isinstance(fields, dict) or not fields or not all(isinstance(v, str) for v in fields.values()):
        return None
    return fields


def _moltbook_body(held: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """("post" or "comment", the body `hands/moltbook` takes) to send a held
    draft again exactly as it was written, or None when the hold does not say
    enough (a hold from before its context named the parent or the url)."""
    context, fields = held["context"], _fields(held)
    if fields is None:
        return None
    tags = {key: context[key] for key in ("format", "experiment") if context.get(key) is not None}
    kind = context.get("post_kind")
    if kind == "post" and isinstance(context.get("submolt"), str) and "type" in context and "title" in fields:
        body = {"submolt": context["submolt"], "title": fields["title"], "type": context["type"], **tags}
        if "body" in fields:
            body["body"] = fields["body"]
        if context.get("url"):
            body["url"] = context["url"]
        return "post", body
    if kind == "comment" and isinstance(context.get("post_id"), str) and "parent_id" in context and "body" in fields:
        body = {"post_id": context["post_id"], "body": fields["body"], **tags}
        if context.get("parent_id"):
            body["parent_id"] = context["parent_id"]
        return "comment", body
    return None


def _no_post_since(held: dict[str, Any]) -> None:
    """Refuses the re-send of a post when Teeny has posted on Moltbook since
    the draft was held: the hand's gap only covers POST_GAP, and a re-screen
    that reaches the hold later (a backlog, ten a run) would otherwise put
    the stale draft out on top of the post Teeny wrote instead."""
    from ..moltbook import rules

    since = hand.now() - _age(held)
    conn = hand.open_db(rules.SCHEMA)
    try:
        statuses = ",".join("?" for _ in rules.COUNTED)
        later = conn.execute(
            f"SELECT count(*) FROM moltbook_posts WHERE at >= ? AND status IN ({statuses})", (since, *rules.COUNTED)
        ).fetchone()[0]
    finally:
        conn.close()
    if later:
        raise hand.Refused("Teeny has posted on Moltbook since this draft was held", rule="resend")


def _resend_moltbook(held: dict[str, Any]) -> str:
    """Sends a held Moltbook draft again through hands/moltbook (the guard
    and every posting rule again); where it went."""
    from ..moltbook import actions

    verb, body = _moltbook_body(held)  # type: ignore[misc]
    if verb == "post":
        _no_post_since(held)
        out = actions.post(body)
        return f"re-judged clean and sent: posted on Moltbook as {out.get('posted')}"
    out = actions.comment(body)
    return f"re-judged clean and sent: commented on Moltbook as {out.get('commented')} on {out.get('post_id')}"


def _reddit_body(held: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """(the hands/reddit verb, its body) to send a held Reddit draft again
    exactly as it was written, or None when the hold does not say enough."""
    context, fields = held["context"], _fields(held)
    if fields is None or "body" not in fields:
        return None
    kind = context.get("post_kind")
    if kind == "comment" and isinstance(context.get("thread"), str):
        return "comment", {"thread": context["thread"], "body": fields["body"]}
    if kind == "reply" and isinstance(context.get("comment"), str):
        return "reply", {"comment": context["comment"], "body": fields["body"]}
    if kind == "post" and isinstance(context.get("subreddit"), str) and "title" in fields:
        return "post", {"subreddit": context["subreddit"], "title": fields["title"], "body": fields["body"]}
    return None


def _resend_reddit(held: dict[str, Any]) -> str:
    """Queues a held Reddit draft again through hands/reddit (the guard, the
    caps and the allowlist again); where it went."""
    from ..reddit import actions

    verb, body = _reddit_body(held)  # type: ignore[misc]
    out = getattr(actions, verb)(body)
    return f"re-judged clean and sent: queued as Reddit {verb} task {out.get('task_id')}"


# surface -> (whether a held draft says enough to send again, what sends it
# and says where it went). A surface not here only expires: an X reply is the
# tick's to send again (bin/lib/tick.py), and the rest wait for Teeny.
RESENDERS: dict[str, tuple[Callable[[dict[str, Any]], bool], Callable[[dict[str, Any]], str]]] = {
    "moltbook": (lambda held: _moltbook_body(held) is not None, _resend_moltbook),
    "reddit": (lambda held: _reddit_body(held) is not None, _resend_reddit),
}


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


def _age(hold: dict[str, Any]) -> float:
    import datetime as dt

    try:
        at = dt.datetime.fromisoformat(str(hold["at"]).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("inf")
    return hand.now() - at


def _outbound(hold: dict[str, Any]) -> str:
    """A draft of Teeny's own: judged again and sent when it can be, else
    expired; its outcome."""
    surface = hold["context"].get("surface")
    resendable, resend = RESENDERS.get(surface, (None, None))
    if resend is None or not resendable(hold) or _age(hold) > RESEND_SECS:
        return "expired" if holds.expire(hold["id"], _EXPIRED) is not None else ""
    # Judged as the guard judges a draft (gate.check_fields): the links, the
    # lexicon, then the judge, field by field. Nothing is recorded here; the
    # hand's own guard records whatever it holds on the re-send.
    for value in _fields(hold).values():  # type: ignore[union-attr]
        _, again = gate._screen_outbound(value, surface)
        if again is not None:
            failure = _failure(again)
            if failure is not None:
                holds.note_rescreen(hold["id"], failure)
                return failure
            holds.reclassify(hold["id"], again.category, again.reason, again.by)
            return "prohibited"
    if hand.dry_run():
        holds.note_rescreen(hold["id"], "would_send")
        return "would_send"
    claimed = holds.claim_release(hold["id"])
    if claimed is None:  # an operator took it meanwhile
        return ""
    try:
        effect = resend(claimed)
    except hand.Refused as refusal:
        new_hold = refusal.detail.get("hold_id")
        if refusal.reason == "held" and isinstance(new_hold, int):
            why = f"re-judged clean, but the guard held the re-send (hold {new_hold})"
            if refusal.detail.get("judge_failed"):
                # One re-send per draft: a judge that fails again ends it.
                holds.expire(new_hold, f"the judge failed again on the re-send of hold {hold['id']}; not tried again")
        else:
            why = f"re-judged clean; hands/{surface} refused the re-send: {refusal.reason}"
        holds.expire(hold["id"], why, claimed=True)
        return "refused"
    except Exception as error:  # noqa: BLE001 - the hold goes back, tried again next run
        log.warning("guard: re-screen could not send hold %s again: %s", hold["id"], error)
        holds.unclaim_release(hold["id"], str(error))
        holds.note_rescreen(hold["id"], "release_failed")
        return "release_failed"
    holds.clear(hold["id"], effect)
    return "sent"


def run(limit: int = LIMIT, grace: float = GRACE_SECS) -> dict[str, Any]:
    """One pass over the holds an outage made: counts of what came of them.
    One run at a time: a run that finds another under way skips."""
    out = _counts()
    if not gate.judge_enabled():
        out["skipped"] = "the judge is off"
        return out
    lock = hand.lock("guard-rescreen", timeout=LOCK_WAIT)
    try:
        lock.__enter__()
    except hand.HandError:
        out["skipped"] = "another re-screen is running"
        return out
    try:
        _run(out, limit, grace)
    finally:
        lock.__exit__(None, None, None)
    return out


def _run(out: dict[str, Any], limit: int, grace: float) -> None:
    before = holds.iso(hand.now() - grace)
    keys = {surface: key for surface, (key, _) in RELEASERS.items()}
    for hold in holds.infra_holds(before, limit, keys):
        out["checked"] += 1
        if hold["direction"] != INBOUND:
            outcome = _outbound(hold)
            if outcome:
                out[outcome] += 1
            if outcome == "unreachable":
                break
            continue
        outcome = _inbound(hold)
        if outcome == "released":
            out["released"] += 1
            outcome = "cleared"
        if outcome:
            out[outcome] += 1
        if outcome == "unreachable":
            break  # the rest would fail the same way
