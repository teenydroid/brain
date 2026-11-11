"""X's webhook: mentions, replies and quotes pushed to us as they happen.

The account's webhook (registered with X at
https://teeny.enamakel.com/api/webhooks/x, subscribed through the X Activity
API to post.mention.create, post.reply.create and post.quote.create for the
account) is the cheap way in: X pushes each event, so nothing is read or
billed per post. The mentions timeline (`hands/x ingest`) also leaves some
replies out, which the webhook does not.

What this does with a delivery:

- GET with `crc_token` (X's challenge, at registration and every so often):
  answers `sha256=` + base64(HMAC-SHA256(X_API_SECRET, token)).
- POST: refused unless `x-twitter-webhooks-signature` is that HMAC of the raw
  body. Then each post event about the account by somebody else becomes a row
  in `x_mentions`, screened like any mention (mentions._store), and the next
  `bin/mentions` run offers it to the core and the reply flow answers it as
  it answers the timeline's mentions. A post already in the queue (the
  timeline saw it too) is left alone, so nothing is answered twice.

It answers X at once and does the screening on a worker thread: X retries a
delivery it does not see acknowledged quickly.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from .. import hand
from . import cache, mentions, model
from .client import XApi

PATHS = ("/webhooks/x", "/api/webhooks/x")
#: The X Activity API events that are somebody's post about the account.
POST_EVENTS = ("post.mention.create", "post.reply.create", "post.quote.create")
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
            out.append(post)
    return [p for p in out if p.get("authorId") != me_id and not _is_retweet(p.get("text", ""))]


def _identity() -> Mapping[str, Any]:
    known = hand.setting("X_BOT_USER_ID")
    username = hand.setting("X_BOT_USERNAME") or ""
    if known:
        return {"id": known, "username": username}
    return cache.reads(XApi.from_settings()).identity()


def queue_posts(posts: list[Json], me: Mapping[str, Any]) -> dict[str, int]:
    """Each post into `x_mentions` as the timeline's mentions go (screened,
    or skipped as own or answered), once; the next ingest offers them."""
    counts = {status: 0 for status in mentions.STATUSES}
    cache.remember(posts)
    with hand.lock("x-ingest", timeout=60):
        conn = mentions._db()
        try:
            now = hand.now()
            for post in sorted(posts, key=lambda p: int(p["id"])):
                mentions._store(conn, me, post, counts, now)
        finally:
            conn.close()
    return {k: v for k, v in counts.items() if v}


class Receiver:
    """The HTTP side and the worker that screens what arrives."""

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
        posts = posts_from_delivery(delivery, str(self.me["id"]))
        if posts:
            self.work.put(posts)
        return 200, {"ok": True, "queued": len(posts)}

    def drain(self) -> None:
        while True:
            posts = self.work.get()
            try:
                _log(f"queued {queue_posts(posts, self.me)} from {[p['id'] for p in posts]}")
            except Exception as exc:  # noqa: BLE001 - a bad batch must not stop the worker
                _log(f"could not queue {[p.get('id') for p in posts]}: {type(exc).__name__}: {exc}")
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
