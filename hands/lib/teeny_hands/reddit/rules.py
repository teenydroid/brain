"""The rules a Reddit write must pass before it reaches the browser desk,
and the ledger they read (docs/hands.md, "reddit").

Reddit is not a network for agents. Its communities run on their own rules,
spam filters catch an account that posts like a bot, a shadowban is silent,
and vote manipulation is a ban. So:

- **An account first** (`no_account`): nothing is written before a signup
  task recorded Teeny's Reddit account (`accounts`, hands/tasks).
- **The allowlist** (`subreddit`): Teeny comments and posts only in the
  subreddits deploy/reddit-subreddits.txt names, an operator-owned list of
  communities that tolerate a disclosed AI account. It may browse anywhere
  public.
- **Rules read first** (`no_rules`): a comment or post in a subreddit needs
  a `reddit_browse` of it that captured its rules; the rules text rides
  along in the write task's goal.
- **The caps** (`daily_cap`, the last 24 hours, with `wait_seconds`):
  REDDIT_COMMENTS_PER_DAY (6, at most 20; comments and replies together),
  REDDIT_POSTS_PER_DAY (1, at most 3), REDDIT_VOTES_PER_DAY (15, at most 40;
  each target counts), REDDIT_SUBSCRIBES_PER_DAY (3, at most 10). A task
  still waiting on the desk counts; one that failed does not.
- **The post gap** (`rate_limit`, with `wait_seconds`): POST_GAP_HOURS (6)
  between two posts.
- **The new-account ramp** (`ramp`): comments only until the account is
  RAMP_DAYS (7) old and holds RAMP_COMMENT_KARMA (50) comment karma; posts
  after that.
- **Votes only on what Teeny read** (`unseen`, `own`): an upvote's target is
  something a browse brought back (`reddit_seen`), never Teeny's own post or
  comment, and never twice (`duplicate`).
- **Crypto** (`crypto`): text that reads as crypto (Moltbook's detector) is
  never written; most communities remove it and some ban for it.

A browse of one subreddit (or the front page) is queued at most once an
hour (`rate_limit`).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .. import hand

MINUTE = 60.0
HOUR = 3600.0
DAY = 86400.0

POST_GAP_HOURS = 6
BROWSE_GAP_SECONDS = HOUR
RAMP_DAYS = 7
RAMP_COMMENT_KARMA = 50
#: How many targets one vote task carries.
VOTE_BATCH = 5
#: The share of a subreddit's rules that rides in a write task's goal.
RULES_CHARS = 1200

#: cap group -> (the setting, its default, its ceiling, the verbs it counts)
CAPS = {
    "comment": ("REDDIT_COMMENTS_PER_DAY", 6, 20, ("comment", "reply")),
    "post": ("REDDIT_POSTS_PER_DAY", 1, 3, ("post",)),
    "upvote": ("REDDIT_VOTES_PER_DAY", 15, 40, ("upvote",)),
    "subscribe": ("REDDIT_SUBSCRIBES_PER_DAY", 3, 10, ("subscribe",)),
}
#: Ledger rows that count: on the desk, or done.
COUNTED = ("queued", "done")
STATUSES = ("queued", "done", "failed")
#: Verbs Teeny does once per target.
ONCE = ("comment", "reply", "upvote", "subscribe")

SCHEMA = """
CREATE TABLE IF NOT EXISTS reddit_actions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    verb       TEXT NOT NULL,
    target     TEXT NOT NULL,
    subreddit  TEXT COLLATE NOCASE,
    title      TEXT,
    body       TEXT,
    task_id    INTEGER,
    status     TEXT NOT NULL DEFAULT 'queued',
    permalink  TEXT,
    own        TEXT,
    created_at REAL NOT NULL,
    settled_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS reddit_actions_once ON reddit_actions (verb, target)
    WHERE verb IN ('comment', 'reply', 'upvote', 'subscribe') AND status != 'failed';
