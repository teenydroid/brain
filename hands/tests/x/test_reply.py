"""Replies: the guard, then one reply per post, never to the account's own
post, the pacing, then DRY_RUN, X, and the reply id recorded before anything
else. Ported from crates/teeny-jobs/src/reply/job_tests.rs (self-reply,
pacing, "a crash after posting does not answer the mention again") and the
reply cases of crates/teeny-agent/src/tools/post_tests.rs."""

from __future__ import annotations

import contextlib
import io
import sqlite3
import threading
from unittest import mock

from .support import HOUR, T0, Guard, Scratch, me
from teeny_hands import hand
from teeny_hands.x import posting_rules, publish
from teeny_hands.x.client import XError


def target(post_id: str, author_id: str = "42", username: str = "someone") -> dict:
    return {
        "data": {"id": post_id, "text": "@teenydroid gm", "author_id": author_id, "conversation_id": "4"},
        "includes": {"users": [{"id": author_id, "username": username}]},
    }


class Replying(Scratch):
    settings = {"REPLY_MIN_INTERVAL_MS": "45000", "REPLY_MAX_INTERVAL_MS": "45000"}

    def setUp(self):
        super().setUp()
        self.at(T0)
        me(self.x, "1000")
        for post_id in ("5", "6", "7"):
            self.x.route("GET", f"/2/tweets/{post_id}", target(post_id))
        self.x.route("GET", "/2/tweets/10", target("10", author_id="1000", username="teenydroid"))
        self.created = self.x.route("POST", "/2/tweets", {"data": {"id": "99"}}, status=201)
        self.guard = Guard()
        patcher = mock.patch.object(publish, "check_outbound", self.guard)
        patcher.start()
        self.addCleanup(patcher.stop)

    def posted(self) -> list[dict]:
        return [s.json() for s in self.x.requests("POST", "/2/tweets")]

    def table(self, name: str) -> list[dict]:
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        return [dict(r) for r in conn.execute(f"SELECT * FROM {name}")]


class ReplyTest(Replying):
    def test_replies_and_records_the_reply(self):
        result = publish.reply("5", {"text": "hi"})
        self.assertEqual(result, {"id": "99", "in_reply_to": "5", "kind": "reply", "text": "hi"})
        self.assertEqual(self.posted(), [{"text": "hi", "reply": {"in_reply_to_tweet_id": "5"}}])
        [replied] = self.table("x_replies")
        self.assertEqual((replied["in_reply_to"], replied["reply_id"]), ("5", "99"))
        [ledger] = self.table("x_posts")
        self.assertEqual((ledger["id"], ledger["kind"], ledger["in_reply_to"]), ("99", "reply", "5"))

    def test_asks_the_guard_about_a_reply_to_its_author(self):
        publish.reply("5", {"text": "hi"})
        text, kwargs = self.guard.calls[0]
        self.assertEqual((text, kwargs["kind"], kwargs["replying_to"]), ("hi", "reply", "someone"))
        self.assertEqual(kwargs["context"]["post_id"], "5")

    def test_publishes_the_guard_s_text(self):
        self.guard.transform = lambda text: "styled"
        publish.reply("5", {"text": "@someone hi"})
        self.assertEqual(self.posted()[0]["text"], "styled")

    def test_never_replies_twice_to_the_same_post(self):
        publish.reply("5", {"text": "one"})
        self.at(T0 + HOUR)
        with self.assertRaises(hand.Refused) as caught:
            publish.reply("5", {"text": "two"})
        self.assertEqual(caught.exception.detail["rule"], "replied")
        self.assertEqual(caught.exception.detail["reply_id"], "99")
        self.assertEqual(len(self.posted()), 1)

    def test_refuses_a_post_the_account_itself_wrote(self):
        with self.assertRaises(hand.Refused) as caught:
            publish.reply("10", {"text": "me again"})
        self.assertEqual(caught.exception.detail["rule"], "self")
        self.assertEqual(self.posted(), [])

    def test_refuses_when_x_does_not_have_the_post(self):
        self.x.route("GET", "/2/tweets/404", {"errors": [{"title": "Not Found Error"}]})
        with self.assertRaises(hand.Refused):
            publish.reply("404", {"text": "hi"})
        self.assertEqual(self.posted(), [])

    def test_refuses_when_the_account_cannot_be_identified(self):
        self.x.route("GET", "/2/users/me", {"title": "Unauthorized"}, status=401)
        with self.assertRaises(XError):
            publish.reply("5", {"text": "hi"})
        self.assertEqual(self.posted(), [])

    def test_refuses_when_the_author_is_unknown(self):
        self.x.route("GET", "/2/tweets/8", {"data": {"id": "8", "text": "no author"}})
        with self.assertRaises(hand.Refused) as caught:
            publish.reply("8", {"text": "hi"})
        self.assertEqual(caught.exception.detail["rule"], "self")
        self.assertEqual(self.posted(), [])

    def test_a_reply_is_not_held_to_the_gap(self):
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        posting_rules.record_post(conn, "77", "post", "gm", created_at=T0)
        publish.reply("5", {"text": "hi"})
        self.assertEqual(len(self.posted()), 1)

    def test_the_post_replied_to_must_be_an_x_id(self):
        for bad in ("", "abc", "https://x.com/a/status/5", "5; DROP"):
            with self.assertRaises(hand.HandError, msg=bad):
                publish.reply(bad, {"text": "hi"})

    def test_a_reply_cannot_quote(self):
        with self.assertRaises(hand.HandError):
            publish.reply("5", {"text": "hi", "quote_of": "6"})


