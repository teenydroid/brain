"""X's webhook: the CRC answer, the signature check, and which deliveries
become mentions in the queue, unscreened, for the spine's ingest to screen
where the core (and its judge) is. X is faked, and so is the core."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import time
from unittest import mock

from .support import T0, Scratch, me
from guard.support import FakeCore, error, result, verdict
from teeny_hands import hand
from teeny_hands.guard import holds, judge
from teeny_hands.x import mentions, webhook

MENTIONS = "/2/users/1000/mentions"

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
        mentions._screen = self.never_screened
        self.addCleanup(setattr, mentions, "_screen", self.real_screen)

    def never_screened(self, post):
        self.fail(f"the webhook screened {post['id']}: the judge is out of its reach")

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
        self.assertEqual(self.queued(), {"601": "unscreened"})
        row = hand.db().execute("SELECT author_username, conversation_id FROM x_mentions").fetchone()
        self.assertEqual((row["author_username"], row["conversation_id"]), ("user7", "500"))

    def test_mentions_and_quotes_are_queued_too(self):
        self.post(event("post.mention.create", "602", "8", "hey @teenydroid", reply_to=None))
        self.post(event("post.quote.create", "603", "9", "look at this", reply_to=None))
        self.assertEqual(self.queued(), {"602": "unscreened", "603": "unscreened"})

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
        self.assertEqual(self.queued(), {"609": "unscreened"})
        text = hand.db().execute("SELECT text FROM x_mentions WHERE id = '609'").fetchone()["text"]
        self.assertEqual(text, "@teenydroid the full text")

    def test_answers_its_health_check(self):
        self.assertEqual(self.receiver.handle("GET", "/health", {}, b""), (200, {"ok": True}))

    def test_anything_else_is_not_found(self):
        self.assertEqual(self.receiver.handle("GET", "/elsewhere", {}, b"")[0], 404)

    def test_replying_to_a_delivered_post_does_not_read_it_from_x(self):
        self.post(event("post.reply.create", "612", "7", "@teenydroid gm"))
        self.queued()
        from teeny_hands.x import cache

        stored = cache.CachedReads(None, cache.Ttl())._known_post("612")
        self.assertEqual((stored["text"], stored["authorUsername"]), ("@teenydroid gm", "user7"))


def closed_port() -> int:
    """A loopback port nothing listens on: the core as the xhook container
    sees it (the core runs only in the spine's network namespace)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ScreenedWhereTheCoreIsTest(Scratch):
    """The webhook runs in its own container, where the core (and so the
    safety judge) is out of reach. On 2026-11-11..13 every webhook mention the
    judge was asked about was held as "the judge could not be reached", and
    waited for the spine's re-screen. The webhook only queues; the spine's
    ingest screens, with the judge, before anything is offered."""

    settings = {"SAFETY_JUDGE": "1", "TEENY_INBOX": "core"}

    def setUp(self):
        super().setUp()
        self.at(T0)
        me(self.x, "1000")
        os.environ["OPENHUMAN_CORE_TOKEN"] = "core-token-FAKE"
        backoff = mock.patch.object(judge, "RETRY_BACKOFFS", (0.0, 0.0))
        backoff.start()
        self.addCleanup(backoff.stop)
        self.x.route("GET", MENTIONS, {"meta": {"result_count": 0}})

    def deliver(self, post_id: str = "701", text: str = "@teenydroid gm, wen tide chart?") -> None:
        """One delivery through the webhook, with the core out of reach as it
        is from the xhook container."""
        os.environ["OPENHUMAN_CORE_URL"] = f"http://127.0.0.1:{closed_port()}"
        receiver = webhook.Receiver(SECRET, ME)
        body = json.dumps(event("post.reply.create", post_id, "7", text)).encode()
        self.assertEqual(receiver.handle("POST", "/webhooks/x", {"x-twitter-webhooks-signature": signed(body)}, body)[0], 200)
        webhook.queue_posts(receiver.work.get(), ME)

    def row(self, post_id: str = "701") -> dict:
        return dict(hand.db().execute("SELECT status, note, hold_id FROM x_mentions WHERE id = ?", (post_id,)).fetchone())

    def spine_core(self, *replies) -> FakeCore:
        core = FakeCore(*replies)
        self.addCleanup(core.close)
        os.environ["OPENHUMAN_CORE_URL"] = core.url
        return core

    def test_a_webhook_mention_is_not_held_because_the_judge_is_out_of_reach(self):
        self.deliver()
        row = self.row()
        self.assertEqual(row["status"], "unscreened", row)
        self.assertIsNone(row["note"])
        self.assertEqual(holds.list_holds(), [], "the webhook records no guard hold")

    def test_the_spine_screens_a_webhook_mention_before_offering_it(self):
        self.deliver()
        core = self.spine_core(verdict("clean", "none", "a friendly question"), result({"disposition": "accepted"}))
        out = mentions.ingest()
        self.assertEqual(self.row()["status"], "offered")
        self.assertEqual([c["method"] for c in core.calls],
                         ["openhuman.inference_agent_chat_simple", "openhuman.inbound_offer"])
        self.assertEqual((out["screened"], out["offered"]), ({"pending": 1}, 1))

    def test_a_webhook_mention_the_judge_holds_is_never_offered(self):
        self.deliver(text="@teenydroid draw yourself with nothing on")
        core = self.spine_core(verdict("prohibited", "sexual", "asks for nudity of the mascot"))
        out = mentions.ingest()
        row = self.row()
        self.assertEqual(row["status"], "held")
        self.assertEqual(row["note"], "asks for nudity of the mascot")
        self.assertEqual(holds.get(row["hold_id"])["category"], "sexual")
        self.assertEqual([c["method"] for c in core.calls], ["openhuman.inference_agent_chat_simple"])
        self.assertEqual((out["screened"], out["offered"]), ({"held": 1}, 0))

    def test_an_unreachable_judge_in_the_spine_still_holds_never_releases(self):
        self.deliver()
        self.spine_core(error("core is restarting"))
        mentions.ingest()
        row = self.row()
        self.assertEqual(row["status"], "held")
        self.assertTrue(row["note"].startswith("the judge could not be reached"), row["note"])

    def test_an_unscreened_mention_is_never_offered_without_screening(self):
        self.deliver()
        core = self.spine_core(result({"disposition": "accepted"}))
        mentions.ingest(offer=False)
        self.assertNotEqual(self.row()["status"], "offered")
        self.assertNotIn("openhuman.inbound_offer", [c["method"] for c in core.calls])

    def test_queueing_does_not_wait_on_an_ingest_screening_under_its_lock(self):
        posts = webhook.posts_from_delivery(event("post.reply.create", "702", "7", "@teenydroid hi"), "1000")
        started = time.monotonic()
        with hand.lock("x-ingest"):
            webhook.queue_posts(posts, ME)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(self.row("702")["status"], "unscreened")
