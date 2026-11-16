"""The verbs of hands/reddit (docs/hands.md, "reddit"). Reads (R) only read
the ledger. Acting verbs (A) queue a desk task for the browser operator and
take their fields as flags or as a JSON body on stdin (a playbook's and the
mind's way), the flags winning. Operator verbs (O) are for a person at the box.

  R  status                                   the account, caps left, allowlist, rows on the desk
  R  feed [--subreddit S] [--max N]           the threads the last browses brought back
  R  seen [--max N]                           everything a browse brought back (what an upvote may target)
  R  due                                      the next read the reddit-browse playbook owes, or null
  A  browse [--subreddit S] [--sort hot|new|rising]   {subreddit?, sort?, inbox?}
  A  inbox                                    replies and mentions, back to the mind (screened)
  A  stats                                    the profile's karma and age
  A  comment [--thread P]                     {thread, body}
  A  reply [--comment P]                      {comment, body}
  A  post [--subreddit S] [--title T] [--url U]   {subreddit, title, body?, url?}
  A  upvote [P ...]                           {targets: [P, ...]} or {permalink}
  A  subscribe [S]                            {subreddit}
  O  signup [--username U]                    Teeny signs itself up (operator-source signup task)
  O  profile                                  the display name and the AI bio
"""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from .. import hand
from . import account, actions, reads


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def _merged(body: Any, **flags: Any) -> dict[str, Any]:
    """The body on stdin with the flags given on top."""
    if body is not None and not isinstance(body, Mapping):
        raise hand.HandError("stdin must be a JSON object")
    out = dict(body or {})
    out.update({k: v for k, v in flags.items() if v not in (None, [], "")})
    return out


def _upvote(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    if args.permalink:
        return actions.upvote(_merged(body, targets=list(args.permalink)))
    return actions.upvote(body)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/reddit", description="Teeny on Reddit, through the browser desk.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    verb("status", lambda a, _: reads.status(), "the account, the caps and the desk")
    feed = verb("feed", lambda a, _: reads.feed(a.subreddit, a.max), "the threads the last browses brought back")
    feed.add_argument("--subreddit", default=None)
    feed.add_argument("--max", type=_bounded(1, 100, "--max"), default=25)
    verb("seen", lambda a, _: reads.seen(a.max), "what a browse brought back").add_argument(
        "--max", type=_bounded(1, 500, "--max"), default=50)
    verb("due", lambda a, _: reads.due(), "the next read the reddit-browse playbook owes")

    browsing = verb("browse", lambda a, b: reads.browse(_merged(b, subreddit=a.subreddit, sort=a.sort)),
                    "queue a read of a subreddit or the front page", body=True)
    browsing.add_argument("--subreddit", default=None)
    browsing.add_argument("--sort", default=None, choices=reads.SORTS)
    verb("inbox", lambda a, _: reads.inbox(), "queue a read of Teeny's Reddit inbox")
    verb("stats", lambda a, _: reads.stats(), "queue a read of the profile's karma and age")

    verb("comment", lambda a, b: actions.comment(_merged(b, thread=a.thread)),
         "a top-level comment on a thread (body on stdin)", body=True).add_argument("--thread", default=None)
    verb("reply", lambda a, b: actions.reply(_merged(b, comment=a.comment)),
         "an answer to a comment (body on stdin)", body=True).add_argument("--comment", default=None)
    posting = verb("post", lambda a, b: actions.post(_merged(b, subreddit=a.subreddit, title=a.title, url=a.url)),
                   "a post of Teeny's own (body on stdin)", body=True)
    posting.add_argument("--subreddit", default=None)
    posting.add_argument("--title", default=None)
    posting.add_argument("--url", default=None)
    verb("upvote", _upvote, "upvote things a browse brought back", body=True).add_argument(
        "permalink", nargs="*", metavar="PERMALINK")
    verb("subscribe", lambda a, b: actions.subscribe(_merged(b, subreddit=a.subreddit)),
         "join a subreddit", body=True).add_argument("subreddit", nargs="?", default=None, metavar="SUBREDDIT")

    verb("signup", lambda a, _: account.signup(a.username), "Teeny signs itself up (operator)").add_argument(
        "--username", default=account.USERNAMES[0])
    verb("profile", lambda a, _: account.profile(), "set the display name and the AI bio (operator)")
    return root


def main(argv: list[str]) -> int:
    return hand.run("reddit", parser(), argv)
