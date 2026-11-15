"""hands/x thread: two to five posts, the first an own post and each later one
a reply to the one before it. Everything is checked before anything is sent;
the thread counts once against the gap and the daily cap; a continuation is
recorded with its own kind, measured like any post of Teeny's and counted as
neither an own post nor a reply."""

from __future__ import annotations

import contextlib
import io
import json
from unittest import mock

from .support import HOUR, T0, Guard, Scratch, me
from teeny_hands import hand
from teeny_hands.x import cli, metrics, posting_rules, publish
from teeny_hands.x.client import XError

TEXTS = ["one: the build broke", "two: here is why", "three: and the fix"]


class Threading(Scratch):
    def setUp(self):
        super().setUp()
        self.at(T0)
        me(self.x, "1000")
        self.ids: dict[str, str] = {}
        self.guard = Guard()
        patcher = mock.patch.object(publish, "check_outbound", self.guard)
        patcher.start()
        self.addCleanup(patcher.stop)

    def serve(self, texts=TEXTS, first_id=101, fail_at: int | None = None, status: int = 403) -> None:
        """X answers each text with the next id; the one at `fail_at` with `status`."""
        for i, text in enumerate(texts):
            if i == fail_at:
                self.x.route("POST", "/2/tweets", {"title": "Forbidden"}, status=status,
                             when=lambda s, t=text: s.json()["text"] == t)
            else:
                self.x.route("POST", "/2/tweets", {"data": {"id": str(first_id + i)}}, status=201,
                             when=lambda s, t=text: s.json()["text"] == t)

    def posted(self) -> list[dict]:
        return [s.json() for s in self.x.requests("POST", "/2/tweets")]

    def ledger(self) -> list[dict]:
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        return [dict(r) for r in conn.execute("SELECT * FROM x_posts ORDER BY created_at, id")]

    def body(self, texts=TEXTS, **extra) -> dict:
        return {"posts": list(texts), "anchor": "pr 212", **extra}


