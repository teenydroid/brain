"""The rules a Reddit write must pass before Reddit hears of it, and the
ledger they read.

Reddit is not a network for agents. Its communities run on their own rules,
spam filters catch an account that posts like a bot, and a shadowban is
silent. So the fences here are tighter than Moltbook's, and the operator,
not Teeny, opens each community:

- **The allowlist** (`subreddit`): Teeny posts, comments and subscribes only
  in the subreddits `REDDIT_SUBREDDITS` names (comma-separated, without
  `r/`). Empty is nowhere. The operator reads a community's rules (`hands/reddit
  subreddit NAME`) before adding it; many forbid bots or self-promotion.
- **The caps** (`daily_cap`, the last 24 hours): `REDDIT_MAX_POSTS_PER_DAY`
  (0) and `REDDIT_MAX_COMMENTS_PER_DAY` (6). A new account that posts is
  what Reddit's spam filter catches first, so a fresh install comments only
  and posts once the operator raises the post cap.
- **The gaps** (`rate_limit`, with `wait_seconds`): `REDDIT_POST_GAP_HOURS`
  (6) between any two posts, `REDDIT_SUBREDDIT_POST_GAP_DAYS` (7) between
  two posts in one subreddit, and `REDDIT_COMMENT_GAP_SECONDS` (120, never
  under 30) between comments. Reddit's own "you are doing that too much"
  (RATELIMIT) is refused with its wait as well, and never retried.
- **Crypto** (`crypto`): text that reads as crypto (Moltbook's detector) goes
  only to a subreddit `REDDIT_CRYPTO_SUBREDDITS` names. Most communities
  remove it and some ban for it.
- **The anchor** (`anchor`, in `actions.post`): a post names the real,
  current thing it is about; comments need none.
- **The thread age** (`too_old`, in `actions`): an unasked comment on a post
  older than `REDDIT_MAX_COMMENT_AGE_HOURS` (3, clamped 1 to 48) is refused;
  an answer to a reply to Teeny is not.
- **Back-offs** (`backoff`, `reddit_backoffs`): a sign of trouble in a
  subreddit (a write answered 403, a ban the community check sees, one of
  Teeny's own posts or comments found removed by `metrics`) stops every
  write there for `REDDIT_BACKOFF_DAYS` (7), refused locally with the time
  left. A sign of trouble with the account (`whoami` says suspended, or the
  account's own read answers 403) stops every write until the operator runs
  `hands/reddit clear-backoff`. Each back-off tells the stimuli inbox once.
- **No votes and no private messages**: there is no verb for either.
  Automated voting is vote manipulation under Reddit's rules, and an
  unasked DM from a bot is spam.

A "day" is the last 24 hours, rolling, as for Moltbook.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Callable, Iterable

from .. import hand, stimuli, tags

MINUTE = 60.0
HOUR = 3600.0
DAY = 86400.0

DEFAULT_MAX_POSTS_PER_DAY = 0
DEFAULT_MAX_COMMENTS_PER_DAY = 6
DEFAULT_POST_GAP_HOURS = 6
DEFAULT_SUBREDDIT_POST_GAP_DAYS = 7
DEFAULT_COMMENT_GAP_SECONDS = 120
MIN_COMMENT_GAP_SECONDS = 30
DEFAULT_MAX_COMMENT_AGE_HOURS = 3
DEFAULT_BACKOFF_DAYS = 7
#: The scope of a back-off on the whole account, not one subreddit.
ACCOUNT = "*"
#: The signals a back-off starts on; the account's last until it is cleared.
SIGNALS = ("forbidden", "banned", "removed", "suspended")
#: The longest wait `slot --wait` sits out (as `hands/moltbook post-slot`).
SLOT_MAX_WAIT = 180

#: Ledger statuses of content Reddit accepted.
COUNTED = ("posted",)

SCHEMA = """
CREATE TABLE IF NOT EXISTS reddit_posts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id   TEXT,
    at        REAL NOT NULL,
    subreddit TEXT NOT NULL COLLATE NOCASE,
    title     TEXT NOT NULL,
    status    TEXT NOT NULL,
    detail    TEXT NOT NULL DEFAULT '{}',
    anchor    TEXT,
    format    TEXT,
    experiment INTEGER
);
CREATE INDEX IF NOT EXISTS reddit_posts_at ON reddit_posts (at);
CREATE TABLE IF NOT EXISTS reddit_comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id TEXT,
    post_id    TEXT NOT NULL,
    parent_id  TEXT,
    subreddit  TEXT NOT NULL COLLATE NOCASE,
    at         REAL NOT NULL,
    status     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '{}',
    format     TEXT,
    experiment INTEGER
);
CREATE INDEX IF NOT EXISTS reddit_comments_post ON reddit_comments (post_id);
CREATE INDEX IF NOT EXISTS reddit_comments_at ON reddit_comments (at);
CREATE TABLE IF NOT EXISTS reddit_actions (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     REAL NOT NULL,
    verb   TEXT NOT NULL,
    target TEXT NOT NULL COLLATE NOCASE,
    detail TEXT NOT NULL DEFAULT '{}',
    UNIQUE (verb, target)
);
CREATE TABLE IF NOT EXISTS reddit_backoffs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT NOT NULL COLLATE NOCASE,
    signal     TEXT NOT NULL,
    reason     TEXT NOT NULL,
    ref        TEXT,
    at         REAL NOT NULL,
    until      REAL,
    cleared_at REAL
);
CREATE INDEX IF NOT EXISTS reddit_backoffs_scope ON reddit_backoffs (scope, cleared_at);
"""


def db() -> sqlite3.Connection:
    conn = hand.open_db(SCHEMA)
    # A ledger from before the anchor and the lab tags keeps its rows.
    hand.add_columns(conn, "reddit_posts", {"anchor": "TEXT", **tags.COLUMNS})
    hand.add_columns(conn, "reddit_comments", tags.COLUMNS)
    return conn


# --- settings ------------------------------------------------------------------------


def _names(setting: str) -> list[str]:
    raw = hand.setting(setting) or ""
    return [p.strip().removeprefix("r/").lower() for p in raw.replace("\n", ",").split(",") if p.strip()]


def allowed_subreddits() -> list[str]:
    return _names("REDDIT_SUBREDDITS")


def crypto_subreddits() -> list[str]:
    return _names("REDDIT_CRYPTO_SUBREDDITS")


def max_posts_per_day() -> int:
    return hand.number("REDDIT_MAX_POSTS_PER_DAY", DEFAULT_MAX_POSTS_PER_DAY, 0, 10)


def max_comments_per_day() -> int:
    return hand.number("REDDIT_MAX_COMMENTS_PER_DAY", DEFAULT_MAX_COMMENTS_PER_DAY, 0, 100)


def post_gap() -> float:
    return hand.number("REDDIT_POST_GAP_HOURS", DEFAULT_POST_GAP_HOURS, 1, 168) * HOUR


def subreddit_post_gap() -> float:
    return hand.number("REDDIT_SUBREDDIT_POST_GAP_DAYS", DEFAULT_SUBREDDIT_POST_GAP_DAYS, 1, 90) * DAY


def comment_gap() -> float:
    return float(hand.number("REDDIT_COMMENT_GAP_SECONDS", DEFAULT_COMMENT_GAP_SECONDS, MIN_COMMENT_GAP_SECONDS, 3600))


def max_comment_age_hours() -> int:
    return hand.number("REDDIT_MAX_COMMENT_AGE_HOURS", DEFAULT_MAX_COMMENT_AGE_HOURS, 1, 48)


def backoff_days() -> int:
    return hand.number("REDDIT_BACKOFF_DAYS", DEFAULT_BACKOFF_DAYS, 1, 90)


def caps() -> dict[str, Any]:
    """Every rule's setting, as the operator's `rules` verb shows them."""
    return {
        "subreddits": allowed_subreddits(),
        "crypto_subreddits": crypto_subreddits(),
        "max_posts_per_day": max_posts_per_day(),
        "max_comments_per_day": max_comments_per_day(),
        "post_gap_hours": post_gap() / HOUR,
        "subreddit_post_gap_days": subreddit_post_gap() / DAY,
        "comment_gap_seconds": comment_gap(),
        "max_comment_age_hours": max_comment_age_hours(),
        "backoff_days": backoff_days(),
    }


# --- the rules -------------------------------------------------------------------------


def check_subreddit(name: str) -> None:
    if name.lower() not in allowed_subreddits():
        raise hand.Refused(
            f"r/{name} is not on Teeny's subreddit allowlist (REDDIT_SUBREDDITS); the operator opens a community"
            " after reading its rules",
            rule="subreddit",
            subreddit=name,
        )


def _wait(last: Any, gap: float, what: str) -> None:
    if last is None:
        return
    elapsed = hand.now() - float(last)
    if elapsed < gap:
        wait = int(gap - elapsed) + 1
        raise hand.Refused(f"{what}; wait {wait}s", rule="rate_limit", wait_seconds=wait)


def _counted(table: str, what: str, more: str = "") -> str:
    return f"SELECT {what} FROM {table} WHERE status IN ({','.join(repr(s) for s in COUNTED)}){more}"


def check_post(conn: sqlite3.Connection, subreddit: str) -> None:
    _wait(conn.execute(_counted("reddit_posts", "max(at)")).fetchone()[0], post_gap(),
          f"one Reddit post per {post_gap() / HOUR:g} hours")
    _wait(conn.execute(_counted("reddit_posts", "max(at)", " AND subreddit = ?"), (subreddit,)).fetchone()[0],
          subreddit_post_gap(), f"one post in r/{subreddit} per {subreddit_post_gap() / DAY:g} days")
    cap = max_posts_per_day()
    done = int(conn.execute(_counted("reddit_posts", "count(*)", " AND at > ?"), (hand.now() - DAY,)).fetchone()[0])
    if done >= cap:
        raise hand.Refused(f"{done} Reddit post(s) in the last 24 hours; the cap is {cap}", rule="daily_cap")


def check_comment(conn: sqlite3.Connection) -> None:
    _wait(conn.execute(_counted("reddit_comments", "max(at)")).fetchone()[0], comment_gap(),
          f"one Reddit comment per {comment_gap():g}s")
    cap = max_comments_per_day()
    done = int(conn.execute(_counted("reddit_comments", "count(*)", " AND at > ?"), (hand.now() - DAY,)).fetchone()[0])
    if done >= cap:
        raise hand.Refused(f"{done} Reddit comment(s) in the last 24 hours; the cap is {cap}", rule="daily_cap")


def looks_crypto(texts: Iterable[str]) -> bool:
    from ..moltbook.rules import looks_crypto as detector

    return detector(texts)


def check_crypto(texts: Iterable[str], subreddit: str) -> None:
    if looks_crypto(texts) and subreddit.lower() not in crypto_subreddits():
        raise hand.Refused(
            f"this reads as crypto, and r/{subreddit} is not on REDDIT_CRYPTO_SUBREDDITS", rule="crypto", subreddit=subreddit
        )


# --- back-offs -------------------------------------------------------------------------


def _in_force(conn: sqlite3.Connection, scope: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM reddit_backoffs WHERE cleared_at IS NULL AND (until IS NULL OR until > ?)"
    params: list[Any] = [hand.now()]
    if scope is not None:
        sql += " AND scope = ?"
        params.append(scope)
    return conn.execute(sql + " ORDER BY at, id", params).fetchall()


def start_backoff(conn: sqlite3.Connection, scope: str, signal: str, reason: str, ref: str | None = None) -> bool:
    """Starts a back-off on `scope` (a subreddit, or ACCOUNT) unless one is
    already in force there, and tells the stimuli inbox of it once. True
    when one started."""
    if signal not in SIGNALS:
        raise hand.HandError(f"a back-off signal is one of {', '.join(SIGNALS)}")
    scope = scope.lower()
    now = hand.now()
    until = None if scope == ACCOUNT else now + backoff_days() * DAY
    with hand.transaction(conn):
        if _in_force(conn, scope):
            return False
        cur = conn.execute(
            "INSERT INTO reddit_backoffs (scope, signal, reason, ref, at, until) VALUES (?, ?, ?, ?, ?, ?)",
            (scope, signal, reason, ref, now, until),
        )
    where = "every Reddit write" if scope == ACCOUNT else f"every write to r/{scope}"
    how_long = "until the operator runs hands/reddit clear-backoff" if until is None else f"for {backoff_days()} days"
    try:
        stimuli.add(
            "reddit", "backoff", f"{scope}:{cur.lastrowid}",
            f"Reddit back-off: {reason}. {where} is stopped {how_long}.",
            payload={"scope": scope, "signal": signal, "reason": reason, "until": until, **({"ref": ref} if ref else {})},
        )
    except Exception as exc:  # noqa: BLE001 - the back-off holds whether or not the mind hears of it
        import sys

        print(f"hands/reddit: a back-off started but the inbox did not take it: {type(exc).__name__}", file=sys.stderr)
    return True


def signal(scope: str, signal: str, reason: str, ref: str | None = None) -> bool:
    """`start_backoff` on a connection of its own."""
    conn = db()
    try:
        return start_backoff(conn, scope, signal, reason, ref)
    finally:
        conn.close()


def backoffs(conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """The back-offs in force, oldest first."""
    own = conn is None
    conn = conn or db()
    try:
        return [
            {k: row[k] for k in ("scope", "signal", "reason", "at", "until")} | ({"ref": row["ref"]} if row["ref"] else {})
            for row in _in_force(conn)
        ]
    finally:
        if own:
            conn.close()


def clear_backoff(subreddit: str | None) -> dict[str, Any]:
    """The operator lifts the back-off on one subreddit or, with none named,
    the account's."""
    from . import client

    scope = client.subreddit_name(subreddit, "SUBREDDIT").lower() if subreddit else ACCOUNT
    conn = db()
    try:
        with hand.transaction(conn):
            cur = conn.execute(
                "UPDATE reddit_backoffs SET cleared_at = ? WHERE scope = ? AND cleared_at IS NULL", (hand.now(), scope)
            )
        return {"cleared": cur.rowcount, "scope": scope}
    finally:
        conn.close()


