"""The verbs of hands/moltbook (docs/hands.md, "moltbook"). Reads (R) go
through the read cache and are safe to give a drafting agent; acting verbs
(A) take a JSON body on stdin and only go through `actions`, which runs the
guard, the rules and DRY_RUN itself. Operator verbs (O) are for a person at
the box.

  R  whoami                                   /agents/me
  R  status                                   claim status
  R  home [--expand]                          the dashboard: notifications, what to do next;
                                              --expand attaches the post behind each mention
  R  feed [--submolt S]... [--sort hot|new|top|rising] [--max N] [--brief]
          [--max-age-hours H] [--max-author-karma K] [--min-author-karma K]
          [--max-comments N] [--skip-mine]
                                              several --submolt are read and merged, newest first;
                                              the filters drop posts before the cut (--skip-mine:
                                              Teeny's own and those it already commented on)
  R  peers [--per-peer N] [--max-age-hours H] [--skip-mine]
                                              the newest posts of the agents in brain/moltbook-peers.txt
  R  post ID                                  one post (with no ID, the acting verb below)
  R  comments ID [--sort best|new|old] [--max N]
  R  search QUERY [--type posts|comments|all] [--max N]
  R  profile NAME                             another agent
  R  submolts                                 every submolt
  R  submolt NAME                             one submolt (allow_crypto and all)
  R  inbox [--days N] [--max N] [--max-own N] comments said to Teeny (on its posts, under its
                                              comments), screened, into its mind's inbox; a comment
                                              on its own post left unanswered is offered again
  R  unanswered [--max N]                     comments on Teeny's own posts still owed an answer,
                                              with their age, and how the last 24 hours settled
  R  metrics refresh                          measure Teeny's own writes due a checkpoint (1h,
                                              24h, 72h) into moltbook_post_metrics (under DRY_RUN too)
  R  metrics list [--days N]                  the latest row per write measured in the last N days
  R  metrics daily                            the day's numbers hands/stats keeps (live posts, scores,
                                              writes lost to the challenge, unanswered own-thread comments)
  R  follows [--days N] [--max N]             follows at least N days old (7) that Teeny has not
                                              unfollowed, and whether each follows back (null = unknown)
  R  exchanges [--days N] [--max N]          agents with a real exchange with Teeny in the last N days
                                              (14), and the peers, none followed or unfollowed yet
  R  study                                    the week's best posts (hot+top, six submolts) and
                                              Teeny's own last 10 writes with their metrics; cached 6h;
                                              own_summary: its own writes per format and submolt, 7d and 14d
  R  material                                 Teeny's own receipts of the last 7 days as dated fact
                                              lines: guard holds, health, merged PRs, journal beats
  R  post-slot [--wait S]                     whether a post would pass the limits now: {open: true},
                                              or only {why}; a wait for the gap of at most S seconds
                                              (0, at most 180) is sat out first. A posting playbook's
                                              first input (required), so a closed gap skips its run
  R  highlight [--hours N]                    the day's best exchange said to Teeny, for an X post
                                              (bridge.py); null when nothing qualifies
  A  post       {submolt, title, body?, url?, type?, format?, experiment?}
  A  comment    {post_id, body, parent_id?, format?}   no format: the playbook (TEENY_WRITER) or where it went
  A  helpdesk   {post_id?, body?, parent_id?, pain?}  an answer, a pain filed as a build request, or both
  A  decline    {comment_id, reason, note?}   a comment on Teeny's own post it will not answer:
                                              reason spam, boilerplate, crypto or instruction
  A  upvote     {post_id} or {comment_id}
  A  follow     {name}                        at most MOLTBOOK_MAX_FOLLOWS_PER_DAY (30) a day
  A  unfollow   {name}                        never followed again afterwards
  A  subscribe  {submolt}
  O  register --name N --description D        prints the new key once; stores nothing
  O  pending [--status S]                     challenges left for the operator, and the failure count
  O  verify ID --answer N                     answer a pending challenge
  O  describe --description D                set the profile text (PATCH /agents/me); operator-run
  O  prune [--days N] [--max N]               unfollow whoever followed 7+ days ago and does not
                                              follow back (a profile that does not say stays followed)
  O  unlock                                   clear the verification lockout
  O  collab add NAME --note T | collab list | collab remove NAME
"""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from .. import hand
from . import actions, bridge, client, exchanges, inbox, material, metrics, owed, reads, rules, study


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


def _hours(value: str) -> float:
    number = float(value)
    if not 0.1 <= number <= 168:
        raise argparse.ArgumentTypeError("--max-age-hours is 0.1..168")
    return number


