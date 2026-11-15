"""The only way hands/reddit writes. Every action runs, in this one
invocation: the body's shape, the guard on every word it would publish
(`check_fields`, surface reddit), the rules (`rules`), what Reddit says of
the community or the thread, DRY_RUN, Reddit, the ledger.

The comment rules, on top of the caps and the allowlist in `rules`:
- never twice on one post, except to answer a reply to Teeny (a comment
  whose parent is Teeny's comment, or a top-level comment on Teeny's own
  post), and never twice to the same comment;
- never a top-level comment on Teeny's own post: there it answers;
- never on a locked, archived or NSFW post, and never to its own comment;
- never an unasked comment (anything but an answer to a reply to Teeny) on a
  post older than `REDDIT_MAX_COMMENT_AGE_HOURS` (`too_old`).

A post names its `anchor`: the real, current thing it is about (a receipt id
from `hands/moltbook material`, a PR number, a `listen` item). It is kept in
the ledger, never published. Both verbs take the lab tags (`format`,
`experiment`; teeny_hands.tags), kept beside the write.

A back-off (rules, `reddit_backoffs`) is checked before anything is sent,
and started here when Reddit answers a write with 403 or the community
check finds Teeny banned (never under DRY_RUN).

Under DRY_RUN nothing is written anywhere: Reddit or the ledger. The answer
says what would have happened (`would_*`), after every rule ran. Reads a
rule needs (the post, the parent comment, the community) still happen.

The content's id is recorded the moment Reddit returns it. A ledger write
that fails after a write is logged, never raised: an error then would
invite a retry of something already done.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
import sys
from typing import Any, Mapping

from .. import hand, tags
from ..rest import RestError
from . import client, reads, rules

try:
    # Imported by name, so a test can stand in for the other hand's gate.
    from ..guard.gate import check_fields
except ImportError:  # the guard hand is not installed: nothing is published

    def check_fields(fields: Mapping[str, str], **_: Any) -> Any:
        raise hand.HandError("the content guard (hands/guard) is not installed, so nothing can be published")


Json = dict[str, Any]
_FLAIR = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_MINUTES = re.compile(r"(\d+)\s*(minute|second)", re.IGNORECASE)
ANCHOR_CHARS = 200


# --- the body ---------------------------------------------------------------------------------


def _body(body: Any, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise hand.HandError(f"stdin must be a JSON object with {', '.join(required)}")
    unknown = sorted(set(body) - {*required, *optional})
    if unknown:
        raise hand.HandError(f"unknown field(s) in the body: {', '.join(unknown)}")
    for key in required:
        if key not in body:
            raise hand.HandError(f'"{key}" is required')
    return dict(body)


def _string(body: Mapping[str, Any], key: str, required: bool = True) -> str | None:
    value = body.get(key)
    if value in (None, "") and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise hand.HandError(f'"{key}" must be a non-empty string')
    return value


# --- the guard ----------------------------------------------------------------------------------


def guarded(fields: Mapping[str, str], kind: str, context: Mapping[str, Any]) -> dict[str, str]:
    """The guard's say on every field; the fields to publish when it allows."""
    verdict = check_fields(dict(fields), surface="reddit", kind=kind, context=dict(context))
    if verdict.kind == "allow" and isinstance(verdict.fields, dict) and set(verdict.fields) == set(fields):
        return dict(verdict.fields)
    if verdict.kind == "hold":
        raise hand.Refused("held", category=verdict.category, hold_id=verdict.hold_id, field=verdict.field)
    if verdict.kind == "refuse":
        raise hand.Refused(verdict.reason, rewrite=True, field=verdict.field)
    raise hand.HandError(f"the guard answered {verdict.kind!r}, which hands/reddit does not publish on")


def _link_allowed(url: str) -> None:
    """A link post's URL is published too: it must be on the guard's link allowlist."""
    from ..guard import gate, links

    if len(url) > 2000:
        raise hand.Refused("the url is too long", rule="link")
    reason = links.judge_link(links.FoundLink(url, links.parse_url(url)), gate.allowlist())
    if reason is not None:
        raise hand.Refused(f"the url is not on the link allowlist ({reason})", rule="link", rewrite=True)


# --- Reddit's answers --------------------------------------------------------------------------------


