"""The dispatch queue: one exact Moltbook write the mind asked for, sent by
code (docs/hands.md, "moltbook dispatch").

Moltbook belongs to the scripts (docs/mind.md): the broker refuses the mind
every Moltbook write. What it may do is file one: a reply under a comment
(post_id and parent_id), a comment on a post (post_id alone) or a post of
its own (title, submolt), with the exact words, an expiry at most
MAX_DAYS out and a short reason. Until 2026-11-26 each such write was a pull
request appending a prose "one-off dispatch" block to a playbook, re-read by
the playbook's model on every run, pinned by a guard input of its own and
cleaned up by another pull request; about half of Teeny's pull requests in
two days were those.

`file` (the broker's `dispatch_moltbook`) checks what can be checked before
Moltbook hears of it: whole UUIDs, the expiry, the cap on pending
dispatches (MOLTBOOK_DISPATCH_MAX_PENDING, 10; 0 turns filing off), one
dispatch a target, and from this box's ledger the comment rules the hand
enforces (never twice under one parent, never under Teeny's own comment,
never a top-level comment on Teeny's own post or a second one on any post,
a post that retells one is refused), then the guard without its model judge
(the lengths, the house style, the links and the lexicon). A refusal says
why, with a `rule`. Under DRY_RUN nothing is filed.

`run` (every beat, bin/lib/tick.py, when `due`) sends what is due: oldest
filed first, at most one write a run, verbatim, through `actions.comment`
or `actions.post`, so the guard and its judge, the caps, the pacing
(COMMENT_GAP, the post gap), DRY_RUN and once_per_parent all hold as for
any other write. Before a reply goes out the run reads the thread fresh and
settles it `answered` when a teenydroid reply already sits under the parent
(the ledger is asked first, which needs no read). A dispatch past its
expiry is `expired`. The hand's answer settles the rest: `sent`;
`answered` for once_per_parent (and once_per_post on a top-level comment);
`pending` again with a `retry_at` for a pacing, cap, reserve or lockout
refusal, or for a reply Moltbook plainly rejected (a 4xx) with no copy found
or challenge recorded; `held` for a judge outage, which goes out only if the
guard's re-screen clears it (one sender); an attempt is `sending` on record
before Moltbook hears of it, so one a dead worker left is never sent again; `refused` for every other refusal, never retried (an
unreadable challenge is a void), and for anything Moltbook created in the
attempt whatever came after it, and for a send that ended without a clear
answer when a copy may exist (`unknown_result`): a write is never sent twice. Under DRY_RUN a
run says what it would send and changes nothing.

`listing` is what the mind reads (`teeny dispatches`): every pending
dispatch and those settled in the last week, with their states.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import string
from datetime import datetime, timezone
from typing import Any, Mapping

from .. import hand
from ..rest import RestError
from . import client, rules
from .dispatch_seeds import SEEDS

Json = dict[str, Any]

TABLE = "moltbook_dispatch"
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL CHECK (kind IN ('comment', 'post')),
    post_id    TEXT,
    parent_id  TEXT,
    submolt    TEXT,
    title      TEXT,
    body       TEXT NOT NULL,
    format     TEXT,
    why        TEXT NOT NULL,
    filed_at   REAL NOT NULL,
    expires_at REAL NOT NULL,
    status     TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    retry_at   REAL,
    last       TEXT,
    result     TEXT,
    settled_at REAL,
    seed       TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS {TABLE}_status ON {TABLE} (status, filed_at);
"""

MAX_DAYS = 7
DEFAULT_MAX_PENDING = 10
MAX_WHY = 300
#: How long a dispatch waits after a refusal that frees up on its own.
RETRY_SECS = {"rate_limit": 60.0, "daily_cap": 1800.0, "own_thread_reserve": 1800.0, "verification_lockout": 3600.0}
#: After a judge outage or an error on the way (Moltbook down, a read failed).
RETRY_ERROR_SECS = 600.0
#: How long a settled dispatch stays in the listing.
LISTED_DAYS = 7
LISTED_MAX = 50
FIELDS = ("post_id", "parent_id", "submolt", "title", "body", "format", "expires_at", "why")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SHAPE = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"


