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

from .. import hand, tags
from . import open_db, twitr
from . import cache, media, mentions, peers
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
        # judge_failed: the judge could not judge it, so the tick sends it again
        # next beat (bin/lib/tick.py, RETRIED); a hold it read is never sent again.
        raise hand.Refused("held", category=verdict.category, hold_id=verdict.hold_id,
                           **({"judge_failed": True} if verdict.judge_failed else {}))
    if verdict.kind == "refuse":
        raise hand.Refused(verdict.reason, rewrite=True)
    # An answer this hand does not understand is not an "allow".
    raise hand.HandError(f"the guard answered {verdict.kind!r}, which hands/x does not publish on")


def _draft(body: Any, *, quote: bool) -> tuple[str, list[str], str | None, str | None]:
    """The stdin body: text, media ids, a Giphy id, and for an own post the
    post it quotes and its anchor (read by `post`). Anything else in it is
    refused, so a URL cannot ride in under a field this hand ignores."""
    if not isinstance(body, Mapping):
        raise hand.HandError('stdin must be a JSON object with "text"')
    allowed = {"text", "media_ids", "gif_id"} | ({"quote_of", "anchor"} if quote else set())
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


def link_budget() -> Json:
    """Today's link posts against the cap (`rules.link_budget`). A read of
    the ledger: it writes nothing and calls no one."""
    return rules.link_budget(_db())


def slot() -> Json:
    """Whether an own post would pass the gap and the day's cap now: {open:
    true}, or {why} with the refusal it would get. A read of the ledger: it
    writes nothing and calls no one. A post job reads it as a required input,
    so a shut slot skips the run before the model turn (bin/act)."""
    conn = _db()
    try:
        rules.check_gap(conn)
        rules.check_daily_cap(conn)
    except hand.Refused as refused:
        return {"why": refused.reason}
    return {"open": True}


def post(body: Any, x: XApi | None = None, *, approved: bool = False) -> Json:
    """An own post, or a quote: its anchor, guard, 280, attachments, then the
    gap under the process-wide posting lock, so two jobs firing in the same
    minute cannot both read the ledger before either is recorded. `approved`:
    a released hold, which skips the guard only (the anchor still holds).
    The lab tags and the producer's tags (`rules.studio_tags`) are kept
    beside the post, never published."""
    body, tagged = tags.split(body)
    body, made = rules.studio_tags(body)
    text, media_ids, gif_id, quote_of = _draft(body, quote=True)
    anchor = rules.check_anchor(body.get("anchor"), quote_of)
    kind = "quote" if quote_of else "post"
    if not approved:
        text = _guarded(text, kind, context={"anchor": anchor})
    rules.check_length(text)
    # A quote of somebody else's post is outward: it goes to their community.
    quoted = None
    if quote_of:
        x = x or twitr.client()
        quoted = _quoted(cache.reads(x), quote_of)
    if quoted is not None:
        rules.check_outward(text)
    live = not hand.dry_run()
    conn = _db()
    rules.check_attachments(conn, media_ids, gif_id, live=live)
    with hand.lock("posting"):
        rules.check_gap(conn)
        rules.check_daily_cap(conn)
        if quoted is not None:
            reads = cache.reads(x)
            rules.check_peer_rotation(conn, quoted["authorId"], lambda post_id: _author_of(reads, post_id))
        rules.check_repeat(conn, text)
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
            rules.record_post(conn, post_id, kind, text, quote_of=quote_of, media=media_ids, anchor=anchor,
                              quote_author=quoted["authorId"] if quoted else None, **tagged, **made)
        except Exception as exc:  # noqa: BLE001
            print(f"hands/x: published {post_id} but could not record it: {type(exc).__name__}", file=sys.stderr)
        # A quote of a peer (brain/x-peers.txt) is a touch, whoever quoted.
        if quoted is not None:
            try:
                peers.record_touch(quoted, post_id, tagged.get("format"))
            except Exception as exc:  # noqa: BLE001
                print(f"hands/x: published {post_id} but could not record the peer touch: {type(exc).__name__}",
                      file=sys.stderr)
    return {"id": post_id, "kind": kind, "text": text}


def _quoted(reads: Any, quote_of: str) -> Json | None:
    """The post a quote quotes, when somebody else wrote it; None when it is
    the account's own. Not knowing who wrote it fails closed, as a reply's
    target does."""
    target = reads.get_post(quote_of)
    if target is None:
        raise hand.Refused(f"X does not have post {quote_of} (deleted, protected or not visible); nothing was published",
                           rule="target")
    if not target.get("authorId"):
        raise hand.Refused(f"could not tell who wrote post {quote_of}, so nothing was published", rule="target")
    if target["authorId"] == reads.identity()["id"]:
        return None
    return target


