"""The verbs of hands/x (docs/hands.md). Reads (R) go through the read cache
and are safe to expose to a drafting agent; acting verbs (A) only go through
`publish` and `media`, which run the guard and the posting rules themselves.

  R  get ID                 one post
  R  thread ID [--max N]    the post and its conversation
  R  user HANDLE            an account by its handle
  R  users --ids A,B,...    up to 100 accounts by id: {id, username, createdAt, followers, followsMe}; kept 24h
  R  search QUERY [--max N] a recent search
  R  whoami                 the account's own id and username
  R  links                  today's link posts against X_LINK_POSTS_MAX_PER_DAY: {cap, sent_today, left}
  R  slot [--quote]         whether an original post (a quote, with --quote) would pass its lane's gap and the
                            day's cap now: {open: true} or {why}
  R  peers [--max-age-hours H] [--per-peer N] [--skip-quoted] [--reuse-hours H]
                            the newest original posts of the accounts in brain/x-peers.txt, screened; with
                            --reuse-hours, a search younger than H hours is answered from its kept posts
  R  peers trending [--max-age-hours H] [--stories N] [--per-story N] [--skip-quoted]
                            the X posts behind today's top-ranked AI and tech stories (hands/trend top), screened;
                            no X read: ids, handles and texts are Digg's
  A  peer-suggest           stdin {"candidates": [{quote_of, text, relevance, why, source?, format}], "media_ids"?,
                            "meme"?}: all kept, the top one quoted only when it clears the bar (x/peers.py)
  R  peers suggestions [--days N]  what was suggested, posted and skipped (7 days)
  R  peers report [--days N]       per peer: touches, who answered, how the quote did (14 days)
  A  post                   stdin {"text", "anchor", "media_ids"?, "gif_id"?, "quote_of"?}; anchor defaults to quote_of
  A  post-thread            stdin {"posts": [2 to 5 texts], "anchor", "media_ids"?, "gif_id"?, "format"?, "experiment"?}; one own post, each later text a reply to the one before (not `thread`, the read above)
                            Both also take the producer's tags, kept in x_posts and never published: "producer"
                            (studio, brief, peer-quote or legacy), "storyline", "shape", "pitch_id"
  A  reply --to ID          stdin {"text", "media_ids"?, "gif_id"?}
  A  follow-up [--to ID]    stdin {"text", "to"?, ...}: continue an own recorded post as a thread
  A  like ID                skipped under DRY_RUN
  A  unsay ID [--confirm]   delete an own post; previews unless confirmed
  A  upload --file PATH [--alt TEXT]  from state/media/ or brain/assets/ only; a still under 5 MB; alt text for X
  J  ingest [--max N] [--no-offer]   new mentions into the queue, offered to the core
  J  mentions [--status S]  the mentions queue
  J  mark ID --status S [--note T]   what became of a mention after its draft
  J  requests [--max N]     asks to build something in the stored mentions, into build_requests
  J  metrics refresh        own posts and replies due a checkpoint (1h, 24h, 72h) into x_post_metrics
  R  metrics list [--days N]  the latest row per own post measured in the last N days (7)
  O  twitr-connect HANDLE   start linking HANDLE to the wallet on twitr.sh: a link for the operator
  O  twitr-accounts         the X accounts linked to the wallet on twitr.sh
  O  subscribe --webhook-id ID [--confirm]   subscribe the webhook to the X Activity events it reads

Job verbs (J) keep the mentions queue. They answer nobody, but they write
state and call the core, so no drafting agent is given them. Operator verbs
(O) are run by a person once, when setting twitr.sh or the webhook up
(docs/hands.md).
"""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from .. import hand
from . import cache, media, mentions, metrics, peers, publish, twitr, webhook
from .client import MAX_USER_IDS, number_setting
from .model import SearchOptions


