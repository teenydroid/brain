"""The only way hands/moltbook writes. Every action runs, in this one
invocation: the body's shape, the guard on every word it would publish
(`check_fields`, surface moltbook), the rules (`rules`), DRY_RUN, Moltbook,
the ledger, and Moltbook's verification challenge.

The comment rules, on top of the limits in `rules`:
- never twice on one post, except to answer a reply to Teeny (a comment
  whose parent is Teeny's comment, or a comment on Teeny's own post), and
  never twice to the same comment;
- never on Teeny's own post except as its first comment, when nobody else
  has commented yet;
- never under a comment already at Moltbook's drawing depth (rules.MAX_REPLY_DEPTH):
  a reply there is not shown to anyone.

A comment or upvote naming a shortened id (a UUID's first characters, as
Teeny's memory writes them) is refused before anything is sent (`short_id`).

Under DRY_RUN nothing is written anywhere: Moltbook, the ledger or the
lockout counter. The answer says what would have happened (`would_*`), after
every rule ran. Reads a rule needs (the post, its comments, a submolt's
crypto setting) still happen.

The content's id is recorded the moment Moltbook returns it, before its
challenge is answered, so a crash between the two never forgets a post. A
ledger write that fails after a write is logged, never raised: an error then
would invite a retry of something already done.
"""

from __future__ import annotations

import re
import sqlite3
import string
import sys
from typing import Any, Mapping

from .. import hand
from ..rest import RestApi, RestError, quote
from . import challenge, client, model_read, reads, rules

try:
    # Imported by name, so a test can stand in for the other hand's gate.
    from ..guard.gate import check_fields
except ImportError:  # the guard hand is not installed: nothing is published

    def check_fields(fields: Mapping[str, str], **_: Any) -> Any:
        raise hand.HandError("the content guard (hands/guard) is not installed, so nothing can be published")


POST_TYPES = ("text", "link", "image")
Json = dict[str, Any]


# --- the body ---------------------------------------------------------------------------------


def _body(body: Any, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise hand.HandError(f"stdin must be a JSON object with {', '.join(required) or ', '.join(optional)}")
    unknown = sorted(set(body) - {*required, *optional})
    if unknown:
        raise hand.HandError(f"unknown field(s) in the body: {', '.join(unknown)}")
    for key in required:
        if key not in body:
            raise hand.HandError(f'"{key}" is required')
    return dict(body)


def _string(body: Mapping[str, Any], key: str, required: bool = True) -> str | None:
    value = body.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        if not required and value == "":
            return None
        raise hand.HandError(f'"{key}" must be a non-empty string')
    return value


#: Moltbook's ids are UUIDs. A leading slice of one (Teeny's memory and
#: journal shorten them, "post 8e2395c9") names nothing Moltbook knows, and it
#: answers a bare 400 the mind cannot learn from: refused here, by name.
_UUID_SHAPE = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"


def _full_id(value: str, what: str) -> str:
    """The id, unless it is a UUID cut short (8 or more of its first
    characters, not all 36)."""
    short = 8 <= len(value) < len(_UUID_SHAPE) and all(
        c == "-" if s == "-" else c in string.hexdigits for c, s in zip(value, _UUID_SHAPE)
    )
    if short:
        raise hand.Refused(
            f'"{what}" {value} is a shortened id: Moltbook ids are whole UUIDs (36 characters, four dashes)'
            " and it does not know this one. Use the full id from the batch, not a short one from memory",
            rule="short_id", rewrite=True, field=what,
        )
    return value


# --- the guard ----------------------------------------------------------------------------------


def guarded(fields: Mapping[str, str], kind: str, context: Mapping[str, Any]) -> dict[str, str]:
    """The guard's say on every field; the fields to publish when it allows."""
    verdict = check_fields(dict(fields), surface="moltbook", kind=kind, context=dict(context))
    if verdict.kind == "allow" and isinstance(verdict.fields, dict) and set(verdict.fields) == set(fields):
        return dict(verdict.fields)
    if verdict.kind == "hold":
        # judge_failed: the judge could not judge it, so the tick sends it again
        # next beat (bin/lib/tick.py, RETRIED); a hold it read is never sent again.
        raise hand.Refused("held", category=verdict.category, hold_id=verdict.hold_id, field=verdict.field,
                           **({"judge_failed": True} if verdict.judge_failed else {}))
    if verdict.kind == "refuse":
        raise hand.Refused(verdict.reason, rewrite=True, field=verdict.field)
    raise hand.HandError(f"the guard answered {verdict.kind!r}, which hands/moltbook does not publish on")


_FORMAT = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _format_tag(value: Any) -> str | None:
    """The post's format tag (confession, claim, news, question ...), for the
    lab to compare. Optional; a malformed one is an error, not a guess."""
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not _FORMAT.match(value.strip().lower()):
        raise hand.HandError('"format" is a short lowercase tag (letters, digits, - and _)')
    return value.strip().lower()


def _experiment(value: Any) -> None:
    """An experiment id may ride along on a post; this hand accepts and ignores
    it (the lab ledger is a separate hand)."""
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, str))):
        raise hand.HandError('"experiment" is an experiment id')


