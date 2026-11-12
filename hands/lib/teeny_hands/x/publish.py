"""The one way to publish. Every write runs, in this order and in this one
invocation: the guard (`check_outbound`: filter, house style, the emoji cap),
the 280 cap on what the guard returned, the posting rules, `DRY_RUN`, X, and
the record. A job script that forgets a step still cannot post unchecked,
because there is no step to forget. Ported from
crates/teeny-x/src/publisher.rs and the host side of
crates/teeny-agent/src/tools/post.rs and teeny-jobs/src/reply/job.rs.

What goes out is always the guard's text, never the caller's. The one
exception is `approved=True`, which only `hands/guard holds release` passes:
the text is a draft the guard held and an operator then approved, so the
guard is not asked again (it would hold it again); every posting rule,
the 280 cap and DRY_RUN still apply.
"""

from __future__ import annotations

import random
import sys
from typing import Any, Mapping

from .. import hand
from . import open_db
from . import cache, media
from . import posting_rules as rules
from .client import XApi, XError, number_setting

try:
    # Imported by name, so a test can stand in for the other hand's gate.
    from ..guard.gate import check_outbound
except ImportError:  # the guard hand is not installed: nothing is published

    def guard_unavailable(text: str, **_: Any) -> Any:
        raise hand.HandError("the content guard (hands/guard) is not installed, so nothing can be published")

    check_outbound = guard_unavailable
else:

    def guard_unavailable(text: str, **_: Any) -> Any:
        raise hand.HandError("the content guard (hands/guard) is not installed, so nothing can be published")


Json = dict[str, Any]


def _max_emoji() -> int:
    return int(number_setting("MAX_EMOJI_PER_POST", 1, 0, 10))


def _guarded(text: str, kind: str, replying_to: str | None = None, context: Mapping[str, Any] | None = None) -> str:
    """The guard's say on `text`; the text to publish when it allows."""
    verdict = check_outbound(
        text,
        surface="x",
        kind=kind,
        replying_to=replying_to,
        max_emoji=_max_emoji(),
        context=dict(context or {}),
    )
    if verdict.kind == "allow" and isinstance(verdict.text, str) and verdict.text.strip():
        return verdict.text
    if verdict.kind == "hold":
        raise hand.Refused("held", category=verdict.category, hold_id=verdict.hold_id)
    if verdict.kind == "refuse":
        raise hand.Refused(verdict.reason, rewrite=True)
    # An answer this hand does not understand is not an "allow".
    raise hand.HandError(f"the guard answered {verdict.kind!r}, which hands/x does not publish on")


def _draft(body: Any, *, quote: bool) -> tuple[str, list[str], str | None, str | None]:
    """The stdin body: text, media ids, a Giphy id, and for an own post the
    post it quotes. Anything else in it is refused, so a URL cannot ride in
    under a field this hand ignores."""
    if not isinstance(body, Mapping):
        raise hand.HandError('stdin must be a JSON object with "text"')
    allowed = {"text", "media_ids", "gif_id"} | ({"quote_of"} if quote else set())
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise hand.HandError(f"unknown field(s) in the body: {', '.join(unknown)}")
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise hand.HandError('"text" must be a non-empty string')
    media_ids = body.get("media_ids") or []
    if not isinstance(media_ids, list) or not all(isinstance(m, str) and m for m in media_ids):
        raise hand.HandError('"media_ids" must be a list of media ids')
    gif_id = body.get("gif_id")
    if gif_id is not None and not isinstance(gif_id, str):
        raise hand.HandError('"gif_id" must be a string')
    quote_of = body.get("quote_of")
    if quote_of is not None and not rules.is_x_id(quote_of):
        raise hand.HandError('"quote_of" must be a numeric X post id')
    return text, list(media_ids), gif_id, quote_of


def _dry(text: str, kind: str, **extra: Any) -> Json:
    """What a rehearsal says: the guard's text, after every rule ran."""
    return {"ok": True, "dry_run": True, "would_post": text, "kind": kind, **extra}


def _db():
    return open_db(rules.SCHEMA)


def post(body: Any, x: XApi | None = None, *, approved: bool = False) -> Json:
    """An own post, or a quote: guard, 280, attachments, then the gap under
    the process-wide posting lock, so two jobs firing in the same minute
    cannot both read the ledger before either is recorded. `approved`: a
    released hold, which skips the guard only."""
    text, media_ids, gif_id, quote_of = _draft(body, quote=True)
    kind = "quote" if quote_of else "post"
    if not approved:
        text = _guarded(text, kind)
    rules.check_length(text)
    live = not hand.dry_run()
    conn = _db()
    rules.check_attachments(conn, media_ids, gif_id, live=live)
    with hand.lock("posting"):
        rules.check_gap(conn)
        rules.check_link_budget(conn, text)
        if not live:
            return _dry(text, kind)
        x = x or twitr.client()
        # Only a real post fetches and uploads its GIF: a refused or
        # rehearsed one uploads nothing.
        if gif_id:
            media_ids = [media.attach_gif(x, gif_id)]
        post_id = x.create_post(text, quote=quote_of, media_ids=media_ids)
        # The post is out. A ledger that cannot record it is logged, not
        # raised: an error here would invite a retry that posts it twice.
        try:
            rules.record_post(conn, post_id, kind, text, quote_of=quote_of, media=media_ids)
        except Exception as exc:  # noqa: BLE001
            print(f"hands/x: published {post_id} but could not record it: {type(exc).__name__}", file=sys.stderr)
    return {"id": post_id, "kind": kind, "text": text}


