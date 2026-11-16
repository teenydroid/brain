"""X's webhook: mentions, replies and quotes pushed to us as they happen.

The account's webhook (registered with X at
https://teeny.enamakel.com/api/webhooks/x, subscribed through the X Activity
API to `SUBSCRIBED_EVENTS` for the account: `hands/x subscribe`) is the cheap
way in: X pushes each event, so nothing is read or billed per post. The
mentions timeline (`hands/x ingest`) also leaves some replies out, which the
webhook does not.

What this does with a delivery:

- GET with `crc_token` (X's challenge, at registration and every so often):
  answers `sha256=` + base64(HMAC-SHA256(X_API_SECRET, token)).
- POST: refused unless `x-twitter-webhooks-signature` is that HMAC of the raw
  body. Then each post event about the account by somebody else becomes a row
  in `x_mentions`, `unscreened`, and the next `bin/mentions` run (in the
  spine) screens it with the inbound guard, offers it, and the reply flow
  answers it as it answers the timeline's mentions. A post already in the
  queue (the timeline saw it too) is left alone, so nothing is answered
  twice. The author's account age, follower count and verified badge, when
  the delivery carries them (`includes.users`), are kept on the row: a
  giveaway's filters read them instead of paying X for a user lookup.
- POST, `follow.follow` / `follow.unfollow` of the account by somebody else:
  one row per follower in `x_follows` (x/follows.py), so "follows
  @teenydroid" is a local lookup too.

It screens nothing itself: it runs in the `xhook` container, which has its
own network namespace, and the core (and so the safety judge) listens only in
the spine's. Asking from here held every mention as "the judge could not be
reached" (2026-11-11 to 13) until the guard's re-screen released it.

It answers X at once and queues on a worker thread: X retries a delivery it
does not see acknowledged quickly.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import queue
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from .. import hand
from . import cache, follows, mentions, model
from .client import XApi

PATHS = ("/webhooks/x", "/api/webhooks/x")
#: The X Activity API events that are somebody's post about the account.
POST_EVENTS = ("post.mention.create", "post.reply.create", "post.quote.create")
#: Everything the account's subscriptions ask X for (`hands/x subscribe`):
#: the posts above, and somebody following or unfollowing the account.
#: docs.x.com/x-api/activity/introduction, "Follow events".
SUBSCRIBED_EVENTS = POST_EVENTS + follows.EVENTS
MAX_BODY = 1024 * 1024

Json = dict[str, Any]


def _log(message: str) -> None:
    print(f"[x-webhook] {message}", file=sys.stderr, flush=True)


def signature(secret: str, data: bytes) -> str:
    return "sha256=" + base64.b64encode(hmac.new(secret.encode(), data, hashlib.sha256).digest()).decode()


def crc_response(secret: str, token: str) -> Json:
    return {"response_token": signature(secret, token.encode())}


def valid_signature(secret: str, body: bytes, header: str | None) -> bool:
    return bool(header) and hmac.compare_digest(signature(secret, body), header)


def _is_retweet(text: str) -> bool:
    return text.startswith("RT @")


def _iso(stamp: Any) -> str | None:
    """A v1.1 `created_at` ("Wed Oct 10 20:19:24 +0000 2018") as ISO 8601,
    the v2 shape; anything else as it came."""
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.strptime(stamp, "%a %b %d %H:%M:%S %z %Y").astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except ValueError:
        return stamp


def _author_fields(user: Mapping[str, Any], followers: Any, created_at: Any) -> Json:
    """What a delivery says about a post's author that a giveaway's filters
    want: `authorCreatedAt`, `authorFollowers`, `authorVerified`. Absent
    when the delivery did not carry it."""
    out: Json = {}
    if isinstance(created_at, str) and created_at:
        out["authorCreatedAt"] = created_at
    if isinstance(followers, int) and not isinstance(followers, bool):
        out["authorFollowers"] = followers
    if isinstance(user.get("verified"), bool):
        out["authorVerified"] = user["verified"]
    return out


def _v2_authors(includes: Mapping[str, Any] | None) -> dict[str, Json]:
    authors: dict[str, Json] = {}
    for user in (includes or {}).get("users") or []:
        if isinstance(user, Mapping) and user.get("id"):
            metrics = user.get("public_metrics") if isinstance(user.get("public_metrics"), Mapping) else {}
            authors[str(user["id"])] = _author_fields(user, metrics.get("followers_count"), user.get("created_at"))
    return authors


def posts_from_delivery(delivery: Any, me_id: str) -> list[Json]:
    """The posts in one delivery that are somebody else's mention, reply or
    quote of the account. The X Activity API sends one event per request
    (`{data: {event_type, filter, payload, includes}}`); the older Account
    Activity shape (`tweet_create_events`, v1.1 tweets) is read too."""
    if not isinstance(delivery, Mapping):
        return []
    out: list[Json] = []
    data = delivery.get("data")
    if isinstance(data, Mapping) and isinstance(data.get("event_type"), str):
        if data["event_type"] not in POST_EVENTS:
            return []
        target = (data.get("filter") or {}).get("user_id") if isinstance(data.get("filter"), Mapping) else None
        if target is not None and str(target) != me_id:
            return []
        payload = data.get("payload")
        includes = data.get("includes") if isinstance(data.get("includes"), Mapping) else None
        if isinstance(payload, Mapping):
            out = model.to_posts([payload], includes)
            authors = _v2_authors(includes)
            for post in out:
                post.update(authors.get(str(post.get("authorId")), {}))
    elif isinstance(delivery.get("tweet_create_events"), list):
        if delivery.get("for_user_id") is not None and str(delivery["for_user_id"]) != me_id:
            return []
        for tweet in delivery["tweet_create_events"]:
            if not isinstance(tweet, Mapping) or tweet.get("retweeted_status"):
                continue
            user = tweet.get("user") if isinstance(tweet.get("user"), Mapping) else {}
            text = (tweet.get("extended_tweet") or {}).get("full_text") if isinstance(tweet.get("extended_tweet"), Mapping) else None
            text = text or tweet.get("text")
            if not tweet.get("id_str") or not isinstance(text, str):
                continue
            post: Json = {"id": str(tweet["id_str"]), "text": text}
            for key, value in (
                ("authorId", user.get("id_str")),
                ("authorUsername", user.get("screen_name")),
                ("createdAt", tweet.get("created_at")),
                ("inReplyToUserId", tweet.get("in_reply_to_user_id_str")),
            ):
                if value is not None:
                    post[key] = str(value)
            post.update(_author_fields(user, user.get("followers_count"), _iso(user.get("created_at"))))
            out.append(post)
    return [p for p in out if p.get("authorId") != me_id and not _is_retweet(p.get("text", ""))]


def _identity() -> Mapping[str, Any]:
    known = hand.setting("X_BOT_USER_ID")
    username = hand.setting("X_BOT_USERNAME") or ""
    if known:
        return {"id": known, "username": username}
    return cache.reads(XApi.from_settings()).identity()


def queue_posts(posts: list[Json], me: Mapping[str, Any]) -> dict[str, int]:
    """Each post into `x_mentions`, once: `unscreened` (or skipped as own or
    answered). Nothing is screened here: this runs in the xhook container,
    where the core, and so the safety judge, is out of reach. The next
    ingest, in the spine, screens and offers them.

    It takes no lock: a row is one INSERT OR IGNORE on the post id, so it
    cannot race the ingest into a second row, and an ingest judging under
    its lock must not make X's delivery wait (and be dropped)."""
    counts = {status: 0 for status in mentions.STATUSES}
    cache.remember(posts)
    conn = mentions._db()
    try:
        now = hand.now()
        for post in sorted(posts, key=lambda p: int(p["id"])):
            mentions._store(conn, me, post, counts, now, screen=False)
    finally:
        conn.close()
    return {k: v for k, v in counts.items() if v}