class SendingTest(Threading):
    def test_sends_each_post_as_a_reply_to_the_one_before(self):
        self.serve()
        result = publish.thread(self.body())
        self.assertEqual(self.posted(), [
            {"text": TEXTS[0]},
            {"text": TEXTS[1], "reply": {"in_reply_to_tweet_id": "101"}},
            {"text": TEXTS[2], "reply": {"in_reply_to_tweet_id": "102"}},
        ])
        self.assertEqual((result["kind"], result["sent"], result["ids"]), ("thread", 3, ["101", "102", "103"]))
        self.assertEqual(result["posts"], TEXTS)

    def test_records_the_head_as_an_own_post_and_the_rest_as_continuations(self):
        self.serve()
        publish.thread(self.body(format="proof", experiment=3))
        head, second, third = self.ledger()
        self.assertEqual((head["id"], head["kind"], head["anchor"], head["format"], head["experiment"]),
                         ("101", "post", "pr 212", "proof", 3))
        self.assertEqual((second["id"], second["kind"], second["in_reply_to"], second["thread_of"]),
                         ("102", "thread", "101", "101"))
        self.assertEqual((third["id"], third["kind"], third["in_reply_to"], third["thread_of"]),
                         ("103", "thread", "102", "101"))
        self.assertIsNone(second["format"])
        self.assertIsNone(second["anchor"])

    def test_media_go_on_the_head_only(self):
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        posting_rules.record_media(conn, "m1", "state/media/a.png", "image/png", 3)
        self.serve()
        publish.thread(self.body(media_ids=["m1"]))
        first, second, third = self.posted()
        self.assertEqual(first["media"], {"media_ids": ["m1"]})
        self.assertNotIn("media", second)
        self.assertNotIn("media", third)

    def test_every_post_goes_through_the_guard_and_its_text_is_what_is_sent(self):
        self.guard.transform = str.upper
        self.serve([t.upper() for t in TEXTS])
        publish.thread(self.body())
        self.assertEqual([c[0] for c in self.guard.calls], TEXTS)
        self.assertEqual([p["text"] for p in self.posted()], [t.upper() for t in TEXTS])

    def test_replies_nothing_to_the_replying_lane(self):
        self.serve()
        publish.thread(self.body())
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        self.assertEqual(conn.execute("SELECT count(*) FROM x_replies").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT count(*) FROM x_pacing").fetchone()[0], 0)


class RefusalTest(Threading):
    def refused(self, body, rule: str | None = None):
        with self.assertRaises(hand.Refused) as caught:
            publish.thread(body)
        if rule:
            self.assertEqual(caught.exception.detail.get("rule"), rule)
        self.assertEqual(self.x.writes(), [])
        self.assertEqual(self.ledger(), [])
        return caught.exception

    def test_one_post_is_not_a_thread(self):
        self.refused(self.body(["alone"]), "thread")

    def test_six_posts_are_too_many(self):
        self.refused(self.body([f"post number {i} of six" for i in range(6)]), "thread")

    def test_two_and_five_posts_are_enough(self):
        for n in (2, 5):
            with self.subTest(n=n):
                texts = [f"a thread of {n}, post {i} says something new" for i in range(n)]
                self.serve(texts, first_id=200 + 10 * n)
                self.assertEqual(publish.thread(self.body(texts))["sent"], n)
                self.at(hand.now() + 6 * HOUR)

    def test_posts_must_be_a_list_of_text(self):
        for bad in ("one two", [1, 2], ["ok", "  "], None):
            with self.subTest(bad=bad), self.assertRaises(hand.HandError):
                publish.thread({"posts": bad, "anchor": "pr 1"})

    def test_no_anchor(self):
        self.refused({"posts": TEXTS}, "anchor")
        self.refused({"posts": TEXTS, "anchor": "  "}, "anchor")

    def test_a_quote_is_not_a_thread(self):
        self.refused(self.body(quote_of="555"), "thread")

    def test_unknown_fields_are_refused(self):
        with self.assertRaises(hand.HandError):
            publish.thread(self.body(text="smuggled https://example.com"))
        self.assertEqual(self.x.writes(), [])

    def test_one_held_post_holds_the_whole_thread(self):
        def verdict(text, **kwargs):
            from types import SimpleNamespace
            if text == TEXTS[2]:
                return SimpleNamespace(kind="hold", text=text, reason="", category="politics", hold_id=9, judge_failed=False)
            return SimpleNamespace(kind="allow", text=text, reason="", category=None, hold_id=None, judge_failed=False)
        with mock.patch.object(publish, "check_outbound", verdict):
            self.refused(self.body())
        self.assertEqual(self.guard.calls, [])

    def test_one_refused_post_refuses_the_whole_thread(self):
        with mock.patch.object(publish, "check_outbound", Guard(kind="refuse", reason="no")):
            self.refused(self.body())

    def test_one_over_long_post_refuses_the_whole_thread(self):
        long = "x" * 281
        exc = self.refused(self.body([TEXTS[0], long, TEXTS[2]]), "length")
        self.assertEqual(exc.detail["length"], 281)

    def test_two_hundred_eighty_is_allowed(self):
        texts = [TEXTS[0], "y" * 280]
        self.serve(texts)
        self.assertEqual(publish.thread(self.body(texts))["sent"], 2)


class RulesTest(Threading):
    def test_counts_once_against_the_daily_cap(self):
        self.configure(X_POSTS_MAX_PER_DAY="2", POST_MIN_GAP_HOURS="0")
        self.serve()
        publish.thread(self.body())
        later = ["a second thread, first", "a second thread, second"]
        self.serve(later, first_id=300)
        self.assertEqual(publish.thread(self.body(later, anchor="pr 213"))["sent"], 2)
        third = ["a third thread, first", "a third thread, second"]
        with self.assertRaises(hand.Refused) as caught:
            publish.thread(self.body(third, anchor="pr 214"))
        self.assertEqual(caught.exception.detail["rule"], "daily_cap")
        self.assertEqual(caught.exception.detail["sent_today"], 2)

    def test_counts_once_against_the_gap_and_the_slot_stays_shut_after(self):
        self.serve()
        publish.thread(self.body())
        self.assertIn("why", publish.slot())
        self.at(T0 + 5 * HOUR)
        self.assertEqual(publish.slot(), {"open": True})
        with self.assertRaises(hand.Refused) as caught:
            self.at(T0 + HOUR)
            publish.thread(self.body(["other thread, one", "other thread, two"]))
        self.assertEqual(caught.exception.detail["rule"], "gap")
        self.assertEqual(caught.exception.detail["last_post_id"], "101")

    def test_the_gap_is_read_from_the_head_not_the_continuations(self):
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        posting_rules.record_post(conn, "50", "post", "earlier post", created_at=T0 - 6 * HOUR)
        posting_rules.record_post(conn, "51", "thread", "its second", in_reply_to="50", created_at=T0 - HOUR)
        self.serve()
        self.assertEqual(publish.thread(self.body())["sent"], 3)

    def test_continuations_are_not_own_posts_for_the_cap(self):
        self.configure(X_POSTS_MAX_PER_DAY="2", POST_MIN_GAP_HOURS="0")
        self.serve()
        publish.thread(self.body())
        conn = hand.db()
        self.assertEqual(conn.execute("SELECT count(*) FROM x_posts WHERE kind IN ('post', 'quote')").fetchone()[0], 1)
        posting_rules.check_daily_cap(conn)

    def test_a_continuation_is_not_a_reply_for_the_pacing_or_the_one_reply_rule(self):
        self.serve()
        publish.thread(self.body())
        conn = hand.db()
        posting_rules.check_pacing(conn)
        posting_rules.check_not_replied(conn, "101")

    def test_a_repeat_of_the_head_is_refused(self):
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        posting_rules.record_post(conn, "50", "post", TEXTS[0] + " today", created_at=T0 - 6 * HOUR)
        with self.assertRaises(hand.Refused) as caught:
            publish.thread(self.body())
        self.assertEqual(caught.exception.detail["rule"], "repeat")
        self.assertEqual(self.x.writes(), [])

    def test_a_repeat_of_an_earlier_thread_as_a_whole_is_refused(self):
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        posting_rules.record_post(conn, "50", "post", " ".join(TEXTS), created_at=T0 - 6 * HOUR)
        with self.assertRaises(hand.Refused) as caught:
            publish.thread(self.body(["a different opening line entirely", *TEXTS[1:]]))
        self.assertEqual(caught.exception.detail["rule"], "repeat")
        self.assertEqual(caught.exception.detail["similar_to"], "50")

    def test_a_link_anywhere_makes_it_one_link_post(self):
        texts = [TEXTS[0], "two: details at github.com/teenydroid/brain", "three: https://example.com/x"]
        self.serve(texts)
        publish.thread(self.body(texts))
        self.assertEqual(posting_rules.link_budget(hand.db())["sent_today"], 1)

    def test_a_thread_with_a_link_is_refused_when_the_link_budget_is_spent(self):
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        posting_rules.record_post(conn, "50", "post", "see github.com/teenydroid", created_at=T0 - 6 * HOUR)
        texts = [TEXTS[0], TEXTS[1], "three: github.com/teenydroid/brain"]
        with self.assertRaises(hand.Refused) as caught:
            publish.thread(self.body(texts))
        self.assertEqual(caught.exception.detail["rule"], "links")
        self.assertEqual(self.x.writes(), [])

    def test_a_thread_without_a_link_ignores_the_spent_link_budget(self):
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        posting_rules.record_post(conn, "50", "post", "see github.com/teenydroid", created_at=T0 - 6 * HOUR)
        self.serve()
        self.assertEqual(publish.thread(self.body())["sent"], 3)


class FailureTest(Threading):
    def test_a_failure_part_way_keeps_what_went_out_and_says_so(self):
        texts = [*TEXTS, "four: and the last"]
        self.serve(texts, fail_at=2)
        with self.assertRaises(XError) as caught:
            publish.thread(self.body(texts))
        detail = caught.exception.detail
        self.assertEqual((detail["sent"], detail["of"], detail["ids"]), (2, 4, ["101", "102"]))
        self.assertEqual(detail["failed_at"], 3)
        self.assertEqual(len(self.posted()), 3, "the fourth is never tried and the third never retried")
        self.assertEqual(self.x.requests("DELETE"), [])
        self.assertEqual([r["id"] for r in self.ledger()], ["101", "102"])
        self.assertEqual([r["kind"] for r in self.ledger()], ["post", "thread"])

    def test_an_unconfirmed_post_is_marked_in_doubt(self):
        self.serve(fail_at=1, status=502)
        with self.assertRaises(XError) as caught:
            publish.thread(self.body())
        self.assertTrue(caught.exception.detail["in_doubt"])
        self.assertEqual(caught.exception.detail["ids"], ["101"])

    def test_a_head_that_fails_is_an_ordinary_failure_with_nothing_out(self):
        self.serve(fail_at=0)
        with self.assertRaises(XError) as caught:
            publish.thread(self.body())
        self.assertEqual(caught.exception.detail["sent"], 0)
        self.assertEqual(self.ledger(), [])

    def test_the_cli_reports_it_as_an_error_with_the_ids(self):
        self.serve(fail_at=2)
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(self.body()))), contextlib.redirect_stdout(out):
            code = cli.main(["thread"])
        result = json.loads(out.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertEqual((result["sent"], result["ids"]), (2, ["101", "102"]))
        self.assertIn("error", result)

    def test_the_cli_runs_the_verb(self):
        self.serve()
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(self.body()))), contextlib.redirect_stdout(out):
            code = cli.main(["thread"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["ids"], ["101", "102", "103"])


class DryRunTest(Threading):
    def test_dry_run_sends_and_records_nothing_and_shows_what_would_go(self):
        self.configure(DRY_RUN="1")
        result = publish.thread(self.body())
        self.assertEqual(result["dry_run"], True)
        self.assertEqual(result["would_post"], TEXTS)
        self.assertEqual(self.x.writes(), [])
        self.assertEqual(self.ledger(), [])

    def test_dry_run_still_applies_every_rule(self):
        self.configure(DRY_RUN="1")
        with self.assertRaises(hand.Refused):
            publish.thread(self.body([TEXTS[0], "x" * 300]))
        with self.assertRaises(hand.Refused):
            publish.thread(self.body(["only one"]))


class ReplyStaysClosedTest(Threading):
    def test_hands_x_reply_to_an_own_post_is_still_refused(self):
        self.serve()
        publish.thread(self.body())
        self.x.route("GET", "/2/tweets/102", {"data": {
            "id": "102", "text": TEXTS[1], "author_id": "1000", "conversation_id": "101"},
            "includes": {"users": [{"id": "1000", "username": "teenydroid"}]}})
        with self.assertRaises(hand.Refused) as caught:
            publish.reply("102", {"text": "and one more"})
        self.assertEqual(caught.exception.detail["rule"], "self")


class MeasuringTest(Threading):
    def test_metrics_refresh_measures_the_continuations_too(self):
        self.serve()
        publish.thread(self.body())
        self.x.route("GET", "/2/tweets", {"data": [
            {"id": i, "text": "t", "public_metrics": {"like_count": 1, "reply_count": 0, "retweet_count": 0,
                                                       "quote_count": 0, "impression_count": 10}}
            for i in ("101", "102", "103")]})
        self.at(T0 + 2 * HOUR)
        out = metrics.refresh()
        self.assertEqual(sorted(r["id"] for r in out["recorded"]), ["101", "102", "103"])
        kinds = {m["post_id"]: m["kind"] for m in metrics.listing(7)["metrics"]}
        self.assertEqual(kinds, {"101": "post", "102": "thread", "103": "thread"})


class LedgerMigrationTest(Threading):
    def test_a_ledger_created_before_threads_takes_a_continuation(self):
        conn = hand.db()
        conn.executescript("""
            CREATE TABLE x_posts (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('post', 'reply', 'quote')),
                text TEXT NOT NULL, in_reply_to TEXT, quote_of TEXT,
                media TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL);
            CREATE INDEX x_posts_kind_created ON x_posts (kind, created_at);
            INSERT INTO x_posts (id, kind, text, created_at) VALUES ('7', 'reply', 'old reply', 1.0);
        """)
        conn.close()
        self.serve()
        publish.thread(self.body())
        rows = {r["id"]: r["kind"] for r in self.ledger()}
        self.assertEqual(rows, {"7": "reply", "101": "post", "102": "thread", "103": "thread"})