def _in_doubt(error: XError) -> bool:
    """Whether a failed post may still have gone out. A 4xx is X saying no;
    a 5xx, no answer, or a 2xx without an id leaves it unknown."""
    return error.status is None or error.status >= 500


def reply(to: str, body: Any, x: XApi | None = None, *, approved: bool = False) -> Json:
    """A reply to post `to`: guard (told whom it answers), 280, never the
    account's own post, attachments, then under the replying lock one reply
    per post and the pacing. The reply id is recorded the moment X returns
    it, before the ledger or anything else that can fail."""
    if not rules.is_x_id(to):
        raise hand.HandError("--to must be a numeric X post id")
    text, media_ids, gif_id, _ = _draft(body, quote=False)
    x = x or twitr.client()
    reads = cache.reads(x)
    target = reads.get_post(to)
    if target is None:
        raise hand.Refused(f"X does not have post {to} (deleted, protected or not visible); nothing was published", rule="target")
    account = reads.identity()
    if not approved:
        text = _guarded(
            text,
            "reply",
            replying_to=target.get("authorUsername"),
            context={
                "post_id": to,
                "conversation_id": target.get("conversationId"),
                "author_username": target.get("authorUsername"),
            },
        )
    rules.check_length(text)
    rules.check_not_own(target, account["id"])
    live = not hand.dry_run()
    conn = _db()
    rules.check_attachments(conn, media_ids, gif_id, live=live)
    with hand.lock("replying"):
        rules.check_not_replied(conn, to)
        rules.check_pacing(conn)
        rules.check_link_budget(conn, text)
        if not live:
            return _dry(text, "reply", in_reply_to=to)
        if gif_id:
            media_ids = [media.attach_gif(x, gif_id)]
        with hand.transaction(conn):
            rules.claim_reply(conn, to)
        try:
            reply_id = x.create_post(text, reply_to=to, media_ids=media_ids)
        except XError as error:
            if _in_doubt(error):
                rules.note_reply_in_doubt(conn, to, str(error))
            else:
                rules.release_reply(conn, to)
            raise
        # The reply is out: say so before anything else can fail or stop, so
        # nothing from here on can ever answer this post a second time. If
        # even that write fails, the claim row still stands and refuses a
        # second reply; the failure is logged, never raised, because an error
        # now would invite a retry of a reply that is already out.
        for step, record in (
            ("reply id", lambda: rules.record_reply_id(conn, to, reply_id)),
            ("pacing", lambda: rules.advance_pacing(conn, random.random())),
            ("ledger", lambda: rules.record_post(conn, reply_id, "reply", text, in_reply_to=to, media=media_ids)),
        ):
            try:
                record()
            except Exception as exc:  # noqa: BLE001
                print(f"hands/x: replied {reply_id} but could not record the {step}: {type(exc).__name__}", file=sys.stderr)
    return {"id": reply_id, "in_reply_to": to, "kind": "reply", "text": text}


def like(post_id: str, x: XApi | None = None) -> Json:
    """Likes a post as the account. Nothing is said, so there is no guard;
    under DRY_RUN nothing reaches X, not even "who am I". A like that went
    out is written to `x_likes`."""
    if not rules.is_x_id(post_id):
        raise hand.HandError("the post to like must be a numeric X post id")
    if hand.dry_run():
        return {"dry_run": True, "would_like": post_id}
    x = x or twitr.client()
    x.like_post(post_id, user_id=cache.reads(x).identity()["id"])
    # The like is out: a ledger that cannot record it is logged, not raised.
    try:
        rules.record_like(_db(), post_id)
    except Exception as exc:  # noqa: BLE001
        print(f"hands/x: liked {post_id} but could not record it: {type(exc).__name__}", file=sys.stderr)
    return {"liked": post_id}


def unsay(post_id: str, confirm: bool, x: XApi | None = None) -> Json:
    """Deletes one of the account's own posts. Without `confirm` it only
    shows what would go, so a deletion is always a second, deliberate call."""
    if not rules.is_x_id(post_id):
        raise hand.HandError("the post to unsay must be a numeric X post id")
    x = x or twitr.client()
    reads = cache.reads(x)
    post = reads.get_post(post_id)
    if post is None:
        raise hand.Refused(f"X does not have post {post_id}; nothing to unsay", rule="target")
    if post.get("authorId") != reads.identity()["id"]:
        raise hand.Refused(f"post {post_id} is not the account's own; only its own posts can be unsaid", rule="self")
    if not confirm:
        return {"preview": True, "would_delete": post_id, "post": post}
    if hand.dry_run():
        return {"dry_run": True, "would_delete": post_id}
    return {"id": post_id, "deleted": x.delete_post(post_id)}