def check_account(conn: sqlite3.Connection) -> None:
    for row in _in_force(conn, ACCOUNT):
        raise hand.Refused(
            f"every Reddit write is stopped ({row['reason']}) until the operator runs hands/reddit clear-backoff",
            rule="backoff",
            signal=row["signal"],
        )


def check_backoff(conn: sqlite3.Connection, subreddit: str) -> None:
    check_account(conn)
    for row in _in_force(conn, subreddit.lower()):
        left = int(float(row["until"]) - hand.now()) + 1
        raise hand.Refused(
            f"writes to r/{subreddit} are stopped ({row['reason']}); {left / HOUR:.1f} hours left",
            rule="backoff",
            subreddit=subreddit,
            signal=row["signal"],
            wait_seconds=left,
        )


# --- the slot --------------------------------------------------------------------------


def _open(conn: sqlite3.Connection, kind: str) -> None:
    """Refuses as the first write of `kind` would, in any allowlisted
    subreddit; the soonest refusal when every one is shut."""
    check_account(conn)
    subs = allowed_subreddits()
    if not subs:
        raise hand.Refused("REDDIT_SUBREDDITS is empty, so Teeny writes nowhere", rule="subreddit")
    shut: list[hand.Refused] = []
    for sub in subs:
        try:
            check_backoff(conn, sub)
            check_post(conn, sub) if kind == "post" else check_comment(conn)
            return
        except hand.Refused as refused:
            shut.append(refused)
    raise min(shut, key=lambda r: r.detail.get("wait_seconds", float("inf")))


def slot(kind: str, wait: float = 0.0, sleep: Callable[[float], Any] = time.sleep) -> dict[str, Any]:
    """Whether a `kind` write would pass the caps, the gaps and the
    back-offs now (`hands/reddit slot`): {"open": true}, or {"open": false,
    "why": "<rule>: <reason>", "wait_seconds"?}. A wait of at most `wait`
    seconds (and SLOT_MAX_WAIT) is sat out once, then asked again. Reads only."""
    if kind not in ("post", "comment"):
        raise hand.HandError("--kind is post or comment")
    conn = db()
    try:
        waited = 0
        while True:
            try:
                _open(conn, kind)
                return {"open": True, **({"waited": waited} if waited else {})}
            except hand.Refused as refused:
                rule, left = refused.detail.get("rule"), refused.detail.get("wait_seconds")
                if waited or rule != "rate_limit" or not isinstance(left, (int, float)) or left > min(wait, SLOT_MAX_WAIT):
                    out: dict[str, Any] = {"open": False, "why": f"{rule}: {refused.reason}"}
                    if isinstance(left, (int, float)):
                        out["wait_seconds"] = int(left)
                    return out
            sleep(left)
            waited = int(left)
    finally:
        conn.close()