def _safely(what: str, write: Any) -> Any:
    try:
        return write()
    except Exception as exc:  # noqa: BLE001
        print(f"hands/reddit: {what} happened but could not be recorded: {type(exc).__name__}", file=sys.stderr)
        return None


def _rate_limited(wait: float | None, why: str) -> hand.Refused:
    detail: dict[str, Any] = {"rule": "rate_limit"}
    if wait is not None:
        detail["wait_seconds"] = int(wait) + 1
    return hand.Refused(f"Reddit says slow down ({why}); nothing was retried", **detail)


def _call(api: client.Reddit, path: str, form: Mapping[str, str], subreddit: str) -> Mapping[str, Any]:
    """A write, and its `json.data`. Reddit answers a refused write with 200
    and `json.errors`; each becomes a refusal naming Reddit's code. A 403
    starts a back-off on the subreddit."""
    try:
        got = api.call("POST", path, form=form)
    except RestError as error:
        if error.status == 429:
            raise _rate_limited(error.retry_after, "429") from None
        if error.status == 403:
            _safely("a back-off", lambda: rules.signal(
                subreddit, "forbidden", f"Reddit answered a write to r/{subreddit} with 403", path))
            raise hand.Refused("Reddit refused the write (403): banned, suspended, or the community is closed",
                               rule="forbidden", subreddit=subreddit) from None
        raise
    inner = got.get("json") if isinstance(got, Mapping) and isinstance(got.get("json"), Mapping) else got
    errors = inner.get("errors") if isinstance(inner, Mapping) else None
    if isinstance(errors, list) and errors:
        first = errors[0] if isinstance(errors[0], list) else [str(errors[0])]
        code = str(first[0]) if first else "ERROR"
        message = str(first[1])[:200] if len(first) > 1 else code
        if code == "RATELIMIT":
            wait = inner.get("ratelimit") if isinstance(inner.get("ratelimit"), (int, float)) else None
            if wait is None:
                found = _MINUTES.search(message)
                if found:
                    wait = int(found.group(1)) * (60 if found.group(2).lower() == "minute" else 1)
            raise _rate_limited(wait, message)
        raise hand.Refused(f"Reddit refused it: {message}", rule="reddit", code=code)
    data = inner.get("data") if isinstance(inner, Mapping) else None
    return data if isinstance(data, Mapping) else {}


# --- post ----------------------------------------------------------------------------------------------


def _community(name: str, api: client.Reddit | None) -> Mapping[str, Any]:
    # Fresh: a ban or a change of the community's rules must not wait out the cache.
    about = reads.subreddit(name, api, fresh=True)
    if about is None:
        raise hand.Refused(f"Reddit has no open community r/{name}", rule="subreddit", subreddit=name)
    if about.get("user_is_banned"):
        if not hand.dry_run():
            _safely("a back-off", lambda: rules.signal(name, "banned", f"Teeny is banned from r/{name}"))
        raise hand.Refused(f"Teeny is banned from r/{name}", rule="banned", subreddit=name)
    if about.get("over18"):
        raise hand.Refused(f"r/{name} is NSFW; Teeny stays out", rule="nsfw", subreddit=name)
    return about


def _anchor(body: Mapping[str, Any]) -> str:
    value = body.get("anchor")
    if not isinstance(value, str) or not value.strip():
        raise hand.Refused(
            'a post names its "anchor": the real, current thing it is about (a receipt id from hands/moltbook'
            " material, a PR number, a listen item); a post tied to nothing is dropped",
            rule="anchor",
        )
    value = " ".join(value.split())
    if len(value) > ANCHOR_CHARS:
        raise hand.HandError(f'"anchor" is at most {ANCHOR_CHARS} characters')
    return value


def _tagged(tagged: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in tagged.items() if v is not None}