def _author_of(reads: Any, post_id: str) -> str | None:
    """Who wrote an earlier quoted post, for a ledger row that predates
    `quote_author`. A post X no longer shows is nobody's."""
    try:
        post = reads.get_post(post_id)
    except XError:
        return None
    return post.get("authorId") if post else None


def _thread_draft(body: Any) -> tuple[list[str], list[str], str | None]:
    """The stdin body of a thread: its texts, and the head's media ids and
    Giphy id. Anything else in it is refused, `quote_of` by name: a quote is
    not a thread."""
    if not isinstance(body, Mapping):
        raise hand.HandError('stdin must be a JSON object with "posts"')
    if body.get("quote_of") is not None:
        raise hand.Refused("a quote is not a thread: post it with hands/x post. Nothing was published.", rule="thread")
    unknown = sorted(set(body) - {"posts", "anchor", "media_ids", "gif_id"})
    if unknown:
        raise hand.HandError(f"unknown field(s) in the body: {', '.join(unknown)}")
    posts = body.get("posts")
    if not isinstance(posts, list) or not all(isinstance(t, str) and t.strip() for t in posts):
        raise hand.HandError('"posts" must be a list of non-empty strings')
    _, media_ids, gif_id, _ = _draft({"text": "-", **{k: body[k] for k in ("media_ids", "gif_id") if k in body}}, quote=False)
    return list(posts), media_ids, gif_id


def thread(body: Any, x: XApi | None = None) -> Json:
    """A thread: the first text an own post, each later one a reply to the one
    before, so readers see one thread. Every text goes through the anchor
    check, the guard and the 280 cap before anything is sent; one held or too
    long refuses the whole thread. It is one own post for the gap and the day's
    cap (checked once, for the head) and for the link budget, and its
    continuations are not replies, so they skip the reply pacing, the
    one-reply rule and `check_not_own`. If X fails part way, what went out
    stays, is recorded, and is reported (`sent`, `ids`) in the error; nothing
    is retried or deleted. The producer's tags go on the head's row."""
    body, tagged = tags.split(body)
    body, made = rules.studio_tags(body)
    texts, media_ids, gif_id = _thread_draft(body)
    rules.check_thread_size(len(texts))
    anchor = rules.check_anchor(body.get("anchor"), None)
    texts = [_guarded(t, "post", context={"anchor": anchor, "thread_index": i}) for i, t in enumerate(texts)]
    for text in texts:
        rules.check_length(text)
    live = not hand.dry_run()
    conn = _db()
    rules.check_attachments(conn, media_ids, gif_id, live=live)
    with hand.lock("posting"):
        rules.check_gap(conn)
        rules.check_daily_cap(conn)
        rules.check_repeat(conn, "\n".join(texts))
        rules.check_repeat(conn, texts[0])
        rules.check_link_budget(conn, "\n".join(texts))
        if not live:
            return {"ok": True, "dry_run": True, "would_post": texts, "kind": "thread", "posts": len(texts)}
        # The ledger's one-time migration happens before anything is sent.
        rules.add_tag_columns(conn)
        x = x or twitr.client()
        if gif_id:
            media_ids = [media.attach_gif(x, gif_id)]
        ids: list[str] = []
        for index, text in enumerate(texts):
            try:
                post_id = x.create_post(text, reply_to=ids[-1] if ids else None, media_ids=media_ids if not ids else ())
            except XError as error:
                stopped = XError(
                    f"thread stopped: post {index + 1} of {len(texts)} was not sent ({error}); "
                    f"{len(ids)} went out and stay up, nothing is retried or deleted",
                    error.status,
                )
                stopped.detail.update(
                    sent=len(ids), of=len(texts), ids=ids, failed_at=index + 1, in_doubt=_in_doubt(error),
                    **({"head_id": ids[0]} if ids else {}),
                )
                raise stopped from error
            # The post is out. A ledger that cannot record it is logged, not
            # raised: an error here would invite a retry that posts it twice.
            try:
                if ids:
                    rules.record_post(conn, post_id, "thread", text, in_reply_to=ids[-1], thread_of=ids[0])
                else:
                    rules.record_post(conn, post_id, "post", text, media=media_ids, anchor=anchor, **tagged, **made)
            except Exception as exc:  # noqa: BLE001
                print(f"hands/x: published {post_id} but could not record it: {type(exc).__name__}", file=sys.stderr)
            ids.append(post_id)
    return {"id": ids[0], "kind": "thread", "sent": len(ids), "ids": ids, "posts": texts}


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
    body, tagged = tags.split(body)
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
    conn = open_db(rules.SCHEMA + mentions.SCHEMA)
    rules.check_engaged_first(conn, target, account, lambda post_id: _author_of(reads, post_id))
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
            ("ledger", lambda: rules.record_post(conn, reply_id, "reply", text, in_reply_to=to, media=media_ids, **tagged)),
        ):
            try:
                record()
            except Exception as exc:  # noqa: BLE001
                print(f"hands/x: replied {reply_id} but could not record the {step}: {type(exc).__name__}", file=sys.stderr)
    return {"id": reply_id, "in_reply_to": to, "kind": "reply", "text": text}