def _get(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return {"post": cache.reads().get_post(args.id)}


def _thread(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    reads = cache.reads()
    post = reads.get_post(args.id)
    if post is None:
        return {"post": None, "conversationId": None, "posts": []}
    conversation_id = post.get("conversationId") or args.id
    return {"post": post, "conversationId": conversation_id, "posts": reads.get_conversation(conversation_id, args.max)}


def _user(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return {"user": cache.reads().user_by_username(args.handle)}


def _users(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    users = cache.reads().users_by_ids(args.ids)
    returned = {u["id"] for u in users}
    return {"users": users, "missing": [i for i in args.ids if i not in returned]}


def _ids(value: str) -> list[str]:
    ids = list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    if not ids:
        raise argparse.ArgumentTypeError("--ids needs at least one id")
    if len(ids) > MAX_USER_IDS:
        raise argparse.ArgumentTypeError(f"--ids takes at most {MAX_USER_IDS} ids")
    if not all(i.isdigit() for i in ids):
        raise argparse.ArgumentTypeError("--ids are X user ids (digits), comma-separated")
    return ids


def _subscribe(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return webhook.subscribe(args.webhook_id, confirm=args.confirm)


def _search(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    # A cost dial: X bills every post a search returns.
    wanted = args.max if args.max is not None else int(number_setting("X_SEARCH_MAX_RESULTS", 10, 10, 100))
    return {"posts": cache.reads().search_posts(args.query, SearchOptions(max_results=wanted))}


def _whoami(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return cache.reads().identity()


def _links(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return publish.link_budget()


def _slot(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return publish.slot(quote=args.quote)


def _post(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    return publish.post(body)


def _post_thread(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    return publish.thread(body)


def _reply(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    return publish.reply(args.to, body)


def _follow_up(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    # bin/act pipes a playbook's draft with no flags, so the post may be named
    # in the body instead; publish.follow_up still refuses all but own posts.
    to = args.to if args.to is not None else (body.get("to") if isinstance(body, Mapping) else None)
    if to is None:
        raise hand.HandError("follow-up needs --to or a \"to\" in the body")
    body = {k: v for k, v in body.items() if k != "to"} if isinstance(body, Mapping) else body
    return publish.follow_up(str(to), body)


def _like(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return publish.like(args.id)


def _unsay(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return publish.unsay(args.id, confirm=args.confirm)


def _upload(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return media.upload(args.file, alt=args.alt)


def _ingest(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return mentions.ingest(args.max, offer=not args.no_offer)


def _mentions(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return mentions.mentions_list(args.status, args.limit)


def _mark(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return mentions.mark(args.id, args.status, args.note)


def _requests(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return mentions.note_requests(args.max)


def _metrics(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return metrics.refresh() if args.action == "refresh" else metrics.listing(args.days)


def _peers(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return peers.read(args.max_age_hours, args.per_peer, skip_quoted=args.skip_quoted, reuse_hours=args.reuse_hours)


def _peers_suggest(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    return peers.suggest(body)


def _peers_suggestions(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return peers.suggestions(args.days)


def _peers_report(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return peers.report(args.days)


def _peers_trending(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return peers.trending(args.trend_max_age_hours, args.stories, args.per_story, skip_quoted=args.trend_skip_quoted)


def _stories(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 10:
        raise argparse.ArgumentTypeError("--stories is 1..10")
    return number


def _hours(value: str) -> float:
    number = float(value)
    if not 1 <= number <= 168:
        raise argparse.ArgumentTypeError("--max-age-hours is 1..168 (X searches the last 7 days)")
    return number


def _reuse(value: str) -> float:
    number = float(value)
    if not 0 <= number <= 24:
        raise argparse.ArgumentTypeError("--reuse-hours is 0..24")
    return number


def _per_peer(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 5:
        raise argparse.ArgumentTypeError("--per-peer is 1..5")
    return number


def _days(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 30:
        raise argparse.ArgumentTypeError("--days is 1..30")
    return number


def _twitr_connect(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return twitr.TwitrApi.from_settings().connect(args.handle)


def _twitr_accounts(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return twitr.TwitrApi.from_settings().accounts()


def _max(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError("--max is 1..100")
    return number


def _bounded_scan(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 1000:
        raise argparse.ArgumentTypeError("--max is 1..1000")
    return number


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/x", description="The only holder of the X keys.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    verb("get", _get, "one post").add_argument("id")
    thread = verb("thread", _thread, "a post and its conversation")
    thread.add_argument("id")
    thread.add_argument("--max", type=_max, default=None)
    verb("user", _user, "an account by its handle").add_argument("handle")
    verb("users", _users, "up to 100 accounts by id, kept a day").add_argument("--ids", type=_ids, required=True)
    search = verb("search", _search, "a recent search")
    search.add_argument("query")
    search.add_argument("--max", type=_max, default=None)
    verb("whoami", _whoami, "the account's own id and username")
    verb("links", _links, "today's link posts against the daily cap (reads the ledger only)")
    verb("slot", _slot, "whether an original post (or a quote) would pass its lane's gap and cap now (reads the ledger only)"
         ).add_argument("--quote", action="store_true", help="the quote lane, not the original posts'")
    reading = verb("peers", _peers, "the newest posts of the accounts in brain/x-peers.txt")
    reading.add_argument("--max-age-hours", type=_hours, default=8.0)
    reading.add_argument("--per-peer", type=_per_peer, default=2)
    reading.add_argument("--skip-quoted", action="store_true", help="leave out peers quoted in the last 7 days")
    reading.add_argument("--reuse-hours", type=_reuse, default=0.0,
                         help="answer from a peers search younger than this (hours) instead of searching again")
    peer_verbs = reading.add_subparsers(dest="peers_verb")
    listed = peer_verbs.add_parser("suggestions", help="what was suggested, posted and skipped")
    listed.add_argument("--days", type=_days, default=7)
    listed.set_defaults(handler=_peers_suggestions, takes_body=False)
    reported = peer_verbs.add_parser("report", help="per peer: touches, who answered, how the quote did")
    reported.add_argument("--days", type=_days, default=14)
    reported.set_defaults(handler=_peers_report, takes_body=False)
    trend = peer_verbs.add_parser("trending", help="the X posts behind today's top-ranked AI and tech stories")
    trend.add_argument("--max-age-hours", dest="trend_max_age_hours", type=_hours, default=peers.TREND_MAX_AGE_HOURS)
    trend.add_argument("--stories", type=_stories, default=peers.TREND_STORIES)
    trend.add_argument("--per-story", type=_per_peer, default=peers.TREND_POSTS_PER_STORY)
    trend.add_argument("--skip-quoted", dest="trend_skip_quoted", action="store_true",
                       help="leave out authors quoted in the last 7 days")
    trend.set_defaults(handler=_peers_trending, takes_body=False)
    # One word, as a playbook's `hand:x <verb>` output names it (peer-quote).
    verb("peer-suggest", _peers_suggest, "up to 3 scored quote suggestions; the top one posted only past the bar",
         body=True)
    verb("post", _post, "an own post or a quote (JSON on stdin)", body=True)
    verb("post-thread", _post_thread, "a thread of 2 to 5 posts, one own post (JSON on stdin)", body=True)
    verb("reply", _reply, "a reply (JSON on stdin)", body=True).add_argument("--to", required=True)
    verb("follow-up", _follow_up, "continue an own post as a thread (JSON on stdin)", body=True).add_argument("--to")
    verb("like", _like, "like a post").add_argument("id")
    unsay = verb("unsay", _unsay, "delete an own post")
    unsay.add_argument("id")
    unsay.add_argument("--confirm", action="store_true")
    uploading = verb("upload", _upload, "upload a file from state/media/ or brain/assets/")
    uploading.add_argument("--file", required=True)
    uploading.add_argument("--alt", default=None, help="alt text: what the picture shows (at most 1000 characters)")
    ingest = verb("ingest", _ingest, "new mentions into the queue, offered to the core")
    ingest.add_argument("--max", type=_max, default=None)
    ingest.add_argument("--no-offer", action="store_true", help="queue only; offer on a later run")
    listing = verb("mentions", _mentions, "the mentions queue, newest first")
    listing.add_argument("--status", choices=mentions.STATUSES, default=None)
    listing.add_argument("--limit", type=_max, default=50)
    marking = verb("mark", _mark, "what became of a mention after its draft")
    marking.add_argument("id")
    marking.add_argument("--status", required=True, choices=mentions.MARKS)
    marking.add_argument("--note", default=None)
    noting = verb("requests", _requests, "asks to build something in the stored mentions, into build_requests")
    noting.add_argument("--max", type=_bounded_scan, default=200)
    measuring = verb("metrics", _metrics, "how the account's own posts and replies landed")
    measuring.add_argument("action", choices=("refresh", "list"))
    measuring.add_argument("--days", type=_days, default=7)
    verb("twitr-connect", _twitr_connect, "start linking an X account to the wallet on twitr.sh").add_argument("handle")
    verb("twitr-accounts", _twitr_accounts, "the X accounts linked to the wallet on twitr.sh")
    subscribing = verb("subscribe", _subscribe, "subscribe the webhook to the X Activity events it reads")
    subscribing.add_argument("--webhook-id", required=True)
    subscribing.add_argument("--confirm", action="store_true")
    return root


def main(argv: list[str]) -> int:
    return hand.run("x", parser(), argv)
