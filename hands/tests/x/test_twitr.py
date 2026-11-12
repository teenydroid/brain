"""twitr.sh behind hands/x: every call one `openhuman.x402_fetch` under a cap,
reads that fall back to X, writes that never do, and the shapes mapped to
the hand's own. The core is a stand-in; nothing reaches twitr.sh or pays."""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

from .support import T0, Guard, Scratch, me
from teeny_hands import hand
from teeny_hands.x import cache, publish, twitr
from teeny_hands.x.client import XApi
from teeny_hands.x.model import SearchOptions


class FakeCore:
    """`hand.core` for x402_fetch: answers queued (status, body) pairs, or
    raises a queued CoreError, and records every call's params."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.answers: list[Any] = []

    def answer(self, status: int, body: Any, **extra: Any) -> None:
        self.answers.append({"status": status, "body": json.dumps(body), "truncated": False, "payment": None, **extra})

    def fail(self, message: str) -> None:
        self.answers.append(hand.CoreError(message))

    def __call__(self, method: str, params: Any = None, timeout: float = 0) -> Any:
        self.calls.append((method, dict(params or {})))
        if not self.answers:
            raise AssertionError(f"unexpected core call {method}")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def params(self) -> dict:
        return self.calls[-1][1]

    def body(self) -> dict:
        return json.loads(self.params["body"])


class Twitr(Scratch):
    settings = {"X_TWITR_READS": "1", "X_TWITR_WRITES": "1", "TWITR_ACCOUNT": "teenydroid", "TWITR_API_BASE": "https://twitr.test"}

    def setUp(self):
        super().setUp()
        self.core = FakeCore()
        patcher = mock.patch.object(hand, "core", self.core)
        patcher.start()
        self.addCleanup(patcher.stop)


class ReadsTest(Twitr):
    def test_a_post_is_one_capped_fetch_pinned_to_base(self):
        self.core.answer(200, {"items": [{"id": "77", "text": "hello", "authorUsername": "someone", "likeCount": 3, "retweetCount": 1, "createdAt": "2026-11-01T00:00:00Z"}]})
        post = twitr.client().get_post("77")
        self.assertEqual(post["authorUsername"], "someone")
        self.assertEqual(post["metrics"], {"id": "77", "likes": 3, "replies": 0, "reposts": 1, "quotes": 0})
        method, params = self.core.calls[0]
        self.assertEqual(method, "x402_fetch")
        self.assertEqual(params["url"], "https://twitr.test/api/tools/x_read")
        self.assertEqual(params["method"], "POST")
        self.assertEqual(params["maxAmountAtomic"], twitr.FLOOR)
        self.assertEqual(params["network"], "eip155:8453")
        self.assertEqual(self.core.body(), {"resource": "get-tweet", "id": "77"})
        self.assertEqual(self.x.requests(), [], "X is not asked when twitr answers")

    def test_a_search_caps_the_payment_at_its_results_limit(self):
        self.core.answer(200, {"items": [{"id": "601", "text": "b"}, {"id": "600", "text": "a"}]})
        page = twitr.client().search_page("teeny", SearchOptions(max_results=20))
        self.assertEqual([p["id"] for p in page["posts"]], ["601", "600"])
        self.assertEqual(page["newestId"], "601")
        self.assertEqual(self.core.body()["resultsLimit"], 20)
        self.assertEqual(self.core.params["maxAmountAtomic"], 20 * twitr.PER_RECORD)

    def test_since_id_keeps_only_newer_posts(self):
        self.core.answer(200, {"items": [{"id": "9", "text": "old"}, {"id": "12", "text": "new"}]})
        posts = twitr.client().get_conversation("4", since_id="10")
        self.assertEqual([p["id"] for p in posts], ["12"])
        self.assertEqual(self.core.body()["q"], "conversation_id:4")

    def test_a_call_over_the_per_call_cap_is_refused_before_the_core_and_x_answers(self):
        self.configure(TWITR_MAX_PER_CALL_USD="0.01")
        self.x.route("GET", "/2/tweets/search/recent", {"data": [{"id": "5", "text": "from x"}]})
        posts = twitr.client().search_posts("q", SearchOptions(max_results=50))
        self.assertEqual(self.core.calls, [])
        self.assertEqual([p["text"] for p in posts], ["from x"])

    def test_a_failed_read_falls_back_to_x(self):
        self.core.answer(503, {"error": "upstream busy"})
        self.x.route("GET", "/2/users/by/username/someone", {"data": {"id": "42", "username": "someone"}})
        user = twitr.client().user_by_username("@someone")
        self.assertEqual(user["id"], "42")
        self.assertEqual(self.core.body(), {"resource": "get-user", "id": "someone"})

    def test_reads_go_through_the_read_cache(self):
        self.core.answer(200, {"items": [{"id": "77", "text": "hello"}]})
        reads = cache.reads()
        self.assertEqual(reads.get_post("77")["text"], "hello")
        self.assertEqual(reads.get_post("77")["text"], "hello")
        self.assertEqual(len(self.core.calls), 1, "the second read is the cache's")

    def test_user_shape(self):
        self.core.answer(200, {"items": [{"id": "42", "userName": "someone", "name": "S", "description": "bio", "followers": 10, "following": 2, "statusesCount": 99, "isBlueVerified": True}]})
        self.assertEqual(
            twitr.client().user_by_username("someone"),
            {"id": "42", "username": "someone", "name": "S", "description": "bio", "verified": True, "followers": 10, "following": 2, "posts": 99},
        )

    def test_snake_case_items_and_nested_authors_are_read(self):
        post = twitr.to_post({"id_str": "5", "full_text": "t", "author": {"id": "42", "userName": "a"}, "conversation_id": "4", "in_reply_to_user_id": "1000", "reply_count": 2})
        self.assertEqual(post["authorId"], "42")
        self.assertEqual(post["authorUsername"], "a")
        self.assertEqual(post["conversationId"], "4")
        self.assertEqual(post["inReplyToUserId"], "1000")
        self.assertEqual(post["metrics"]["replies"], 2)


class OffTest(Scratch):
    def test_without_the_switches_the_client_is_plain_x(self):
        self.assertIsInstance(twitr.client(), XApi)


class WritesTest(Twitr):
    def setUp(self):
        super().setUp()
        self.at(T0)
        me(self.x, "1000")
        self.x.route("GET", "/2/tweets/5", {"data": {"id": "5", "text": "@teenydroid gm", "author_id": "42", "conversation_id": "4"}, "includes": {"users": [{"id": "42", "username": "someone"}]}})
        guard = mock.patch.object(publish, "check_outbound", Guard())
        guard.start()
        self.addCleanup(guard.stop)
        # Reads stay on X here, so the only core calls are the writes.
        self.configure(X_TWITR_READS="0")

    def test_a_reply_goes_out_through_twitr_with_a_stable_idempotency_key(self):
        self.core.answer(200, {"items": [{"id": "99"}]})
        result = publish.reply("5", {"text": "hi"})
        self.assertEqual(result["id"], "99")
        self.assertEqual(self.core.body(), {"action": "reply", "account": "teenydroid", "text": "hi", "reply_to_tweet_id": "5"})
        self.assertEqual(self.core.params["maxAmountAtomic"], twitr.WRITE)
        self.assertEqual(self.core.params["headers"]["Idempotency-Key"], twitr.idempotency_key("reply", "teenydroid", "5", "hi"))
        self.assertEqual(self.x.requests("POST", "/2/tweets"), [], "nothing is written through X")

    def test_a_failed_write_never_falls_back_to_x_and_stays_in_doubt(self):
        self.core.fail("openhuman.x402_fetch: x402_fetch: request failed")
        with self.assertRaises(twitr.TwitrError) as caught:
            publish.reply("5", {"text": "hi"})
        self.assertIsNone(caught.exception.status)
        self.assertEqual(self.x.writes(), [])
        conn = hand.db()
        [row] = conn.execute("SELECT in_reply_to FROM x_replies").fetchall()
        self.assertEqual(row[0], "5", "an in-doubt reply keeps its claim, so it is never sent twice")

    def test_a_refused_payment_is_a_definite_no(self):
        self.core.fail("openhuman.x402_fetch: X402_REFUSED: x402 payment of 36000 exceeds maxAmountAtomic")
        with self.assertRaises(twitr.TwitrError) as caught:
            publish.reply("5", {"text": "hi"})
        self.assertEqual(caught.exception.status, 402)

    def test_accepted_without_an_id_is_in_doubt(self):
        self.core.answer(202, {"status": "pending_confirmation", "action_id": "a1"})
        with self.assertRaises(twitr.TwitrError) as caught:
            twitr.client().create_post("hi")
        self.assertIsNone(caught.exception.status)

    def test_media_and_quotes_go_through_x(self):
        created = self.x.route("POST", "/2/tweets", {"data": {"id": "98"}}, status=201)
        self.assertEqual(twitr.client().create_post("look", quote="5"), "98")
        self.assertEqual(created.times[0], 1)
        self.assertEqual(self.core.calls, [])


class AccountTest(Twitr):
    def test_connect_signs_in_and_cannot_pay(self):
        self.core.answer(200, {"handle": "teenydroid", "connect_url": "https://twitr.test/connect/abc", "expires_in": 900, "other": 1})
        result = twitr.TwitrApi.from_settings().connect("@teenydroid")
        self.assertEqual(result, {"handle": "teenydroid", "connect_url": "https://twitr.test/connect/abc", "expires_in": 900})
        self.assertTrue(self.core.params["signIn"])
        self.assertEqual(self.core.params["maxAmountAtomic"], twitr.FREE)
        self.assertEqual(self.core.body(), {"username": "teenydroid"})