class PacingTest(Replying):
    def test_the_gap_is_random_within_the_range_and_never_below_the_minimum(self):
        self.assertEqual(posting_rules.gap_ms(20, 100, 0.0), 20)
        self.assertEqual(posting_rules.gap_ms(20, 100, 1.0), 100)
        self.assertEqual(posting_rules.gap_ms(20, 120, 0.5), 70)
        self.assertEqual(posting_rules.gap_ms(50, 10, 0.9), 50)
        self.assertEqual(posting_rules.gap_ms(0, 100, 0.5), 0)

    def test_does_not_hold_back_the_first_reply(self):
        publish.reply("5", {"text": "hi"})
        self.assertEqual(len(self.posted()), 1)

    def test_holds_the_next_reply_back_and_says_how_long(self):
        publish.reply("5", {"text": "one"})
        self.at(T0 + 44)
        with self.assertRaises(hand.Refused) as caught:
            publish.reply("6", {"text": "two"})
        self.assertEqual(caught.exception.detail["rule"], "pacing")
        self.assertAlmostEqual(caught.exception.detail["wait_seconds"], 1.0)
        self.assertEqual(len(self.posted()), 1)
        self.at(T0 + 45)
        publish.reply("6", {"text": "two"})
        self.assertEqual(len(self.posted()), 2)

    def test_the_default_gap_is_thirty_to_sixty_seconds(self):
        self.configure(REPLY_MIN_INTERVAL_MS=None, REPLY_MAX_INTERVAL_MS=None)
        publish.reply("5", {"text": "one"})
        self.at(T0 + 29.9)
        with self.assertRaises(hand.Refused):
            publish.reply("6", {"text": "two"})
        self.at(T0 + 60)
        publish.reply("6", {"text": "two"})
        [row] = self.table("x_pacing")
        self.assertGreaterEqual(row["next_at"] - row["last_at"], 30)
        self.assertLessEqual(row["next_at"] - row["last_at"], 60)

    def test_a_refused_reply_does_not_move_the_pacing(self):
        publish.reply("5", {"text": "one"})
        self.at(T0 + 50)
        with self.assertRaises(hand.Refused):
            publish.reply("10", {"text": "self"})
        publish.reply("6", {"text": "two"})
        self.assertEqual(len(self.posted()), 2)