#: The tag every subscription `subscribe` makes carries back on its events.
SUBSCRIPTION_TAG = "teeny"


def subscription_requests(me_id: str, webhook_id: str) -> list[Json]:
    """The `POST /2/activity/subscriptions` bodies for `SUBSCRIBED_EVENTS`.
    Follow events ask only for the `inbound` direction: somebody following
    the account, not the account following somebody."""
    out = []
    for event_type in SUBSCRIBED_EVENTS:
        target: Json = {"user_id": me_id}
        if event_type in follows.EVENTS:
            target["qualifiers"] = {"direction": "inbound"}
        out.append({"event_type": event_type, "filter": target, "webhook_id": webhook_id, "tag": SUBSCRIPTION_TAG})
    return out


def subscribe(webhook_id: str, confirm: bool = False, x: XApi | None = None) -> Json:
    """Subscribes the account's webhook to every event in
    `SUBSCRIBED_EVENTS`. Without `confirm` it only shows what it would ask
    for. X treats a subscription it already has as a refresh, so running it
    again (after adding an event type here) is safe. Signed as the account
    (OAuth 1.0a): mentions, replies and quotes are private events."""
    if not webhook_id.strip():
        raise hand.HandError("--webhook-id is required (X's id for the registered webhook)")
    me_id = str(_identity()["id"])
    requests = subscription_requests(me_id, webhook_id.strip())
    if not confirm:
        return {"preview": requests, "confirm": "run again with --confirm to create them"}
    x = x or XApi.from_settings()
    results = []
    for body in requests:
        try:
            response = x.call("POST", "/2/activity/subscriptions", body=body, user=True)
            results.append({"event_type": body["event_type"], "ok": True, "data": response.get("data")})
        except hand.HandError as exc:
            results.append({"event_type": body["event_type"], "ok": False, "error": str(exc)})
    return {"subscriptions": results}