def db() -> sqlite3.Connection:
    conn = rules.db()
    conn.executescript(SCHEMA)
    return conn


def max_pending() -> int:
    return hand.number("MOLTBOOK_DISPATCH_MAX_PENDING", DEFAULT_MAX_PENDING, 0, 50)


def _iso(at: float | None) -> str | None:
    return None if at is None else datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _shape(why: str) -> hand.Refused:
    return hand.Refused(f"{why}. A dispatch is {{post_id, parent_id?, body, expires_at, why}} for a reply or a"
                        " comment, or {submolt?, title, body, format?, expires_at, why} for a post", rule="shape")


def uuid(value: Any, what: str) -> str:
    """A whole Moltbook id (a lowercase UUID), else refused by name."""
    if not isinstance(value, str):
        raise _shape(f'"{what}" must be a Moltbook id')
    value = value.strip().lower()
    if _UUID.match(value):
        return value
    short = 8 <= len(value) < len(_SHAPE) and all(
        c == "-" if s == "-" else c in string.hexdigits for c, s in zip(value, _SHAPE))
    if short:
        raise hand.Refused(f'"{what}" {value} is a shortened id: a Moltbook id is a whole UUID (36 characters, four'
                           " dashes), copied from a fresh listing", rule="short_id", field=what)
    raise hand.Refused(f'"{what}" {value[:60]!r} is not a Moltbook id (a UUID, 36 characters, four dashes)',
                       rule="not_uuid", field=what)


def when(value: Any) -> float:
    """An ISO 8601 moment (UTC when it names no zone) as epoch seconds."""
    if not isinstance(value, str):
        raise ValueError(value)
    moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _expiry(value: Any) -> float:
    try:
        at = when(value)
    except ValueError:
        raise hand.Refused(f'"expires_at" must be an ISO 8601 moment such as "2026-11-29T23:59:59Z", not {str(value)[:40]!r}',
                           rule="expires_at") from None
    now = hand.now()
    if at <= now:
        raise hand.Refused(f'"expires_at" {_iso(at)} has already passed', rule="expires_at")
    if at > now + MAX_DAYS * rules.DAY:
        raise hand.Refused(f'"expires_at" {_iso(at)} is more than {MAX_DAYS} days out; the latest is {_iso(now + MAX_DAYS * rules.DAY)}',
                           rule="expires_at")
    return at


def _text(body: Mapping[str, Any], key: str, required: bool = True) -> str | None:
    value = body.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _shape(f'"{key}" must be a non-empty string')
    return value


