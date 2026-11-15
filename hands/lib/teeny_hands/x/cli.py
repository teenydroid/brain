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
  A  post                   stdin {"text", "media_ids"?, "gif_id"?, "quote_of"?}
  A  reply --to ID          stdin {"text", "media_ids"?, "gif_id"?}
  A  like ID                skipped under DRY_RUN
  A  unsay ID [--confirm]   delete an own post; previews unless confirmed
  A  upload --file PATH     from state/media/ or brain/assets/ only
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
from . import cache, media, mentions, metrics, publish, twitr, webhook
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


def _post(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    return publish.post(body)


def _reply(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    return publish.reply(args.to, body)


def _like(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return publish.like(args.id)


def _unsay(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return publish.unsay(args.id, confirm=args.confirm)


def _upload(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return media.upload(args.file)


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
    verb("post", _post, "an own post or a quote (JSON on stdin)", body=True)
    verb("reply", _reply, "a reply (JSON on stdin)", body=True).add_argument("--to", required=True)
    verb("like", _like, "like a post").add_argument("id")
    unsay = verb("unsay", _unsay, "delete an own post")
    unsay.add_argument("id")
    unsay.add_argument("--confirm", action="store_true")
    verb("upload", _upload, "upload a file from state/media/ or brain/assets/").add_argument("--file", required=True)
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