class Receiver:
    """The HTTP side and the worker that queues what arrives."""

    def __init__(self, secret: str, me: Mapping[str, Any]) -> None:
        self.secret = secret
        self.me = me
        self.work: queue.Queue = queue.Queue()

    def handle(self, method: str, url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, Json]:
        parsed = urlparse(url)
        if parsed.path == "/health" and method == "GET":
            return 200, {"ok": True}
        if parsed.path not in PATHS:
            return 404, {"error": "not found"}
        if method == "GET":
            token = (parse_qs(parsed.query).get("crc_token") or [""])[0]
            if not token:
                return 400, {"error": "crc_token required"}
            _log("answered a CRC challenge")
            return 200, crc_response(self.secret, token)
        if method != "POST":
            return 405, {"error": "method not allowed"}
        if not valid_signature(self.secret, body, headers.get("x-twitter-webhooks-signature")):
            _log("refused a delivery with a bad signature")
            return 401, {"error": "bad signature"}
        try:
            delivery = json.loads(body)
        except ValueError:
            return 400, {"error": "not JSON"}
        me_id = str(self.me["id"])
        posts = posts_from_delivery(delivery, me_id)
        if posts:
            self.work.put(("posts", posts))
        followed = follows.from_delivery(delivery, me_id)
        if followed:
            self.work.put(("follows", followed))
        answer: Json = {"ok": True, "queued": len(posts)}
        if followed:
            answer["follows"] = len(followed)
        return 200, answer

    def apply(self, item: tuple[str, list[Json]]) -> str:
        """One unit of work off the queue, stored; what it did, for the log."""
        kind, items = item
        if kind == "follows":
            return f"follows {follows.record(items)} from {[e['userId'] for e in items]}"
        return f"queued {queue_posts(items, self.me)} from {[p['id'] for p in items]}"

    def drain(self) -> None:
        while True:
            item = self.work.get()
            try:
                _log(self.apply(item))
            except Exception as exc:  # noqa: BLE001 - a bad batch must not stop the worker
                _log(f"could not store {item[0]}: {type(exc).__name__}: {exc}")
            finally:
                self.work.task_done()


def serve(host: str, port: int) -> None:
    secret = hand.require("X_API_SECRET")
    receiver = Receiver(secret, _identity())
    threading.Thread(target=receiver.drain, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _answer(self, method: str) -> None:
            length = int(self.headers.get("content-length") or 0)
            if length > MAX_BODY:
                status, body = 413, {"error": "too large"}
            else:
                raw = self.rfile.read(length) if length else b""
                headers = {k.lower(): v for k, v in self.headers.items()}
                status, body = receiver.handle(method, self.path, headers, raw)
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            self._answer("GET")

        def do_POST(self) -> None:
            self._answer("POST")

    _log(f"listening on {host}:{port} for @{receiver.me.get('username') or receiver.me['id']}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
