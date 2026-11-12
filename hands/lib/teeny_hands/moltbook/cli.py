"""The verbs of hands/moltbook (docs/hands.md, "moltbook"). Reads (R) go
through the read cache and are safe to give a drafting agent; acting verbs
(A) take a JSON body on stdin and only go through `actions`, which runs the
guard, the rules and DRY_RUN itself. Operator verbs (O) are for a person at
the box.

  R  whoami                                   /agents/me
  R  status                                   claim status
  R  home [--expand]                          the dashboard: notifications, what to do next;
                                              --expand attaches the post behind each mention
  R  feed [--submolt S] [--sort hot|new|top|rising] [--max N]
  R  post ID                                  one post (with no ID, the acting verb below)
  R  comments ID [--sort best|new|old] [--max N]
  R  search QUERY [--type posts|comments|all] [--max N]
  R  profile NAME                             another agent
  R  submolts                                 every submolt
  R  submolt NAME                             one submolt (allow_crypto and all)
  R  inbox [--days N] [--max N]               comments said to Teeny (on its posts, under its
                                              comments), screened, into its mind's inbox
  R  metrics refresh                          measure Teeny's own writes due a checkpoint (1h,
                                              24h, 72h) into moltbook_post_metrics (under DRY_RUN too)
  R  metrics list [--days N]                  the latest row per write measured in the last N days
  A  post       {submolt, title, body?, url?, type?}
  A  comment    {post_id, body, parent_id?}
  A  upvote     {post_id} or {comment_id}
  A  follow     {name}
  A  subscribe  {submolt}
  O  register --name N --description D        prints the new key once; stores nothing
  O  pending [--status S]                     challenges left for the operator, and the failure count
  O  verify ID --answer N                     answer a pending challenge
  O  unlock                                   clear the verification lockout
  O  collab add NAME --note T | collab list | collab remove NAME
"""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from .. import hand
from . import actions, client, inbox, metrics, reads, rules


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def _post(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    # `post ID` reads; `post` with a body on stdin publishes. Stdin is read
    # only for the second, so a read never waits on it.
    if args.id:
        return reads.post(args.id)
    return actions.post(hand.read_body())


def _metrics(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return metrics.refresh() if args.action == "refresh" else metrics.listing(args.days)


def _collab(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    if args.action == "list":
        return rules.collab_list()
    if not args.name:
        raise hand.HandError(f"collab {args.action} needs NAME")
    name = client.agent_name(args.name, "NAME")
    if args.action == "remove":
        return rules.collab_remove(name)
    if not args.note or not args.note.strip():
        raise hand.HandError("collab add needs --note: where and how the consent was given")
    return rules.collab_add(name, args.note.strip())


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/moltbook", description="The only holder of the Moltbook key.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    verb("whoami", lambda a, _: reads.whoami(), "Teeny's own agent")
    verb("status", lambda a, _: reads.status(), "claim status")
    verb("home", lambda a, _: reads.home(expand=a.expand), "the dashboard").add_argument(
        "--expand", action="store_true", help=f"attach the post behind each mention (at most {reads.EXPAND_MAX})"
    )
    feed = verb("feed", lambda a, _: reads.feed(a.submolt, a.sort, a.max), "posts, newest or hottest")
    feed.add_argument("--submolt", default=None)
    feed.add_argument("--sort", default="hot", choices=reads.SORTS)
    feed.add_argument("--max", type=_bounded(1, 50, "--max"), default=15)
    verb("post", _post, "one post by ID, or publish one (JSON on stdin)").add_argument("id", nargs="?", metavar="ID")
    comments = verb("comments", lambda a, _: reads.comments(a.id, a.sort, a.max), "a post's comments")
    comments.add_argument("id", metavar="ID")
    comments.add_argument("--sort", default="best", choices=reads.COMMENT_SORTS)
    comments.add_argument("--max", type=_bounded(1, 100, "--max"), default=35)
    search = verb("search", lambda a, _: reads.search(a.query, a.type, a.max), "semantic search")
    search.add_argument("query", metavar="QUERY")
    search.add_argument("--type", default="all", choices=reads.SEARCH_TYPES)
    search.add_argument("--max", type=_bounded(1, 50, "--max"), default=10)
    verb("profile", lambda a, _: reads.profile(a.name), "another agent's profile").add_argument("name", metavar="NAME")
    scanning = verb("inbox", lambda a, _: inbox.scan(a.days, a.max), "comments said to Teeny, into its mind's inbox")
    scanning.add_argument("--days", type=_bounded(1, 30, "--days"), default=7)
    scanning.add_argument("--max", type=_bounded(1, 50, "--max"), default=10)
    measuring = verb("metrics", _metrics, "how Teeny's own posts and comments landed")
    measuring.add_argument("action", choices=("refresh", "list"))
    measuring.add_argument("--days", type=_bounded(1, 30, "--days"), default=7)
    verb("study", lambda a, _: study.study(), "what wins on Moltbook this week, and how Teeny's own writes did")
    verb("submolts", lambda a, _: reads.submolts(), "every submolt")
    verb("submolt", lambda a, _: {"submolt": reads.submolt(a.name)}, "one submolt").add_argument("name", metavar="NAME")

    for name, handler, help in (
        ("comment", actions.comment, "comment on a post or answer a comment (JSON on stdin)"),
        ("upvote", actions.upvote, "upvote a post or a comment (JSON on stdin)"),
        ("follow", actions.follow, "follow an agent (JSON on stdin)"),
        ("subscribe", actions.subscribe, "subscribe to a submolt (JSON on stdin)"),
    ):
        verb(name, lambda args, body, handler=handler: handler(body), help, body=True)

    register = verb("register", lambda a, _: actions.register(a.name, a.description), "register a new agent (operator)")
    register.add_argument("--name", required=True)
    register.add_argument("--description", required=True)
    verb("pending", lambda a, _: actions.pending_list(a.status), "challenges waiting for the operator").add_argument(
        "--status", default=None
    )
    check = verb("verify", lambda a, _: actions.verify(a.id, a.answer), "answer a pending challenge (operator)")
    check.add_argument("id", type=int, metavar="ID")
    check.add_argument("--answer", required=True)
    verb("unlock", lambda a, _: actions.unlock(), "clear the verification lockout (operator)")
    collab = verb("collab", _collab, "collaboration consent on record (operator)")
    collab.add_argument("action", choices=("add", "list", "remove"))
    collab.add_argument("name", nargs="?", metavar="NAME")
    collab.add_argument("--note", default=None)
    return root


def main(argv: list[str]) -> int:
    return hand.run("moltbook", parser(), argv)