class DryRunReplyTest(Replying):
    def test_publishes_nothing_records_nothing_and_does_not_pace(self):
        self.configure(DRY_RUN="1")
        result = publish.reply("5", {"text": "hi"})
        self.assertEqual(result, {"ok": True, "dry_run": True, "would_post": "hi", "kind": "reply", "in_reply_to": "5"})
        self.assertEqual(publish.reply("6", {"text": "hi"})["dry_run"], True)
        self.assertEqual(self.x.writes(), [])
        self.assertEqual(self.table("x_replies"), [])
        self.assertEqual(self.table("x_posts"), [])
        self.assertEqual(self.table("x_pacing"), [])

    def test_still_refuses_the_account_s_own_post(self):
        self.configure(DRY_RUN="1")
        with self.assertRaises(hand.Refused):
            publish.reply("10", {"text": "hi"})


class RecordFirstTest(Replying):
    def test_the_reply_id_is_recorded_before_anything_that_can_fail(self):
        broken = mock.patch.object(posting_rules, "record_post", side_effect=sqlite3.OperationalError("disk I/O error"))
        with broken, contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = publish.reply("5", {"text": "hi"})
        self.assertEqual(result["id"], "99")
        self.assertIn("could not record", stderr.getvalue())
        self.assertEqual(self.table("x_replies")[0]["reply_id"], "99")
        self.at(T0 + HOUR)
        with self.assertRaises(hand.Refused):
            publish.reply("5", {"text": "again"})
        self.assertEqual(len(self.posted()), 1, "answered once")

    def test_a_reply_id_that_cannot_be_written_still_blocks_a_second_reply(self):
        broken = mock.patch.object(posting_rules, "record_reply_id", side_effect=sqlite3.OperationalError("disk I/O error"))
        with broken, contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(publish.reply("5", {"text": "hi"})["id"], "99")
        self.assertIn("could not record the reply id", stderr.getvalue())
        self.at(T0 + HOUR)
        with self.assertRaises(hand.Refused):
            publish.reply("5", {"text": "again"})
        self.assertEqual(len(self.posted()), 1, "answered once")

    def test_a_reply_x_refused_may_be_tried_again(self):
        self.x.route("POST", "/2/tweets", {"title": "Forbidden"}, status=403)
        with self.assertRaisesRegex(XError, "403"):
            publish.reply("5", {"text": "hi"})
        self.assertEqual(self.table("x_replies"), [])
        self.x.route("POST", "/2/tweets", {"data": {"id": "99"}}, status=201)
        self.assertEqual(publish.reply("5", {"text": "hi"})["id"], "99")

    def test_a_reply_whose_fate_is_unknown_is_never_risked_twice(self):
        for answer, status in (({"errors": [{"message": "odd"}]}, 200), ({"title": "Bad Gateway"}, 502)):
            with self.subTest(status=status):
                self.x.route("POST", "/2/tweets", answer, status=status)
                post_id = "6" if status == 200 else "7"
                with self.assertRaises(XError):
                    publish.reply(post_id, {"text": "hi"})
                self.at(hand.now() + HOUR)
                with self.assertRaises(hand.Refused) as caught:
                    publish.reply(post_id, {"text": "hi"})
                self.assertIn("may already have gone out", caught.exception.reason)

    def test_two_replies_to_one_post_at_once_answer_it_once(self):
        self.x.route("POST", "/2/tweets", {"data": {"id": "99"}}, status=201, delay=0.3)
        outcomes = []

        def attempt():
            try:
                outcomes.append(publish.reply("5", {"text": "hi"}))
            except hand.Refused as refused:
                outcomes.append(refused)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.posted()), 1, outcomes)
        self.assertEqual(sum(isinstance(o, hand.Refused) for o in outcomes), 1)


if __name__ == "__main__":
    import unittest

    unittest.main()


class LinkReplyTest(Replying):
    def test_a_link_reply_over_the_days_cap_is_refused_before_x(self):
        self.configure(X_LINK_POSTS_MAX_PER_DAY="0", REPLY_MIN_INTERVAL_MS="45000", REPLY_MAX_INTERVAL_MS="45000")
        with self.assertRaises(hand.Refused) as caught:
            publish.reply("5", {"text": "here: github.com/teenydroid"})
        self.assertEqual(caught.exception.detail["rule"], "links")
        self.assertEqual(self.posted(), [])
        self.assertEqual(self.table("x_replies"), [])