CREATE INDEX IF NOT EXISTS reddit_actions_task ON reddit_actions (task_id);
CREATE INDEX IF NOT EXISTS reddit_actions_at ON reddit_actions (verb, created_at);
CREATE TABLE IF NOT EXISTS reddit_account (
    handle        TEXT PRIMARY KEY,
    karma_post    INTEGER,
    karma_comment INTEGER,
    age_days      REAL,
    checked_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reddit_seen (
    target    TEXT PRIMARY KEY,
    permalink TEXT NOT NULL,
    subreddit TEXT COLLATE NOCASE,
    title     TEXT,
    author    TEXT,
    task_id   INTEGER,
    seen_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reddit_rules (
    subreddit   TEXT PRIMARY KEY COLLATE NOCASE,
    rules       TEXT NOT NULL,
    task_id     INTEGER,
    captured_at REAL NOT NULL
);
"""

Json = dict[str, Any]


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


# --- names and links -------------------------------------------------------------------

_SUBREDDIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]{1,20}$")
_PERMALINK = re.compile(
    r"^https://(?:(?:www|old|new|np)\.)?reddit\.com/r/([A-Za-z0-9][A-Za-z0-9_]{1,20})/comments/([a-z0-9]{1,12})"
    r"(?:/([^/?#]*)(?:/([a-z0-9]{1,12}))?)?/?(?:[?#].*)?$"
)
_USER = re.compile(r"^[A-Za-z0-9_-]{3,20}$")


def subreddit(value: Any) -> str:
    """A subreddit's name as Reddit spells it, without `r/`."""
    if not isinstance(value, str):
        raise hand.HandError("a subreddit is a name like AI_Agents")
    name = value.strip()
    for prefix in ("/r/", "r/"):
        if name.lower().startswith(prefix):
            name = name[len(prefix):]
    if not _SUBREDDIT.match(name):
        raise hand.HandError(f"{value!r} is not a subreddit name (letters, digits, underscores, 2 to 21)")
    return name


def username(value: Any) -> str:
    if not isinstance(value, str) or not _USER.match(value.strip().removeprefix("u/")):
        raise hand.HandError("a Reddit username is 3 to 20 letters, digits, dashes or underscores")
    return value.strip().removeprefix("u/")


def permalink(value: Any, want: str | None = None) -> Json:
    """A thread's or a comment's link, checked: {subreddit, thread, comment,
    target (t3_<thread> or t1_<comment>), url (on old.reddit.com)}. `want`
    is "thread" or "comment" when only one will do."""
    match = _PERMALINK.match(value.strip()) if isinstance(value, str) else None
    if match is None:
        raise hand.HandError(f"{value!r} is not a Reddit permalink (https://old.reddit.com/r/<sub>/comments/<id>/...)")
    sub, thread, _, comment = match.groups()
    out: Json = {"subreddit": sub, "thread": thread, "comment": comment}
    if comment:
        out["target"] = f"t1_{comment}"
        out["url"] = f"https://old.reddit.com/r/{sub}/comments/{thread}/_/{comment}/"
    else:
        out["target"] = f"t3_{thread}"
        out["url"] = f"https://old.reddit.com/r/{sub}/comments/{thread}/"
    if want == "thread" and comment:
        raise hand.HandError("that is a comment's permalink; a comment goes on a thread (reply answers a comment)")
    if want == "comment" and not comment:
        raise hand.HandError("that is a thread's permalink; reply answers a comment (comment goes on a thread)")
    return out


# --- the allowlist -----------------------------------------------------------------------


def allowlist_path() -> Path:
    return hand.root() / "deploy" / "reddit-subreddits.txt"


def allowlist() -> list[str]:
    """deploy/reddit-subreddits.txt, one subreddit a line, `#` comments. No
    file is nowhere."""
    try:
        text = allowlist_path().read_text()
    except FileNotFoundError:
        return []
    names = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            names.append(subreddit(line))
        except hand.HandError as exc:
            raise hand.HandError(f"deploy/reddit-subreddits.txt line {number}: {exc}") from None
    return names


def check_allowed(name: str) -> str:
    """The allowlist's spelling of `name`, or refused."""
    for listed in allowlist():
        if listed.lower() == name.lower():
            return listed
    raise hand.Refused(
        f"r/{name} is not on Teeny's subreddit allowlist (deploy/reddit-subreddits.txt): it may read there, never write",
        rule="subreddit", subreddit=name,
    )


def captured_rules(conn: sqlite3.Connection, name: str) -> str:
    """The subreddit's rules a browse captured, or refused."""
    row = conn.execute("SELECT rules FROM reddit_rules WHERE subreddit = ?", (name,)).fetchone()
    if row is None:
        raise hand.Refused(
            f"r/{name}'s rules have not been read yet: browse it first (hands/reddit browse --subreddit {name})",
            rule="no_rules", subreddit=name,
        )
    return str(row["rules"])


# --- the account -------------------------------------------------------------------------


def account() -> Json | None:
    """Teeny's Reddit account: the handle a signup recorded (`accounts`),
    when it was made, and what the last stats read said. None before one."""
    from ..tasks import queue

    conn = queue.db()
    try:
        made = conn.execute(
            "SELECT handle, created_at FROM accounts WHERE site = 'reddit.com' OR site LIKE '%.reddit.com'"
            " ORDER BY created_at, id LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if made is None:
        return None
    out: Json = {"handle": made["handle"], "created_at": made["created_at"]}
    conn = db()
    try:
        read = conn.execute("SELECT * FROM reddit_account ORDER BY checked_at DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    age = (hand.now() - float(made["created_at"])) / DAY
    if read is not None:
        out["handle"] = read["handle"]
        for key in ("karma_post", "karma_comment"):
            if read[key] is not None:
                out[key] = int(read[key])
        if read["age_days"] is not None:
            age = max(age, float(read["age_days"]) + (hand.now() - float(read["checked_at"])) / DAY)
        out["checked_at"] = read["checked_at"]
    out["age_days"] = round(age, 2)
    return out


def need_account() -> Json:
    found = account()
    if found is None:
        raise hand.Refused("Teeny has no Reddit account yet: the operator runs hands/reddit signup first",
                           rule="no_account")
    return found


def check_ramp(found: Json) -> None:
    """Posts wait until the account is RAMP_DAYS old with RAMP_COMMENT_KARMA
    comment karma: comments, helpfully, first."""
    age = float(found.get("age_days") or 0)
    if age < RAMP_DAYS:
        raise hand.Refused(
            f"the Reddit account is {age:.1f} days old; it posts from day {RAMP_DAYS}, with {RAMP_COMMENT_KARMA} "
            "comment karma. Comment helpfully until then",
            rule="ramp", wait_seconds=int((RAMP_DAYS - age) * DAY) + 1,
        )
    karma = int(found.get("karma_comment") or 0)
    if karma < RAMP_COMMENT_KARMA:
        raise hand.Refused(
            f"the Reddit account holds {karma} comment karma; it posts from {RAMP_COMMENT_KARMA}. Comment helpfully "
            "until then (hands/reddit stats reads the karma again)",
            rule="ramp",
        )


# --- the caps ------------------------------------------------------------------------------


def cap(group: str) -> int:
    name, default, ceiling, _ = CAPS[group]
    return hand.number(name, default, 0, ceiling)


def caps() -> Json:
    return {group: cap(group) for group in CAPS}


def _marks(values: Iterable[Any]) -> str:
    return ",".join("?" for _ in values)


def check_cap(conn: sqlite3.Connection, group: str, adding: int = 1) -> None:
    """Refuses when `adding` more would pass the group's cap over the last
    24 hours, saying when room opens."""
    limit = cap(group)
    verbs = CAPS[group][3]
    since = hand.now() - DAY
    rows = conn.execute(
        f"SELECT created_at FROM reddit_actions WHERE verb IN ({_marks(verbs)}) AND status IN ({_marks(COUNTED)})"
        " AND created_at > ? ORDER BY created_at",
        (*verbs, *COUNTED, since),
    ).fetchall()
    used = len(rows)
    if used + adding <= limit:
        return
    detail: Json = {"rule": "daily_cap", "cap": limit, "used": used}
    free_at = used + adding - limit
    if limit and adding <= limit and free_at <= used:
        detail["wait_seconds"] = int(float(rows[free_at - 1]["created_at"]) + DAY - hand.now()) + 1
    raise hand.Refused(f"{used} Reddit {group}(s) in the last 24 hours; the cap is {limit} ({CAPS[group][0]})",
                       **detail)


def check_post_gap(conn: sqlite3.Connection) -> None:
    last = conn.execute(
        f"SELECT max(created_at) FROM reddit_actions WHERE verb = 'post' AND status IN ({_marks(COUNTED)})", COUNTED
    ).fetchone()[0]
    gap = POST_GAP_HOURS * HOUR
    if last is not None and hand.now() - float(last) < gap:
        wait = int(float(last) + gap - hand.now()) + 1
        raise hand.Refused(f"one Reddit post per {POST_GAP_HOURS} hours; wait {wait}s", rule="rate_limit",
                           wait_seconds=wait)


def check_browse_gap(conn: sqlite3.Connection, verb: str, target: str) -> None:
    last = conn.execute(
        f"SELECT max(created_at) FROM reddit_actions WHERE verb = ? AND target = ? AND status IN ({_marks(COUNTED)})",
        (verb, target, *COUNTED),
    ).fetchone()[0]
    if last is not None and hand.now() - float(last) < BROWSE_GAP_SECONDS:
        wait = int(float(last) + BROWSE_GAP_SECONDS - hand.now()) + 1
        raise hand.Refused(f"{target} was read less than an hour ago; wait {wait}s", rule="rate_limit",
                           wait_seconds=wait)


def check_once(conn: sqlite3.Connection, verb: str, target: str) -> None:
    row = conn.execute(
        f"SELECT status, task_id FROM reddit_actions WHERE verb = ? AND target = ? AND status IN ({_marks(COUNTED)})",
        (verb, target, *COUNTED),
    ).fetchone()
    if row is not None:
        raise hand.Refused(f"Teeny already did {verb} on {target} (task {row['task_id']}, {row['status']})",
                           rule="duplicate", target=target)


def own_targets(conn: sqlite3.Connection) -> set[str]:
    """Teeny's own posts and comments, as the ledger's settled writes name them."""
    return {r[0] for r in conn.execute("SELECT own FROM reddit_actions WHERE own IS NOT NULL")}


def check_vote(conn: sqlite3.Connection, target: str, handle: str | None) -> None:
    """An upvote only on something a browse brought back, never on Teeny's own."""
    seen = conn.execute("SELECT author FROM reddit_seen WHERE target = ?", (target,)).fetchone()
    if target in own_targets(conn) or (seen is not None and handle and str(seen["author"] or "").lower()
                                       in (handle.lower(), f"u/{handle.lower()}")):
        raise hand.Refused("never a vote on Teeny's own post or comment", rule="own", target=target)
    if seen is None:
        raise hand.Refused("an upvote goes only on a post or comment a browse brought back (hands/reddit seen)",
                           rule="unseen", target=target)
    check_once(conn, "upvote", target)


# --- crypto ------------------------------------------------------------------------------


def check_crypto(texts: Iterable[str]) -> None:
    from ..moltbook.rules import looks_crypto

    if looks_crypto(texts):
        raise hand.Refused("this reads as crypto; Teeny does not write about it on Reddit", rule="crypto")