def _link_allowed(url: str) -> None:
    """A link post's URL is published too: it must be on the guard's link allowlist."""
    from ..guard import gate, links

    if len(url) > 2000:
        raise hand.Refused("the url is too long", rule="link")
    reason = links.judge_link(links.FoundLink(url, links.parse_url(url)), gate.allowlist())
    if reason is not None:
        raise hand.Refused(f"the url is not on the link allowlist ({reason})", rule="link", rewrite=True)


# --- the ledger -------------------------------------------------------------------------------------


def _safely(what: str, write: Any) -> Any:
    try:
        return write()
    except Exception as exc:  # noqa: BLE001
        print(f"hands/moltbook: {what} happened but could not be recorded: {type(exc).__name__}", file=sys.stderr)
        return None


def _insert(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int | None:
    return _safely("a write", lambda: conn.execute(sql, params).lastrowid)


def _rate_limited(error: RestError) -> hand.Refused:
    wait = int(error.retry_after) + 1 if error.retry_after is not None else None
    detail: dict[str, Any] = {"rule": "rate_limit"}
    if wait is not None:
        detail["wait_seconds"] = wait
    return hand.Refused("Moltbook says slow down (429); nothing was retried", **detail)


def _content_id(got: Any, kind: str) -> str | None:
    if not isinstance(got, Mapping):
        return None
    inner = got.get(kind) if isinstance(got.get(kind), Mapping) else got.get("data") if isinstance(got.get("data"), Mapping) else got
    value = inner.get("id") if isinstance(inner, Mapping) else None
    return str(value) if value is not None else None


def _verification(got: Any, kind: str) -> dict[str, Any] | None:
    """The challenge a write came back with, wherever Moltbook put it."""
    if not isinstance(got, Mapping):
        return None
    for holder in (got.get(kind), got.get("data"), got):
        if isinstance(holder, Mapping) and isinstance(holder.get("verification"), Mapping):
            ver = holder["verification"]
            if isinstance(ver.get("verification_code"), str) and isinstance(ver.get("challenge_text"), str):
                return dict(ver)
    return None


# --- the challenge ------------------------------------------------------------------------------------


def _answer(api: RestApi, code: str, answer: str) -> tuple[bool, str]:
    """Submits one answer. (correct, why): a refused or expired answer is
    False; a 429 raises the refusal, since nothing was judged."""
    try:
        got = api.call("POST", "/verify", body={"verification_code": code, "answer": answer})
    except RestError as error:
        if error.status == 429:
            raise _rate_limited(error) from None
        if error.status == 410:
            return False, "expired"
        if error.status == 409:
            return True, "already used"
        return False, str(error)
    if isinstance(got, Mapping) and got.get("success") is False:
        return False, str(got.get("error") or "incorrect")
    return True, "verified"


def _settle(conn: sqlite3.Connection, api: RestApi, kind: str, table: str, row_id: int | None, content_id: str | None, ver: dict[str, Any]) -> dict[str, Any]:
    """Answers a challenge when it reads with confidence (the parser, or else
    two agreeing model reads, model_read.py); otherwise leaves it pending for
    the operator and refuses (rule `verification`)."""
    code, text = ver["verification_code"], ver["challenge_text"]

    def status(value: str) -> None:
        if row_id is not None:
            _safely("a status", lambda: conn.execute(f"UPDATE {table} SET status = ? WHERE id = ?", (value, row_id)))

    def pending(value: str, note: str) -> int | None:
        return _insert(
            conn,
            "INSERT INTO moltbook_pending (at, kind, content_id, verification_code, challenge_text, expires_at, status, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (hand.now(), kind, content_id, code, text[:2000], ver.get("expires_at"), value, note),
        )

    answer = challenge.solve(text) or model_read.solve(text)
    if answer is None:
        status("needs_verification")
        pending_id = pending("needs_verification", "not read with confidence; nothing was guessed")
        # Left unanswered it expires, which Moltbook counts as a failure.
        _safely("the lockout count", lambda: rules.set_failures(conn, rules.failures(conn) + 1))
        raise hand.Refused(
            "Moltbook set a verification challenge this hand could not read with confidence, so it guessed nothing."
            f" The {kind} is hidden until the operator answers it (hands/moltbook verify {pending_id} --answer N)"
            " within 5 minutes.",
            rule="verification",
            kind=kind,
            id=content_id,
            pending_id=pending_id,
            challenge=text[:500],
        )
    try:
        correct, why = _answer(api, code, answer)
    except hand.Refused:
        status("needs_verification")
        pending("needs_verification", f"rate limited before the answer {answer} was sent")
        raise
    if correct:
        status("verified")
        _safely("the lockout count", lambda: rules.set_failures(conn, 0))
        return {"verification": "verified", "answer": answer}
    status("verification_failed")
    pending("failed", f"answered {answer}: {why}")
    _safely("the lockout count", lambda: rules.set_failures(conn, rules.failures(conn) + 1))
    count = _safely("the lockout count", lambda: rules.failures(conn)) or 0
    raise hand.Refused(
        f"Moltbook did not accept the challenge answer ({why}); the {kind} stays hidden",
        rule="verification_failed",
        kind=kind,
        id=content_id,
        consecutive_failures=count,
    )


# --- post ----------------------------------------------------------------------------------------------


def post(body: Any, api: RestApi | None = None) -> Json:
    body = _body(body, ("submolt", "title"), ("body", "url", "type", "format", "experiment"))
    post_format = _format_tag(body.get("format"))
    _experiment(body.get("experiment"))
    submolt = client.submolt_name(body["submolt"])
    title = _string(body, "title")
    text = _string(body, "body", required=False)
    url = _string(body, "url", required=False)
    kind = body.get("type") or ("link" if url and not text else "text")
    if kind not in POST_TYPES:
        raise hand.HandError(f'"type" is one of {", ".join(POST_TYPES)}')
    if kind == "link" and not url:
        raise hand.HandError('a link post needs a "url"')
    fields = {"title": title}
    if text:
        fields["body"] = text
    fields = guarded(fields, "post", {"submolt": submolt})
    if url:
        _link_allowed(url)
    published = [fields["title"], fields.get("body", ""), url or ""]

    conn = rules.db()
    rules.check_collab(conn, "\n".join(published))
    if rules.looks_crypto(published):
        info = reads.submolt(submolt, api)
        if info is None:
            raise hand.HandError(f"Moltbook has no submolt m/{submolt}")
        rules.check_crypto(info, submolt)

    with hand.lock("moltbook-write"):
        with hand.transaction(conn):
            mode = rules.check_post(conn)
        if hand.dry_run():
            return {"dry_run": True, "would_post": {"submolt": submolt, "type": kind, **fields, **({"url": url} if url else {}),
                                                         **({"format": post_format} if post_format else {})}, **mode}
        api = api or client.api()
        payload: dict[str, Any] = {"submolt_name": submolt, "title": fields["title"], "type": kind}
        if "body" in fields:
            payload["content"] = fields["body"]
        if url:
            payload["url"] = url
        try:
            got = api.call("POST", "/posts", body=payload)
        except RestError as error:
            if error.status == 429:
                raise _rate_limited(error) from None
            raise
        post_id = _content_id(got, "post")
        ver = _verification(got, "post")
        row_id = _insert(
            conn,
            "INSERT INTO moltbook_posts (post_id, at, submolt, title, status, format) VALUES (?, ?, ?, ?, ?, ?)",
            (post_id, hand.now(), submolt, fields["title"], "created" if ver else "posted", post_format),
        )
        out: Json = {"posted": post_id, "submolt": submolt, "title": fields["title"]}
        if ver:
            out.update(_settle(conn, api, "post", "moltbook_posts", row_id, post_id, ver))
        return out


# --- comment -------------------------------------------------------------------------------------------


def _walk(nodes: Any, parent: str | None = None):
    """Every comment in Moltbook's tree, with its parent's id."""
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        yield node, parent
        yield from _walk(node.get("replies"), str(node.get("id")) if node.get("id") is not None else parent)


def _depths(nodes: Any, depth: int = 1, out: dict[str, int] | None = None) -> dict[str, int]:
    """Each comment's id -> its depth in Moltbook's tree (a top-level one is 1)."""
    out = {} if out is None else out
    if isinstance(nodes, list):
        for node in nodes:
            if isinstance(node, Mapping):
                if node.get("id") is not None:
                    out[str(node["id"])] = depth
                _depths(node.get("replies"), depth + 1, out)
    return out


def _author(node: Mapping[str, Any]) -> str | None:
    author = node.get("author")
    if isinstance(author, Mapping):
        author = author.get("name")
    if author is None:
        author = node.get("author_name")
    return author if isinstance(author, str) else None


def _check_comment_rules(conn: sqlite3.Connection, api: RestApi | None, post_id: str, parent_id: str | None) -> None:
    me = reads.me_name(api).lower()
    mine = conn.execute(
        f"SELECT comment_id, parent_id FROM moltbook_comments WHERE post_id = ? AND status IN ({','.join('?' * len(rules.COUNTED))})",
        (post_id, *rules.COUNTED),
    ).fetchall()
    the_post = reads.post(post_id, api).get("post")
    own_post = bool(conn.execute("SELECT 1 FROM moltbook_posts WHERE post_id = ?", (post_id,)).fetchone())
    if not own_post and isinstance(the_post, Mapping):
        own_post = (_author(the_post) or "").lower() == me

    tree = None
    depths: dict[str, int] = {}
    if own_post or parent_id is not None:
        nodes = reads.comments(post_id, "new", 100, api, fresh=True).get("comments")
        tree = list(_walk(nodes))
        depths = _depths(nodes)
    if parent_id is None:
        if mine:
            raise hand.Refused("Teeny has already commented on this post; it answers replies, it does not comment twice", rule="once_per_post")
        if own_post and tree:
            raise hand.Refused("on its own post Teeny answers the comments (parent_id), it does not add another", rule="own_post")
        return
    if any(row["parent_id"] == parent_id for row in mine):
        raise hand.Refused("Teeny has already answered that comment", rule="once_per_parent")
    found = next(((node, parent) for node, parent in (tree or []) if str(node.get("id")) == parent_id), None)
    if found is None:
        raise hand.Refused("no such comment on this post (or not in its newest 100)", rule="unknown_parent")
    node, grandparent = found
    if depths.get(parent_id, 1) >= rules.MAX_REPLY_DEPTH:
        raise hand.Refused(
            f"that comment is {depths.get(parent_id)} levels deep; Moltbook draws a thread only {rules.MAX_REPLY_DEPTH}"
            " levels down, so a reply under it would never be seen",
            rule="too_deep",
            depth=depths.get(parent_id),
        )
    if (_author(node) or "").lower() == me:
        raise hand.Refused("that comment is Teeny's own", rule="own_comment")
    if mine or own_post:
        mine_ids = {row["comment_id"] for row in mine} | {
            str(n.get("id")) for n, _ in (tree or []) if (_author(n) or "").lower() == me
        }
        replying_to_teeny = (grandparent is not None and grandparent in mine_ids) or (own_post and grandparent is None)
        if not replying_to_teeny:
            raise hand.Refused(
                "Teeny has already commented here; it only answers a reply to Teeny", rule="once_per_post"
            )


def comment(body: Any, api: RestApi | None = None) -> Json:
    body = _body(body, ("post_id", "body"), ("parent_id",))
    post_id = _full_id(client.object_id(body["post_id"], "post_id"), "post_id")
    parent_id = _full_id(client.object_id(body["parent_id"], "parent_id"), "parent_id") if body.get("parent_id") is not None else None
    text = guarded({"body": _string(body, "body")}, "comment", {"post_id": post_id})["body"]
    conn = rules.db()
    with hand.lock("moltbook-write"):
        with hand.transaction(conn):
            mode = rules.check_comment(conn)
        _check_comment_rules(conn, api, post_id, parent_id)
        if rules.looks_crypto([text]):
            the_post = reads.post(post_id, api).get("post")
            sub = the_post.get("submolt") if isinstance(the_post, Mapping) else None
            name = sub.get("name") if isinstance(sub, Mapping) else sub if isinstance(sub, str) else None
            if not name and isinstance(the_post, Mapping):
                name = the_post.get("submolt_name")
            if not isinstance(name, str):
                raise hand.Refused("cannot tell which submolt the post is in, so crypto content is not sent", rule="crypto")
            name = client.submolt_name(name)
            rules.check_crypto(reads.submolt(name, api), name)
        if hand.dry_run():
            would = {"post_id": post_id, "body": text, **({"parent_id": parent_id} if parent_id else {})}
            return {"dry_run": True, "would_comment": would, **mode}
        api = api or client.api()
        payload: dict[str, Any] = {"content": text}
        if parent_id:
            payload["parent_id"] = parent_id
        try:
            got = api.call("POST", f"/posts/{quote(post_id)}/comments", body=payload)
        except RestError as error:
            if error.status == 429:
                raise _rate_limited(error) from None
            raise
        comment_id = _content_id(got, "comment")
        ver = _verification(got, "comment")
        row_id = _insert(
            conn,
            "INSERT INTO moltbook_comments (comment_id, post_id, parent_id, at, status) VALUES (?, ?, ?, ?, ?)",
            (comment_id, post_id, parent_id, hand.now(), "created" if ver else "posted"),
        )
        out: Json = {"commented": comment_id, "post_id": post_id}
        if parent_id:
            out["parent_id"] = parent_id
        if ver:
            out.update(_settle(conn, api, "comment", "moltbook_comments", row_id, comment_id, ver))
        return out


# --- upvote, follow, subscribe -------------------------------------------------------------------------


def _simple(verb: str, target: str, path: str, api: RestApi | None, own_check: Any = None, method: str = "POST") -> bool:
    """An upvote, a follow or a subscribe: the write rate, never twice, an
    optional rule, DRY_RUN, Moltbook, the ledger. True when it was a dry run."""
    conn = rules.db()
    with hand.lock("moltbook-write"):
        with hand.transaction(conn):
            rules.check_write_rate(conn)
            if conn.execute("SELECT 1 FROM moltbook_actions WHERE verb = ? AND target = ?", (verb, target)).fetchone():
                raise hand.Refused(f"already did {verb} {target}", rule="duplicate")
            if own_check is not None:
                own_check(conn)
        if hand.dry_run():
            return True
        api = api or client.api()
        try:
            api.call(method, path, body={} if method == "POST" else None)
        except RestError as error:
            if error.status == 429:
                raise _rate_limited(error) from None
            raise
        _insert(conn, "INSERT OR IGNORE INTO moltbook_actions (at, verb, target) VALUES (?, ?, ?)", (hand.now(), verb, target))
    return False


def upvote(body: Any, api: RestApi | None = None) -> Json:
    body = _body(body, (), ("post_id", "comment_id"))
    if ("post_id" in body) == ("comment_id" in body):
        raise hand.HandError('an upvote names exactly one of "post_id" or "comment_id"')
    key = "post_id" if "post_id" in body else "comment_id"
    target = _full_id(client.object_id(body[key], key), key)
    table, column, noun = (
        ("moltbook_posts", "post_id", "post") if key == "post_id" else ("moltbook_comments", "comment_id", "comment")
    )

    def not_own(conn: sqlite3.Connection) -> None:
        if conn.execute(f"SELECT 1 FROM {table} WHERE {column} = ?", (target,)).fetchone():
            raise hand.Refused(f"Teeny does not upvote its own {noun}", rule=f"own_{noun}")

    path = f"/posts/{quote(target)}/upvote" if noun == "post" else f"/comments/{quote(target)}/upvote"
    if _simple(f"upvote-{noun}", target, path, api, not_own):
        return {"dry_run": True, "would_upvote": {key: target}}
    return {"upvoted": {key: target}}


def follow(body: Any, api: RestApi | None = None) -> Json:
    body = _body(body, ("name",))
    name = client.agent_name(body["name"])
    if name.lower() == reads.me_name(api).lower():
        raise hand.Refused("Teeny does not follow itself", rule="own")
    target = name.lower()
    if _simple("follow", target, f"/agents/{quote(name)}/follow", api, lambda conn: rules.check_follow(conn, target)):
        return {"dry_run": True, "would_follow": name}
    return {"followed": name}


def unfollow(body: Any, api: RestApi | None = None) -> Json:
    """Takes an agent off Teeny's follow list (DELETE /agents/NAME/follow).
    Once each; the ledger remembers it, so `follow` never takes it back."""
    body = _body(body, ("name",))
    name = client.agent_name(body["name"])
    if _simple("unfollow", name.lower(), f"/agents/{quote(name)}/follow", api, method="DELETE"):
        return {"dry_run": True, "would_unfollow": name}
    return {"unfollowed": name}


#: How a profile says the agent follows Teeny back, by the keys tried. A
#: profile with none of them answers None (unknown), and unknown is never pruned.
_FOLLOWS_BACK_KEYS = ("follows_you", "follows_me", "follows_back", "is_followed_by", "followed_by_you", "is_following_you")


def follows_back(profile: Any) -> bool | None:
    agent = profile.get("agent") if isinstance(profile, Mapping) else None
    for holder in (agent, profile):
        if not isinstance(holder, Mapping):
            continue
        for key in _FOLLOWS_BACK_KEYS:
            if isinstance(holder.get(key), bool):
                return holder[key]
    return None


def stale_follows(days: int = 7, limit: int = 25, api: RestApi | None = None) -> Json:
    """Agents Teeny followed at least `days` ago, not unfollowed since, with
    whether each follows back (from its profile; None when the profile does not
    say). Oldest follow first, at most `limit` profiles read."""
    conn = rules.db()
    try:
        rows = conn.execute(
            "SELECT f.target, f.at FROM moltbook_actions f WHERE f.verb = 'follow' AND f.at <= ?"
            " AND NOT EXISTS (SELECT 1 FROM moltbook_actions u WHERE u.verb = 'unfollow' AND u.target = f.target)"
            " ORDER BY f.at LIMIT ?",
            (hand.now() - days * rules.DAY, limit),
        ).fetchall()
    finally:
        conn.close()
    out: list[Json] = []
    errors: list[str] = []
    for row in rows:
        try:
            back = follows_back(reads.profile(row["target"], api))
        except hand.HandError as exc:
            errors.append(f"{row['target']}: {exc}")
            continue
        out.append({"name": row["target"], "followed_days_ago": int((hand.now() - row["at"]) // rules.DAY), "follows_back": back})
    got: Json = {"days": days, "follows": out}
    if errors:
        got["errors"] = errors
    return got


def prune(days: int = 7, limit: int = 25, api: RestApi | None = None) -> Json:
    """The weekly unfollow: every agent followed at least `days` ago that its
    profile says does not follow back. One whose profile does not say stays."""
    found = stale_follows(days, limit, api)
    unfollowed: list[str] = []
    refused: list[Json] = []
    kept = 0
    for item in found["follows"]:
        if item["follows_back"] is not False:
            kept += 1
            continue
        try:
            unfollow({"name": item["name"]}, api)
            unfollowed.append(item["name"])
        except hand.Refused as exc:
            refused.append({"name": item["name"], "refused": exc.reason})
            if exc.detail.get("rule") == "rate_limit":
                break
    out: Json = {"days": days, "checked": len(found["follows"]), "kept": kept}
    out["would_unfollow" if hand.dry_run() else "unfollowed"] = unfollowed
    if refused:
        out["refused"] = refused
    if found.get("errors"):
        out["errors"] = found["errors"]
    return out


def subscribe(body: Any, api: RestApi | None = None) -> Json:
    body = _body(body, ("submolt",))
    name = client.submolt_name(body["submolt"])
    if _simple("subscribe", name, f"/submolts/{quote(name)}/subscribe", api):
        return {"dry_run": True, "would_subscribe": name}
    return {"subscribed": name}


# --- operator verbs -----------------------------------------------------------------------------------------

_NAME = re.compile(r"^[A-Za-z0-9_-]{2,40}$")


def register(name: str, description: str, api: RestApi | None = None) -> Json:
    """Registers a new agent. Prints its key once, for the operator to put in
    secrets/teeny.env; the key is never written anywhere by this hand."""
    if not _NAME.match(name or ""):
        raise hand.HandError("--name is 2-40 letters, digits, _ and -")
    if not isinstance(description, str) or not description.strip():
        raise hand.HandError("--description is required")
    description = guarded({"body": description}, "profile", {"register": name})["body"]
    if hand.dry_run():
        return {"dry_run": True, "would_register": name, "description": description}
    api = api or client.api(auth=False)
    got = api.call("POST", "/agents/register", body={"name": name, "description": description}, auth=False)
    agent = got.get("agent") if isinstance(got, Mapping) and isinstance(got.get("agent"), Mapping) else {}
    if not agent.get("api_key"):
        raise hand.HandError("Moltbook did not return an API key")
    return {
        "registered": name,
        "api_key": agent.get("api_key"),
        "claim_url": agent.get("claim_url"),
        "verification_code": agent.get("verification_code"),
        "next": (
            "This key is shown once and was not saved. Put it in secrets/teeny.env as MOLTBOOK_API_KEY=<key>"
            " (mode 0600), set MOLTBOOK_AGENT_NAME and MOLTBOOK_CLAIMED_AT once claimed, then open claim_url"
            " to verify the owner's email and post the claim tweet."
        ),
    }


def describe(description: str, api: RestApi | None = None) -> Json:
    """Sets the profile text (PATCH /agents/me). Operator-run: the words go
    past the guard like any others; no model path calls it."""
    if not isinstance(description, str) or not description.strip():
        raise hand.HandError("--description is required")
    if len(description) > 1000:
        raise hand.HandError("--description is at most 1000 characters")
    description = guarded({"body": description}, "profile", {"describe": True})["body"]
    if hand.dry_run():
        return {"dry_run": True, "would_describe": description}
    api = api or client.api()
    api.call("PATCH", "/agents/me", body={"description": description})
    return {"described": description}


def pending_list(status: str | None = None) -> Json:
    conn = rules.db()
    try:
        query = "SELECT id, at, kind, content_id, challenge_text, expires_at, status, note FROM moltbook_pending"
        params: tuple[Any, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        rows = conn.execute(query + " ORDER BY id DESC LIMIT 50", params).fetchall()
        count = rules.failures(conn)
    finally:
        conn.close()
    return {"pending": [dict(r) for r in rows], "consecutive_failures": count, "lockout_after": rules.LOCKOUT_AFTER}


def verify(pending_id: int, answer: str, api: RestApi | None = None) -> Json:
    """The operator's answer to a challenge the hand left pending."""
    try:
        value = float(answer)
    except ValueError:
        raise hand.HandError("--answer must be a number") from None
    formatted = f"{value:.2f}"
    conn = rules.db()
    row = conn.execute("SELECT * FROM moltbook_pending WHERE id = ?", (pending_id,)).fetchone()
    if row is None or row["status"] != "needs_verification":
        raise hand.Refused("no challenge waiting under that id", id=pending_id)
    if hand.dry_run():
        return {"dry_run": True, "would_verify": pending_id, "answer": formatted}
    api = api or client.api()
    correct, why = _answer(api, row["verification_code"], formatted)
    table = "moltbook_posts" if row["kind"] == "post" else "moltbook_comments"
    column = "post_id" if row["kind"] == "post" else "comment_id"
    with hand.transaction(conn):
        conn.execute("UPDATE moltbook_pending SET status = ?, note = ? WHERE id = ?", ("verified" if correct else "failed", f"operator answered {formatted}: {why}", pending_id))
        if row["content_id"]:
            conn.execute(f"UPDATE {table} SET status = ? WHERE {column} = ?", ("verified" if correct else "verification_failed", row["content_id"]))
        if correct:
            rules.set_failures(conn, 0)
    if not correct:
        raise hand.Refused(f"Moltbook did not accept {formatted} ({why})", rule="verification_failed", id=pending_id)
    return {"verified": pending_id, "answer": formatted, "kind": row["kind"], "id": row["content_id"]}


def unlock() -> Json:
    """The operator clears the verification lockout."""
    conn = rules.db()
    try:
        with hand.transaction(conn):
            before = rules.failures(conn)
            rules.set_failures(conn, 0)
    finally:
        conn.close()
    return {"unlocked": True, "consecutive_failures_were": before}