def follow_up(to: str, body: Any, x: XApi | None = None) -> Json:
    """Continues one of the account's own posts as a thread: a reply to `to`,
    which must be in the own-post ledger (`x_posts`), so it never answers
    anyone else and reads nothing from X. The rules of `reply` otherwise
    hold: the guard, 280, one reply per post (so a thread grows one step per
    call, each on its newest post), the pacing, links, and no repeat of what
    Teeny already said. Not an own post: no gap, no daily cap. hands/giveaway
    threads a round's judge pick, countdown and winners under its post."""
    if not rules.is_x_id(to):
        raise hand.HandError("--to must be a numeric X post id")
    body, tagged = tags.split(body)
    text, media_ids, gif_id, _ = _draft(body, quote=False)
    conn = _db()
    rules.add_tag_columns(conn)
    own = conn.execute("SELECT id FROM x_posts WHERE id = ?", (to,)).fetchone()
    if own is None:
        raise hand.Refused(
            f"post {to} is not one of the account's own recorded posts; follow-up only continues those, nothing was published",
            rule="not_own",
        )
    me = (hand.setting("X_BOT_USERNAME", "teenydroid") or "teenydroid").lstrip("@")
    text = _guarded(text, "reply", replying_to=me, context={"post_id": to, "thread": True})
    rules.check_length(text)
    live = not hand.dry_run()
    rules.check_attachments(conn, media_ids, gif_id, live=live)
    with hand.lock("replying"):
        rules.check_not_replied(conn, to)
        rules.check_pacing(conn)
        rules.check_repeat(conn, text)
        rules.check_link_budget(conn, text)
        if not live:
            return _dry(text, "reply", in_reply_to=to)
        x = x or twitr.client()
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
        for step, record in (
            ("reply id", lambda: rules.record_reply_id(conn, to, reply_id)),
            ("pacing", lambda: rules.advance_pacing(conn, random.random())),
            ("ledger", lambda: rules.record_post(conn, reply_id, "reply", text, in_reply_to=to, media=media_ids, **tagged)),
        ):
            try:
                record()
            except Exception as exc:  # noqa: BLE001
                print(f"hands/x: followed up {reply_id} but could not record the {step}: {type(exc).__name__}", file=sys.stderr)
    return {"id": reply_id, "in_reply_to": to, "kind": "reply", "text": text}


#: The switch for `like`, off unless set. X's developer guidelines
#: (https://docs.x.com/developer-guidelines, reviewed 2026-11-12): "Likes must
#: be directly initiated by the authenticated user"; automated liking "by
#: keyword, hashtag, user, or schedule" is prohibited. Every caller of this
#: verb is a model (bin/act's `like` output, the mind's `like` intent), so
#: none of its likes is user-initiated (docs/roadmap.md, P1).
LIKES_FLAG = "X_LIKES"


def like(post_id: str, x: XApi | None = None) -> Json:
    """Likes a post as the account. Refused (`likes_off`) unless X_LIKES is
    on, under DRY_RUN too, so a rehearsal shows what the live run would do.
    Nothing is said, so there is no guard; under DRY_RUN nothing reaches X,
    not even "who am I". A like that went out is written to `x_likes`."""
    if not rules.is_x_id(post_id):
        raise hand.HandError("the post to like must be a numeric X post id")
    if not hand.flag(LIKES_FLAG, False):
        raise hand.Refused(
            "likes are off: X's developer guidelines prohibit automated likes (a like must be initiated by the "
            "person who owns the account), and every like here is chosen by a model; nothing was liked",
            rule="likes_off",
        )
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