def _counted(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> sqlite3.Row | None:
    marks = ",".join("?" * len(rules.COUNTED))
    return conn.execute(sql.format(counted=marks), (*params, *rules.COUNTED)).fetchone()


def _answered(conn: sqlite3.Connection, post_id: str, parent_id: str | None) -> str | None:
    """Teeny's comment already under this parent (or, with none, on this
    post), as the ledger knows it."""
    if parent_id is not None:
        row = _counted(conn, "SELECT comment_id FROM moltbook_comments WHERE parent_id = ? AND status IN ({counted})"
                             " ORDER BY at LIMIT 1", (parent_id,))
    else:
        row = _counted(conn, "SELECT comment_id FROM moltbook_comments WHERE post_id = ? AND parent_id IS NULL"
                             " AND status IN ({counted}) ORDER BY at LIMIT 1", (post_id,))
    return None if row is None else str(row[0] or "") or "(in the ledger)"


def _check_comment(conn: sqlite3.Connection, post_id: str, parent_id: str | None) -> None:
    """The hand's comment rules, as far as this box's ledger can tell."""
    if conn.execute("SELECT 1 FROM moltbook_comments WHERE comment_id = ?", (post_id,)).fetchone():
        raise hand.Refused(f"{post_id} is Teeny's own comment, not a post", rule="comment_as_post")
    if parent_id is None:
        if conn.execute("SELECT 1 FROM moltbook_posts WHERE post_id = ?", (post_id,)).fetchone():
            raise hand.Refused("on its own post Teeny answers the comments (parent_id), it does not add another",
                               rule="own_post")
        if _answered(conn, post_id, None):
            raise hand.Refused("Teeny has already commented on this post; it answers replies, it does not comment twice",
                               rule="once_per_post")
        return
    if conn.execute("SELECT 1 FROM moltbook_comments WHERE comment_id = ?", (parent_id,)).fetchone():
        raise hand.Refused("that comment is Teeny's own: a reply's parent is the other agent's comment", rule="own_comment")
    if conn.execute("SELECT 1 FROM moltbook_posts WHERE post_id = ?", (parent_id,)).fetchone():
        raise hand.Refused(f"{parent_id} is a post, not a comment: it goes in post_id", rule="post_as_parent")
    if _answered(conn, post_id, parent_id):
        raise hand.Refused("Teeny has already answered that comment", rule="once_per_parent")


def _preview(fields: dict[str, str], kind: str, context: Json) -> None:
    """The guard without its model judge: the lengths, the house style, the
    links and the lexicon. The judge reads it when it is sent."""
    from . import actions

    verdict = actions.check_fields(fields, surface="moltbook", kind=kind, context=context, judge=False)
    if verdict.kind == "allow":
        return
    if verdict.kind == "hold":
        raise hand.Refused("held by the guard: say something else or nothing; never reword it to get past",
                           rule="guard", category=verdict.category, field=verdict.field)
    raise hand.Refused(str(verdict.reason or "the guard refused it"), rule="guard", field=verdict.field)


def _pending(conn: sqlite3.Connection) -> int:
    return int(conn.execute(f"SELECT count(*) FROM {TABLE} WHERE status IN ('pending', 'sending')").fetchone()[0])


def file(body: Any) -> Json:
    """Files one dispatch, or refuses it with the reason (module docs)."""
    if not isinstance(body, Mapping):
        raise _shape("the body must be a JSON object")
    unknown = sorted(set(body) - set(FIELDS))
    if unknown:
        raise _shape(f"unknown field(s): {', '.join(unknown)}")
    text = _text(body, "body")
    why = _text(body, "why")
    if len(why) > MAX_WHY:
        raise _shape(f'"why" is a short reason, at most {MAX_WHY} characters')
    post = body.get("title") is not None
    row: Json = {"body": text, "why": why.strip()}
    if post:
        for key in ("post_id", "parent_id"):
            if body.get(key) is not None:
                raise _shape(f'a post has a title and no "{key}"')
        from .. import tags

        try:
            row.update(kind="post", title=_text(body, "title"),
                       submolt=client.submolt_name(body.get("submolt") or "general"),
                       format=tags.split({"format": body.get("format")})[1]["format"])
        except hand.HandError as exc:
            raise _shape(str(exc)) from None
    else:
        for key in ("submolt", "format"):
            if body.get(key) is not None:
                raise _shape(f'"{key}" belongs to a post (with a title), not to a comment')
        row.update(kind="comment", post_id=uuid(body.get("post_id"), "post_id"),
                   parent_id=uuid(body["parent_id"], "parent_id") if body.get("parent_id") is not None else None)
    row["expires_at"] = _expiry(body.get("expires_at"))
    cap = max_pending()
    if not cap:
        raise hand.Refused("filing Moltbook dispatches is off (MOLTBOOK_DISPATCH_MAX_PENDING=0)", rule="off")
    conn = db()
    try:
        if _pending(conn) >= cap:
            raise hand.Refused(f"{cap} dispatches already wait to be sent, the most there may be; one goes out every"
                               " few minutes, so file this when `teeny dispatches` shows room", rule="max_pending", cap=cap)
        _duplicate(conn, row)
        if post:
            from . import retell

            retell.check(conn, row["title"], text)
            _preview({"title": row["title"], "body": text}, "post", {"submolt": row["submolt"]})
        else:
            _check_comment(conn, row["post_id"], row["parent_id"])
            _preview({"body": text}, "comment", {"post_id": row["post_id"], "parent_id": row["parent_id"]})
        if hand.dry_run():
            return {"dry_run": True, "would_file": _shown(row), "note": "rehearsal: checked, nothing was filed"}
        # The cap, the duplicate check and the insert in one write
        # transaction (BEGIN IMMEDIATE): a second filing in another process
        # waits for it, then sees this row.
        with hand.transaction(conn):
            if _pending(conn) >= cap:
                raise hand.Refused(f"{cap} dispatches already wait to be sent", rule="max_pending", cap=cap)
            _duplicate(conn, row)
            row_id = _insert(conn, row, "pending")
        return {"filed": _shown({**row, "id": row_id, "status": "pending", "filed_at": hand.now()}),
                "note": "filed: code sends it, verbatim, when it is due; `teeny dispatches` shows how it went"}
    finally:
        conn.close()


#: The states that hold a target: waiting, at work, or done with it.
LIVE = ("pending", "sending", "sent", "answered")


def _duplicate(conn: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    """One dispatch a target: a post by its title, a comment by its post and
    parent. Checked again inside the insert's transaction."""
    marks = ",".join("?" * len(LIVE))
    if row["kind"] == "post":
        same = conn.execute(f"SELECT id FROM {TABLE} WHERE kind = 'post' AND lower(title) = lower(?)"
                            f" AND status IN ({marks})", (row["title"], *LIVE)).fetchone()
        if same:
            raise hand.Refused(f"dispatch {same[0]} already carries a post with this title", rule="duplicate", id=same[0])
        return
    same = conn.execute(f"SELECT id FROM {TABLE} WHERE kind = 'comment' AND post_id = ? AND parent_id IS ?"
                        f" AND status IN ({marks})", (row["post_id"], row["parent_id"], *LIVE)).fetchone()
    if same:
        raise hand.Refused(f"dispatch {same[0]} already answers this target; one dispatch a target",
                           rule="duplicate", id=same[0])


def _insert(conn: sqlite3.Connection, row: Mapping[str, Any], status: str, seed: str | None = None,
            result: str | None = None) -> int:
    cur = conn.execute(
        f"INSERT INTO {TABLE} (kind, post_id, parent_id, submolt, title, body, format, why, filed_at, expires_at,"
        " status, result, settled_at, seed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (row["kind"], row.get("post_id"), row.get("parent_id"), row.get("submolt"), row.get("title"), row["body"],
         row.get("format"), row["why"], hand.now(), row["expires_at"], status, result,
         None if status == "pending" else hand.now(), seed),
    )
    return int(cur.lastrowid)


def _shown(row: Mapping[str, Any]) -> Json:
    out: Json = {k: row[k] for k in ("id", "kind", "status", "post_id", "parent_id", "submolt", "title", "format", "why",
                                     "attempts", "result") if row.get(k) is not None}
    body = str(row.get("body") or "")
    out["body"] = body if len(body) <= 160 else body[:157] + "..."
    for key in ("filed_at", "expires_at", "retry_at", "settled_at"):
        if row.get(key) is not None:
            out[key] = _iso(row[key])
    if row.get("last"):
        try:
            out["last"] = json.loads(row["last"])
        except ValueError:
            out["last"] = row["last"]
    return out


def listing(days: int = LISTED_DAYS) -> Json:
    """Every pending dispatch, then those settled in the last `days`, newest first."""
    conn = db()
    try:
        pending = conn.execute(f"SELECT * FROM {TABLE} WHERE status IN ('pending', 'sending') ORDER BY filed_at, id").fetchall()
        settled = conn.execute(f"SELECT * FROM {TABLE} WHERE status NOT IN ('pending', 'sending') AND settled_at > ?"
                               " ORDER BY settled_at DESC, id DESC LIMIT ?",
                               (hand.now() - days * rules.DAY, LISTED_MAX)).fetchall()
    finally:
        conn.close()
    return {"dispatches": [_shown(dict(r)) for r in [*pending, *settled]], "pending": len(pending), "cap": max_pending(),
            "note": "sent: it went out; answered: a reply of yours was already there; expired: its time ran out; "
                    "refused: the hand said no for good (`last` says why), never retried; held: the guard's judge was "
                    "down; it goes out only if the guard's re-screen clears it once the judge answers, else never; "
                    "sending: on its way this minute; pending: waiting, or until retry_at after a refusal that "
                    "frees up on its own"}


# --- the seeds --------------------------------------------------------------------------------


def _seeds_due(conn: sqlite3.Connection) -> list[Mapping[str, str]]:
    """The migrated blocks (dispatch_seeds.py) not filed yet and unexpired: a
    reply only on a post this box knows is Teeny's own, a post only on a box
    whose ledger holds posts of Teeny's own (a fresh checkout, a test, files
    nothing)."""
    if not SEEDS:
        return []
    have = {r[0] for r in conn.execute(f"SELECT seed FROM {TABLE} WHERE seed IS NOT NULL")}
    own = {r[0] for r in conn.execute("SELECT post_id FROM moltbook_posts WHERE post_id IS NOT NULL")}
    return [s for s in SEEDS if s["key"] not in have and when(s["expires_at"]) > hand.now()
            and (bool(own) if "title" in s else s["post_id"] in own)]


def _seed(conn: sqlite3.Connection) -> int:
    filed = 0
    for seed in _seeds_due(conn):
        row: Json = {"body": seed["body"], "why": seed["why"], "expires_at": when(seed["expires_at"])}
        if "title" in seed:
            row.update(kind="post", submolt=seed["submolt"], title=seed["title"], format=seed.get("format"))
            done = None
        else:
            row.update(kind="comment", post_id=seed["post_id"], parent_id=seed["parent_id"])
            done = _answered(conn, seed["post_id"], seed["parent_id"])
        with hand.transaction(conn):
            if not conn.execute(f"SELECT 1 FROM {TABLE} WHERE seed = ?", (seed["key"],)).fetchone():
                _insert(conn, row, "answered" if done else "pending", seed=seed["key"], result=done)
                filed += 1
    return filed


# --- sending ----------------------------------------------------------------------------------


def _due_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    now = hand.now()
    return conn.execute(f"SELECT * FROM {TABLE} WHERE status = 'pending' AND expires_at > ?"
                        " AND (retry_at IS NULL OR retry_at <= ?) ORDER BY filed_at, id", (now, now)).fetchall()


def due() -> bool:
    """Whether a run has anything to do (read only; the beat asks first).
    Never under DRY_RUN: a rehearsal settles nothing, so the same dispatch
    would be previewed, and its words judged, every beat until it expired
    (`hands/moltbook dispatch run` by hand still says what it would send)."""
    if hand.dry_run():
        return False
    conn = db()
    try:
        if _due_rows(conn) or conn.execute(f"SELECT 1 FROM {TABLE} WHERE status = 'sending'").fetchone():
            return True
        expired = conn.execute(f"SELECT 1 FROM {TABLE} WHERE status = 'pending' AND expires_at <= ?", (hand.now(),))
        return expired.fetchone() is not None or bool(_seeds_due(conn))
    finally:
        conn.close()


def _settle(conn: sqlite3.Connection, row_id: int, status: str, *, last: Json | None = None, result: str | None = None,
            retry_at: float | None = None, attempted: bool = False) -> None:
    conn.execute(
        f"UPDATE {TABLE} SET status = ?, last = coalesce(?, last), result = coalesce(?, result), retry_at = ?,"
        " attempts = attempts + ?, settled_at = ? WHERE id = ? AND status IN ('pending', 'sending')",
        (status, json.dumps(last, ensure_ascii=False) if last is not None else None, result, retry_at,
         1 if attempted else 0, None if status == "pending" else hand.now(), row_id),
    )


def _thread(row: Mapping[str, Any], api: Any) -> Any:
    """The newest 100 comments of the dispatch's post, read fresh."""
    from . import reads

    return reads.comments(row["post_id"], "new", 100, api, fresh=True).get("comments")


def _already_there(row: Mapping[str, Any], api: Any) -> str | None:
    """Teeny's reply already under the parent (or its comment on the post),
    from a fresh read of the newest 100 comments."""
    from . import actions, reads

    me = reads.me_name(api).lower()
    for node, parent in actions._walk(_thread(row, api)):
        if (actions._author(node) or "").lower() == me and parent == row["parent_id"]:
            return str(node.get("id"))
    return None


def _outcome(refused: hand.Refused, row: Mapping[str, Any]) -> tuple[str, float | None]:
    """What a refusal makes of a dispatch: its status, and when to try again."""
    rule = refused.detail.get("rule")
    if rule == "once_per_parent" or (rule == "once_per_post" and row["kind"] == "comment" and row["parent_id"] is None):
        return "answered", None
    if rule in RETRY_SECS:
        wait = refused.detail.get("wait_seconds")
        secs = float(wait) if isinstance(wait, (int, float)) and not isinstance(wait, bool) and wait > 0 else RETRY_SECS[rule]
        return "pending", hand.now() + max(secs, 1.0)
    if refused.detail.get("judge_failed"):
        # The guard keeps this hold and re-sends it itself once the judge
        # answers (guard/rescreen.py, within its RESEND_SECS): one sender.
        return "held", None
    return "refused", None


def _made(conn: sqlite3.Connection, row: Mapping[str, Any], before: int) -> str | None:
    """The post or comment Moltbook created for this dispatch during the
    attempt that just ended, as the hand's ledger recorded it the moment
    Moltbook returned it (before any challenge): whatever came after, it is
    never sent again."""
    if row["kind"] == "post":
        got = conn.execute("SELECT post_id FROM moltbook_posts WHERE id > ? AND title = ? ORDER BY id LIMIT 1",
                           (before, row["title"])).fetchone()
    else:
        got = conn.execute("SELECT comment_id FROM moltbook_comments WHERE id > ? AND post_id = ? AND parent_id IS ?"
                           " ORDER BY id LIMIT 1", (before, row["post_id"], row["parent_id"])).fetchone()
    return None if got is None else str(got[0] or "") or "(in the ledger)"


def _last_id(conn: sqlite3.Connection, row: Mapping[str, Any]) -> int:
    table = "moltbook_posts" if row["kind"] == "post" else "moltbook_comments"
    return int(conn.execute(f"SELECT coalesce(max(id), 0) FROM {table}").fetchone()[0])


def _send(row: Mapping[str, Any], api: Any) -> Json:
    from . import actions

    if row["kind"] == "post":
        body: Json = {"submolt": row["submolt"], "title": row["title"], "body": row["body"]}
        if row["format"]:
            body["format"] = row["format"]
        return actions.post(body, api)
    body = {"post_id": row["post_id"], "body": row["body"]}
    if row["parent_id"]:
        body["parent_id"] = row["parent_id"]
    return actions.comment(body, api)


def _last_pending(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT coalesce(max(id), 0) FROM moltbook_pending").fetchone()[0])


def _challenged(conn: sqlite3.Connection, row: Mapping[str, Any], since: int) -> bool:
    """Whether a challenge was recorded for this dispatch's own write since
    the attempt's start mark: a row newer than the mark, of this kind, that
    the ledger does not already show belongs to another target. Another
    write's challenge (another post or another comment) never counts."""
    if row["kind"] == "post":
        other = ("SELECT post_id FROM moltbook_posts WHERE post_id IS NOT NULL AND title IS NOT ?", (row["title"],))
    else:
        other = ("SELECT comment_id FROM moltbook_comments WHERE comment_id IS NOT NULL"
                 " AND NOT (post_id IS ? AND parent_id IS ?)", (row["post_id"], row["parent_id"]))
    got = conn.execute("SELECT 1 FROM moltbook_pending WHERE id > ? AND kind = ? AND"
                       f" (content_id IS NULL OR content_id NOT IN ({other[0]})) LIMIT 1",
                       (since, row["kind"], *other[1])).fetchone()
    return got is not None


UNKNOWN = "unknown_result"


def _unknown(why: str) -> Json:
    return {"refused": why[:300], "rule": UNKNOWN}


def _unclear(conn: sqlite3.Connection, row: Mapping[str, Any], api: Any, exc: Exception, before: int,
             before_pending: int) -> tuple[str, str | None, Json]:
    """What a send that failed with an error makes of a dispatch. Moltbook
    may have taken it without saying so (a timeout, a dropped answer), and a
    first copy can sit hidden behind its challenge where no read sees it. So
    it goes out again only when Moltbook plainly refused it (a 4xx answer) and
    nothing says a copy exists: never a post or a top-level comment (no read
    covers every comment on a post), never with a challenge recorded since the
    attempt began; a reply is first looked for again in the thread."""
    made = _made(conn, row, before)
    if made:
        return "refused", made, _unknown(f"Moltbook took it, then: {exc}")
    if _challenged(conn, row, before_pending):
        return "refused", None, _unknown(f"a challenge was left pending during the send ({exc}); a copy may be hidden")
    if row["kind"] == "post" or row["parent_id"] is None:
        return "refused", None, _unknown(f"the send ended without a clear answer: {exc}")
    try:
        there = _already_there(row, api)
    except (RestError, hand.HandError):
        there = None
    if there:
        return "answered", there, {"answered": there}
    status = getattr(exc, "status", None)
    if isinstance(status, int) and 400 <= status < 500:
        return "pending", None, {"error": str(exc)[:300]}
    return "refused", None, _unknown(f"the send ended without a clear answer: {exc}")


def _dead_attempts(conn: sqlite3.Connection) -> int:
    """Attempts a run began and never settled (its worker died mid-send):
    never sent again. A reply the ledger saw land is `answered`, a post it
    saw is `refused` with its id; anything else is `unknown_result`."""
    settled = 0
    for row in map(dict, conn.execute(f"SELECT * FROM {TABLE} WHERE status = 'sending'").fetchall()):
        if row["kind"] == "comment":
            done = _answered(conn, row["post_id"], row["parent_id"])
            status, result, last = ("answered", done, {"answered": done}) if done else (
                "refused", None, _unknown("the run sending it stopped before it said how it went"))
        else:
            got = conn.execute("SELECT post_id FROM moltbook_posts WHERE title = ? AND at >= ? ORDER BY id LIMIT 1",
                               (row["title"], row["filed_at"])).fetchone()
            status, result, last = "refused", (str(got[0]) if got else None), _unknown(
                "the run sending it stopped before it said how it went")
        _settle(conn, row["id"], status, result=result, last=last)
        settled += 1
    return settled


#: One run at a time (the beat's, or one by hand): a row is selected,
#: read, sent and settled under it.
LOCK = "moltbook-dispatch"


def run(api: Any = None) -> Json:
    """Sends what is due, at most one write (module docs), one run at a
    time: a run that finds another at work does nothing (`busy`)."""
    try:
        with hand.lock(LOCK, timeout=1.0):
            return _run(api)
    except hand.HandError as exc:
        if "lock" not in str(exc):
            raise
        return {"busy": True, "sent": 0, "note": "another dispatch run is at work"}


def _run(api: Any = None) -> Json:
    # The mind wrote these words: the comment is tagged and recorded as its.
    os.environ["TEENY_WRITER"] = "mind"
    dry = hand.dry_run()
    conn = db()
    out: Json = {"seeded": 0, "expired": 0, "answered": 0, "sent": 0}
    try:
        if not dry:
            out["seeded"] = _seed(conn)
            with hand.transaction(conn):
                out["expired"] = conn.execute(
                    f"UPDATE {TABLE} SET status = 'expired', retry_at = NULL, settled_at = ? WHERE status = 'pending'"
                    " AND expires_at <= ?", (hand.now(), hand.now())).rowcount
            out["unknown"] = _dead_attempts(conn)
        for row in map(dict, _due_rows(conn)):
            if row["kind"] == "comment":
                done = _answered(conn, row["post_id"], row["parent_id"])
                try:
                    done = done or _already_there(row, api)
                except (RestError, hand.HandError) as exc:
                    if not dry:
                        _settle(conn, row["id"], "pending", last={"error": f"could not read the thread: {exc}"[:300]},
                                retry_at=hand.now() + RETRY_ERROR_SECS)
                    out["deferred"] = row["id"]
                    break
                if done:
                    if not dry:
                        _settle(conn, row["id"], "answered", result=done, last={"answered": done})
                    out["answered"] += 1
                    continue
            if hand.now() >= row["expires_at"]:
                # It ran out while the thread was read: never sent late.
                if not dry:
                    _settle(conn, row["id"], "expired")
                out["expired"] += 1
                continue
            before = _last_id(conn, row)
            before_pending = _last_pending(conn)
            if not dry:
                # On record before Moltbook hears of it: a worker that dies
                # mid-send leaves `sending`, which no run ever sends again.
                with hand.transaction(conn):
                    conn.execute(f"UPDATE {TABLE} SET status = 'sending', attempts = attempts + 1, retry_at = NULL"
                                 " WHERE id = ? AND status = 'pending'", (row["id"],))
            try:
                got = _send(row, api)
            except hand.Refused as refused:
                status, retry_at = _outcome(refused, row)
                made = _made(conn, row, before)
                if (made or _challenged(conn, row, before_pending)) and status != "answered":
                    # Moltbook took it, and then a challenge or its answer
                    # went wrong (or a challenge for this write is on record
                    # though its content row never landed): hidden, perhaps,
                    # but sent once, for good.
                    status, retry_at = "refused", None
                if not dry:
                    _settle(conn, row["id"], status, last={"refused": refused.reason[:300], **refused.detail},
                            result=made or (refused.detail.get("id") if isinstance(refused.detail.get("id"), str) else None),
                            retry_at=retry_at)
                out[status if status != "pending" else "retry"] = {"id": row["id"], "rule": refused.detail.get("rule")}
                break
            except (RestError, hand.HandError) as exc:
                if not dry:
                    status, result, last = _unclear(conn, row, api, exc, before, before_pending)
                    _settle(conn, row["id"], status, result=result, last=last,
                            retry_at=hand.now() + RETRY_ERROR_SECS if status == "pending" else None)
                out["deferred"] = row["id"]
                break
            if dry or got.get("dry_run"):
                out["would_send"] = {"id": row["id"], **got}
                break
            content = got.get("commented") or got.get("posted")
            _settle(conn, row["id"], "sent", result=str(content) if content else None, last=got)
            out["sent"] = 1
            out["dispatch"] = row["id"]
            break
    finally:
        conn.close()
    return out
