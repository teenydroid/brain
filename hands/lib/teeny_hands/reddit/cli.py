"""The verbs of hands/reddit (docs/hands.md, "reddit"). Reads (R) go through
the read cache and are safe to give a drafting agent; acting verbs (A) take
a JSON body on stdin and only go through `actions`, which runs the guard,
the rules and DRY_RUN itself. Operator verbs (O) are for a person at the box.

  R  whoami                                   the account: name, karma, suspended or not
  R  feed [SUBREDDIT] [--sort hot|new|top|rising] [--time T] [--max N]
  R  thread ID [--sort S] [--max N]           one post and its comments
  R  search QUERY [--subreddit S] [--sort S] [--max N]
  R  user NAME                                another account
  R  subreddit NAME                           a community's about page and its rules
  R  inbox [--days N] [--max N]               replies and mentions, screened, into Teeny's mind's inbox
  A  post       {subreddit, title, body? | url?, flair_id?}
  A  comment    {post_id, body, parent_id?}
  A  subscribe  {subreddit}
  O  rules                                    the allowlist, caps and gaps in force
  O  check                                    the credentials work: a token, the account, the allowlist
"""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from .. import hand
from . import actions, client, inbox, reads, rules


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def _subreddit(name: str) -> Mapping[str, Any]:
    got = reads.subreddit(name)
    if got is None:
        raise hand.Refused("Reddit has no open community by that name", subreddit=name)
    return {"subreddit": got, "on_allowlist": client.subreddit_name(name).lower() in rules.allowed_subreddits()}


def _check(_: argparse.Namespace, __: Any) -> Mapping[str, Any]:
    """The operator's first command after filling in secrets/teeny.env."""
    me = reads.whoami()["me"]
    if not me.get("name"):
        raise hand.HandError("Reddit gave a token but no account; is it a script app on the right account?")
    if str(me["name"]).lower() != client.username().lower():
        raise hand.HandError("the token is for a different account than REDDIT_USERNAME")
    out: dict[str, Any] = {"me": me, "rules": rules.caps(), "dry_run": hand.dry_run()}
    if not rules.allowed_subreddits():
        out["next"] = "REDDIT_SUBREDDITS is empty, so Teeny can read but write nowhere. Add the communities it may join."
    return out


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/reddit", description="The only holder of the Reddit credentials.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    verb("whoami", lambda a, _: reads.whoami(), "the account")
    feed = verb("feed", lambda a, _: reads.feed(a.subreddit, a.sort, a.max, a.time), "a subreddit's posts")
    feed.add_argument("subreddit", metavar="SUBREDDIT", nargs="?", help="default: every subreddit on the allowlist")
    feed.add_argument("--sort", default="hot", choices=reads.SORTS)
    feed.add_argument("--time", default="day", choices=reads.TIMES, help="for --sort top")
    feed.add_argument("--max", type=_bounded(1, 50, "--max"), default=15)
    thread = verb("thread", lambda a, _: reads.thread(a.id, a.sort, a.max), "one post and its comments")
    thread.add_argument("id", metavar="ID")
    thread.add_argument("--sort", default="confidence", choices=reads.COMMENT_SORTS)
    thread.add_argument("--max", type=_bounded(1, 200, "--max"), default=50)
    search = verb("search", lambda a, _: reads.search(a.query, a.subreddit, a.sort, a.max), "search posts")
    search.add_argument("query", metavar="QUERY")
    search.add_argument("--subreddit", default=None)
    search.add_argument("--sort", default="relevance", choices=("relevance", "new", "top", "comments"))
    search.add_argument("--max", type=_bounded(1, 50, "--max"), default=10)
    verb("user", lambda a, _: reads.user(a.name), "another account").add_argument("name", metavar="NAME")
    verb("subreddit", lambda a, _: _subreddit(a.name), "a community and its rules").add_argument("name", metavar="NAME")
    scanning = verb("inbox", lambda a, _: inbox.scan(a.days, a.max), "replies and mentions, into Teeny's mind's inbox")
    scanning.add_argument("--days", type=_bounded(1, 30, "--days"), default=7)
    scanning.add_argument("--max", type=_bounded(1, 100, "--max"), default=50)

    for name, handler, help in (
        ("post", actions.post, "submit a post (JSON on stdin)"),
        ("comment", actions.comment, "comment on a post or answer a comment (JSON on stdin)"),
        ("subscribe", actions.subscribe, "join a subreddit on the allowlist (JSON on stdin)"),
    ):
        verb(name, lambda args, body, handler=handler: handler(body), help, body=True)

    verb("rules", lambda a, _: {"rules": rules.caps(), "dry_run": hand.dry_run()}, "the rules in force (operator)")
    verb("check", _check, "check the credentials (operator)")
    return root


def main(argv: list[str]) -> int:
    return hand.run("reddit", parser(), argv)
