"""X's webhook: the CRC answer, the signature check, and which deliveries
become mentions in the queue. X is not called; the guard is stood in for."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from types import SimpleNamespace

from .support import T0, Scratch
from teeny_hands import hand
from teeny_hands.x import mentions, webhook

SECRET = "consumer-secret-FAKE"
ME = {"id": "1000", "username": "teenydroid"}


def signed(body: bytes) -> str:
    return "sha256=" + base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()


def event(kind: str, post_id: str, author: str, text: str, target: str = "1000", reply_to: str | None = "1000") -> dict:
    payload = {"id": post_id, "author_id": author, "text": text, "conversation_id": "500"}
    if reply_to:
        payload["in_reply_to_user_id"] = reply_to
    return {
        "data": {
            "event_type": kind,
            "filter": {"user_id": target},
            "payload": payload,
            "includes": {"users": [{"id": author, "username": f"user{author}"}]},
        }
    }


class WebhookTest(Scratch):
    def setUp(self):
        super().setUp()
        self.at(T0)
        self.receiver = webhook.Receiver(SECRET, ME)
        self.real_screen = mentions._screen
        mentions._screen = lambda post: SimpleNamespace(kind="allow", reason="", hold_id=None)
        self.addCleanup(setattr, mentions, "_screen", self.real_screen)

    def post(self, delivery, signature: str | None = None, path: str = "/webhooks/x"):
        body = json.dumps(delivery).encode()
        headers = {"x-twitter-webhooks-signature": signature if signature is not None else signed(body)}
        return self.receiver.handle("POST", path, headers, body)

    def queued(self) -> dict[str, str]:
        while not self.receiver.work.empty():
            webhook.queue_posts(self.receiver.work.get(), ME)
        rows = mentions._db().execute("SELECT id, status, author_username FROM x_mentions").fetchall()
        return {r["id"]: r["status"] for r in rows}

    def test_answers_the_crc_challenge(self):
        status, body = self.receiver.handle("GET", "/webhooks/x?crc_token=abc123", {}, b"")
        expected = "sha256=" + base64.b64encode(hmac.new(SECRET.encode(), b"abc123", hashlib.sha256).digest()).decode()
        self.assertEqual((status, body), (200, {"response_token": expected}))

    def test_answers_on_the_public_path_too(self):
        status, _ = self.receiver.handle("GET", "/api/webhooks/x?crc_token=t", {}, b"")
        self.assertEqual(status, 200)

    def test_refuses_a_delivery_with_a_bad_or_missing_signature(self):
        delivery = event("post.reply.create", "601", "7", "@teenydroid welcome back")
        self.assertEqual(self.post(delivery, signature="sha256=forged")[0], 401)
        body = json.dumps(delivery).encode()
        self.assertEqual(self.receiver.handle("POST", "/webhooks/x", {}, body)[0], 401)
        self.assertEqual(self.queued(), {})

    def test_a_reply_from_somebody_is_queued_as_a_mention(self):
        status, body = self.post(event("post.reply.create", "601", "7", "@teenydroid welcome back"))
        self.assertEqual((status, body["queued"]), (200, 1))
        self.assertEqual(self.queued(), {"601": "pending"})
        row = hand.db().execute("SELECT author_username, conversation_id FROM x_mentions").fetchone()
        self.assertEqual((row["author_username"], row["conversation_id"]), ("user7", "500"))

    def test_mentions_and_quotes_are_queued_too(self):
        self.post(event("post.mention.create", "602", "8", "hey @teenydroid", reply_to=None))
        self.post(event("post.quote.create", "603", "9", "look at this", reply_to=None))
        self.assertEqual(self.queued(), {"602": "pending", "603": "pending"})

    def test_the_accounts_own_posts_are_never_queued(self):
        self.post(event("post.reply.create", "604", "1000", "@user7 thanks fren"))
        self.assertEqual(self.queued(), {})

    def test_likes_follows_and_reposts_are_not_mentions(self):
        for kind in ("like.create", "follow.follow", "post.repost.create"):
            self.assertEqual(self.post(event(kind, "605", "7", "x"))[0], 200)
        self.assertEqual(self.queued(), {})

    def test_a_retweet_is_never_queued(self):
        self.post(event("post.mention.create", "606", "7", "RT @teenydroid: gm tiny humans", reply_to=None))
        self.assertEqual(self.queued(), {})

    def test_an_event_for_another_account_is_ignored(self):
        self.post(event("post.reply.create", "607", "7", "@someone hi", target="2000"))
        self.assertEqual(self.queued(), {})

    def test_a_post_the_timeline_already_queued_is_not_queued_again(self):
        self.post(event("post.reply.create", "608", "7", "@teenydroid hi"))
        self.queued()
        hand.db().execute("UPDATE x_mentions SET status = 'replied' WHERE id = '608'")
        self.post(event("post.reply.create", "608", "7", "@teenydroid hi"))
        self.assertEqual(self.queued(), {"608": "replied"})

    def test_the_older_account_activity_shape_is_read(self):
        delivery = {
            "for_user_id": "1000",
            "tweet_create_events": [
                {"id_str": "609", "text": "short", "extended_tweet": {"full_text": "@teenydroid the full text"},
                 "user": {"id_str": "7", "screen_name": "fren"}, "in_reply_to_user_id_str": "1000"},
                {"id_str": "610", "text": "RT @teenydroid: x", "retweeted_status": {}, "user": {"id_str": "8"}},
                {"id_str": "611", "text": "mine", "user": {"id_str": "1000"}},
            ],
        }
        self.post(delivery)
        self.assertEqual(self.queued(), {"609": "pending"})
        text = hand.db().execute("SELECT text FROM x_mentions WHERE id = '609'").fetchone()["text"]
        self.assertEqual(text, "@teenydroid the full text")

    def test_anything_else_is_not_found(self):
        self.assertEqual(self.receiver.handle("GET", "/health", {}, b"")[0], 404)
