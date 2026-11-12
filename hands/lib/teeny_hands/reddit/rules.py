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
  (1) and `REDDIT_MAX_COMMENTS_PER_DAY` (10).
- **The gaps** (`rate_limit`, with `wait_seconds`): `REDDIT_POST_GAP_HOURS`
  (6) between any two posts, `REDDIT_SUBREDDIT_POST_GAP_DAYS` (7) between
  two posts in one subreddit, and `REDDIT_COMMENT_GAP_SECONDS` (120, never
  under 30) between comments. Reddit's own "you are doing that too much"
  (RATELIMIT) is refused with its wait as well, and never retried.
- **Crypto** (`crypto`): text that reads as crypto (Moltbook's detector) goes
  only to a subreddit `REDDIT_CRYPTO_SUBREDDITS` names. Most communities
  remove it and some ban for it.
- **No votes and no private messages**: there is no verb for either.
  Automated voting is vote manipulation under Reddit's rules, and an
  unasked DM from a bot is spam.

A "day" is the last 24 hours, rolling, as for Moltbook.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from .. import hand

MINUTE = 60.0
HOUR = 3600.0
DAY = 86400.0

DEFAULT_MAX_POSTS_PER_DAY = 1
DEFAULT_MAX_COMMENTS_PER_DAY = 10
DEFAULT_POST_GAP_HOURS = 6
DEFAULT_SUBREDDIT_POST_GAP_DAYS = 7
DEFAULT_COMMENT_GAP_SECONDS = 120
MIN_COMMENT_GAP_SECONDS = 30

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
    detail    TEXT NOT NULL DEFAULT '{}'
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
    detail     TEXT NOT NULL DEFAULT '{}'
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
"""


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


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