def post(body: Any, api: client.Reddit | None = None) -> Json:
    body, tagged = tags.split(body)
    body = _body(body, ("subreddit", "title"), ("body", "url", "flair_id", "anchor"))
    anchor = _anchor(body)
    sub = client.subreddit_name(body["subreddit"])
    rules.check_subreddit(sub)
    title = _string(body, "title")
    text = _string(body, "body", required=False)
    url = _string(body, "url", required=False)
    if text and url:
        raise hand.HandError('a Reddit post is a "body" or a "url", not both')
    flair = _string(body, "flair_id", required=False)
    if flair and not _FLAIR.match(flair):
        raise hand.HandError('"flair_id" must be a flair template id')
    fields = {"title": title}
    if text:
        fields["body"] = text
    context = {"subreddit": sub, "post_kind": "post", "anchor": anchor, **_tagged(tagged),
               **({"url": url} if url else {}), **({"flair_id": flair} if flair else {})}
    fields = guarded(fields, "post", context)
    if url:
        _link_allowed(url)
    rules.check_crypto([fields["title"], fields.get("body", ""), url or ""], sub)

    about = _community(sub, api)
    kind = "link" if url else "self"
    allowed = about.get("submission_type")
    if allowed in ("self", "link") and allowed != kind:
        raise hand.Refused(f"r/{sub} takes only {allowed} posts", rule="submission_type", subreddit=sub)

    conn = rules.db()
    with contextlib.closing(conn), hand.lock("reddit-write"):
        with hand.transaction(conn):
            rules.check_backoff(conn, sub)
            rules.check_post(conn, sub)
        if hand.dry_run():
            would = {"subreddit": sub, "kind": kind, **fields, **({"url": url} if url else {}), "anchor": anchor, **_tagged(tagged)}
            return {"dry_run": True, "would_post": would}
        form = {"sr": sub, "kind": kind, "title": fields["title"], "resubmit": "false", "sendreplies": "true"}
        if url:
            form["url"] = url
        else:
            form["text"] = fields.get("body", "")
        if flair:
            form["flair_id"] = flair
        data = _call(api or client.api(), "/api/submit", form, sub)
        post_id = str(data["id"]) if data.get("id") is not None else None
        _safely(
            "a post",
            lambda: conn.execute(
                "INSERT INTO reddit_posts (post_id, at, subreddit, title, status, anchor, format, experiment)"
                " VALUES (?, ?, ?, ?, 'posted', ?, ?, ?)",
                (post_id, hand.now(), sub, fields["title"], anchor, tagged["format"], tagged["experiment"]),
            ),
        )
        return {"posted": post_id, "subreddit": sub, "title": fields["title"], "url": data.get("url")}


# --- comment -------------------------------------------------------------------------------------------


def _check_age(the_post: Mapping[str, Any]) -> None:
    """Refuses an unasked comment on a post past REDDIT_MAX_COMMENT_AGE_HOURS:
    it lands under the rest, unread, and an old thread revived by a stranger
    reads as spam."""
    created = the_post.get("created_utc")
    if isinstance(created, bool) or not isinstance(created, (int, float)):
        return
    age = (hand.now() - float(created)) / 3600
    limit = rules.max_comment_age_hours()
    if age > limit:
        raise hand.Refused(
            f"that post is {age:.0f} hours old; Teeny comments unasked only on posts under {limit} hours old",
            rule="too_old",
            age_hours=round(age, 1),
            limit_hours=limit,
        )


def _check_comment_rules(conn: sqlite3.Connection, api: client.Reddit | None, post_id: str, parent_id: str | None) -> str:
    """The thread's rules; the post's subreddit, once they pass."""
    me = client.username().lower()
    the_post = reads.info(f"t3_{post_id}", api)
    if the_post is None:
        raise hand.Refused("Reddit has no such post", rule="unknown_post")
    sub = str(the_post.get("subreddit") or "")
    rules.check_subreddit(sub)
    if the_post.get("locked") or the_post.get("archived"):
        raise hand.Refused("the post is locked or archived", rule="locked")
    if the_post.get("over_18"):
        raise hand.Refused("the post is NSFW; Teeny stays out", rule="nsfw")
    own_post = str(the_post.get("author") or "").lower() == me or bool(
        conn.execute("SELECT 1 FROM reddit_posts WHERE post_id = ?", (post_id,)).fetchone()
    )
    mine = conn.execute(
        f"SELECT comment_id, parent_id FROM reddit_comments WHERE post_id = ? AND status IN ({','.join('?' * len(rules.COUNTED))})",
        (post_id, *rules.COUNTED),
    ).fetchall()
    if parent_id is None:
        if own_post:
            raise hand.Refused("on its own post Teeny answers the comments (parent_id), it does not add another", rule="own_post")
        if mine:
            raise hand.Refused("Teeny has already commented on this post; it answers replies, it does not comment twice", rule="once_per_post")
        _check_age(the_post)
        return sub
    if any(row["parent_id"] == parent_id for row in mine):
        raise hand.Refused("Teeny has already answered that comment", rule="once_per_parent")
    parent = reads.info(f"t1_{parent_id}", api)
    if parent is None or parent.get("link_id") != f"t3_{post_id}":
        raise hand.Refused("no such comment on this post", rule="unknown_parent")
    if str(parent.get("author") or "").lower() == me:
        raise hand.Refused("that comment is Teeny's own", rule="own_comment")
    if mine or own_post:
        grandparent = str(parent.get("parent_id") or "")
        replying_to_teeny = own_post and grandparent == f"t3_{post_id}"
        if not replying_to_teeny and grandparent.startswith("t1_"):
            gid = grandparent[3:]
            replying_to_teeny = any(row["comment_id"] == gid for row in mine)
            if not replying_to_teeny:
                above = reads.info(grandparent, api)
                replying_to_teeny = above is not None and str(above.get("author") or "").lower() == me
        if not replying_to_teeny:
            raise hand.Refused("Teeny has already commented here; it only answers a reply to Teeny", rule="once_per_post")
        return sub
    # Not an answer to Teeny: into somebody else's thread, unasked.
    _check_age(the_post)
    return sub