def _feed(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return reads.feed(
        args.submolt, args.sort, args.max, brief=args.brief, max_age_hours=args.max_age_hours,
        max_author_karma=args.max_author_karma, min_author_karma=args.min_author_karma,
        max_comments=args.max_comments, skip_mine=args.skip_mine,
    )


def _metrics(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    if args.action == "daily":
        return {"metrics": metrics.daily()}
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
    feed = verb("feed", _feed, "posts, newest or hottest")
    feed.add_argument("--brief", action="store_true", help="each post cut down to what a reader needs")
    feed.add_argument("--submolt", action="append", default=None, help="repeat to read several, merged newest first")
    feed.add_argument("--sort", default="hot", choices=reads.SORTS)
    feed.add_argument("--max", type=_bounded(1, 50, "--max"), default=15)
    feed.add_argument("--max-age-hours", type=_hours, default=None, help="drop posts older than this")
    feed.add_argument("--max-author-karma", type=_bounded(0, 10**9, "--max-author-karma"), default=None)
    feed.add_argument("--min-author-karma", type=_bounded(0, 10**9, "--min-author-karma"), default=None)
    feed.add_argument("--max-comments", type=_bounded(0, 10**6, "--max-comments"), default=None)
    feed.add_argument("--skip-mine", action="store_true", help="drop Teeny's own posts and those it already commented on")
    peering = verb(
        "peers",
        lambda a, _: reads.peers(a.per_peer, max_age_hours=a.max_age_hours, skip_mine=a.skip_mine),
        "the newest posts of Teeny's peers (brain/moltbook-peers.txt)",
    )
    peering.add_argument("--per-peer", type=_bounded(1, 10, "--per-peer"), default=2)
    peering.add_argument("--max-age-hours", type=_hours, default=None)
    peering.add_argument("--skip-mine", action="store_true")
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
    scanning = verb("inbox", lambda a, _: inbox.scan(a.days, a.max, max_own=a.max_own), "comments said to Teeny, into its mind's inbox")
    scanning.add_argument("--days", type=_bounded(1, 30, "--days"), default=7)
    scanning.add_argument("--max", type=_bounded(1, 50, "--max"), default=10)
    scanning.add_argument("--max-own", type=_bounded(1, 50, "--max-own"), default=inbox.MAX_OWN)
    verb("unanswered", lambda a, _: owed.unanswered(a.max), "comments on Teeny's own posts still owed an answer").add_argument(
        "--max", type=_bounded(1, 200, "--max"), default=owed.LISTED
    )
    measuring = verb("metrics", _metrics, "how Teeny's own posts and comments landed")
    measuring.add_argument("action", choices=("refresh", "list", "daily"))
    measuring.add_argument("--days", type=_bounded(1, 30, "--days"), default=7)
    talking = verb("exchanges", lambda a, _: exchanges.exchanges(a.days, a.max), "who Teeny has really talked to, to follow")
    talking.add_argument("--days", type=_bounded(1, 60, "--days"), default=14)
    talking.add_argument("--max", type=_bounded(1, 30, "--max"), default=10)
    verb("study", lambda a, _: study.study(), "what wins on Moltbook this week, and how Teeny's own writes did")
    verb("material", lambda a, _: material.material(), "Teeny's own receipts of the last 7 days, as dated facts")
    verb("highlight", lambda a, _: bridge.highlight(a.hours), "the day's best exchange said to Teeny").add_argument(
        "--hours", type=_bounded(1, 72, "--hours"), default=24)
    verb("post-slot", lambda a, _: rules.post_slot(a.wait), "whether a post would pass the limits now").add_argument(
        "--wait", type=_bounded(0, rules.POST_SLOT_MAX_WAIT, "--wait"), default=0)
    verb("submolts", lambda a, _: reads.submolts(), "every submolt")
    verb("submolt", lambda a, _: {"submolt": reads.submolt(a.name)}, "one submolt").add_argument("name", metavar="NAME")

    for name, handler, help in (
        ("comment", actions.comment, "comment on a post or answer a comment (JSON on stdin)"),
        ("helpdesk", bridge.helpdesk, "answer, file a pain as a build request, or both (JSON on stdin)"),
        ("decline", owed.decline, "leave a comment on Teeny's own post unanswered, with the reason (JSON on stdin)"),
        ("upvote", actions.upvote, "upvote a post or a comment (JSON on stdin)"),
        ("follow", actions.follow, "follow an agent (JSON on stdin)"),
        ("unfollow", actions.unfollow, "unfollow an agent (JSON on stdin)"),
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
    follows = verb("follows", lambda a, _: actions.stale_follows(a.days, a.max), "follows due for the weekly prune check")
    follows.add_argument("--days", type=_bounded(0, 365, "--days"), default=7)
    follows.add_argument("--max", type=_bounded(1, 100, "--max"), default=25)
    pruning = verb("prune", lambda a, _: actions.prune(a.days, a.max), "unfollow who does not follow back (operator)")
    pruning.add_argument("--days", type=_bounded(1, 365, "--days"), default=7)
    pruning.add_argument("--max", type=_bounded(1, 100, "--max"), default=25)
    describing = verb("describe", lambda a, _: actions.describe(a.description), "set the profile text (operator)")
    describing.add_argument("--description", required=True)
    verb("unlock", lambda a, _: actions.unlock(), "clear the verification lockout (operator)")
    collab = verb("collab", _collab, "collaboration consent on record (operator)")
    collab.add_argument("action", choices=("add", "list", "remove"))
    collab.add_argument("name", nargs="?", metavar="NAME")
    collab.add_argument("--note", default=None)
    return root


def main(argv: list[str]) -> int:
    return hand.run("moltbook", parser(), argv)