def comment(body: Any, api: client.Reddit | None = None) -> Json:
    body, tagged = tags.split(body)
    body = _body(body, ("post_id", "body"), ("parent_id",))
    post_id = client.base_id(body["post_id"], "post_id", "t3")
    parent_id = client.base_id(body["parent_id"], "parent_id", "t1") if body.get("parent_id") not in (None, "") else None
    context = {"post_id": post_id, "post_kind": "comment", "parent_id": parent_id, **_tagged(tagged)}
    text = guarded({"body": _string(body, "body")}, "comment", context)["body"]
    conn = rules.db()
    with contextlib.closing(conn), hand.lock("reddit-write"):
        with hand.transaction(conn):
            rules.check_account(conn)
            rules.check_comment(conn)
        sub = _check_comment_rules(conn, api, post_id, parent_id)
        rules.check_backoff(conn, sub)
        rules.check_crypto([text], sub)
        if hand.dry_run():
            would = {"post_id": post_id, "subreddit": sub, "body": text, **({"parent_id": parent_id} if parent_id else {}),
                     **_tagged(tagged)}
            return {"dry_run": True, "would_comment": would}
        thing = f"t1_{parent_id}" if parent_id else f"t3_{post_id}"
        data = _call(api or client.api(), "/api/comment", {"thing_id": thing, "text": text}, sub)
        things = data.get("things") if isinstance(data.get("things"), list) else []
        made = things[0].get("data") if things and isinstance(things[0], Mapping) and isinstance(things[0].get("data"), Mapping) else {}
        comment_id = str(made["id"]) if made.get("id") is not None else None
        _safely(
            "a comment",
            lambda: conn.execute(
                "INSERT INTO reddit_comments (comment_id, post_id, parent_id, subreddit, at, status, format, experiment)"
                " VALUES (?, ?, ?, ?, ?, 'posted', ?, ?)",
                (comment_id, post_id, parent_id, sub, hand.now(), tagged["format"], tagged["experiment"]),
            ),
        )
        out: Json = {"commented": comment_id, "post_id": post_id, "subreddit": sub}
        if parent_id:
            out["parent_id"] = parent_id
        return out


# --- subscribe ----------------------------------------------------------------------------------------


def subscribe(body: Any, api: client.Reddit | None = None) -> Json:
    body = _body(body, ("subreddit",))
    sub = client.subreddit_name(body["subreddit"])
    rules.check_subreddit(sub)
    conn = rules.db()
    with contextlib.closing(conn), hand.lock("reddit-write"):
        if conn.execute("SELECT 1 FROM reddit_actions WHERE verb = 'subscribe' AND target = ?", (sub,)).fetchone():
            raise hand.Refused(f"already subscribed to r/{sub}", rule="duplicate")
        rules.check_backoff(conn, sub)
        _community(sub, api)
        if hand.dry_run():
            return {"dry_run": True, "would_subscribe": sub}
        _call(api or client.api(), "/api/subscribe", {"action": "sub", "sr_name": sub}, sub)
        _safely(
            "a subscription",
            lambda: conn.execute("INSERT OR IGNORE INTO reddit_actions (at, verb, target) VALUES (?, 'subscribe', ?)", (hand.now(), sub)),
        )
    return {"subscribed": sub}
