"""Own posts: guard, then the posting rules, then DRY_RUN, then X, then the
ledger. Ported from crates/teeny-x/src/publisher_tests.rs,
crates/teeny-cadence/src/cadence_tests.rs and the attachment cases of
crates/teeny-agent/src/tools/post_tests.rs.

The guard is the other hand's; here it is a stand-in patched into
teeny_hands.x.publish, which is the one place the hand calls it."""

from __future__ import annotations

import contextlib
import io
import sqlite3
import threading
from unittest import mock

from .support import HOUR, MINUTE, T0, Guard, Scratch, shout
from teeny_hands import hand
from teeny_hands.x import posting_rules, publish
from teeny_hands.x.client import XError


class Posting(Scratch):
    def setUp(self):
        super().setUp()
        self.at(T0)
        self.created = self.x.route("POST", "/2/tweets", {"data": {"id": "100"}}, status=201)
        self.guard = Guard()
        patcher = mock.patch.object(publish, "check_outbound", self.guard)
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_guard(self, guard: Guard) -> Guard:
        self.guard = guard
        patcher = mock.patch.object(publish, "check_outbound", guard)
        patcher.start()
        self.addCleanup(patcher.stop)
        return guard

    def ledger(self) -> list[dict]:
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        return [dict(r) for r in conn.execute("SELECT * FROM x_posts ORDER BY created_at")]

    def posted(self) -> list[dict]:
        return [s.json() for s in self.x.requests("POST", "/2/tweets")]

    def mint(self, media_id: str, dry_run: bool = False) -> None:
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        posting_rules.record_media(conn, media_id, "state/media/a.png", "image/png", 3, dry_run=dry_run)


class GuardOrderTest(Posting):
    def test_posts_what_the_guard_returns_and_records_it(self):
        self.use_guard(Guard(transform=shout))
        result = publish.post({"text": "gm"})
        self.assertEqual(result["id"], "100")
        self.assertEqual(result["text"], "GM")
        self.assertEqual(self.posted(), [{"text": "GM"}])
        [row] = self.ledger()
        self.assertEqual((row["id"], row["kind"], row["text"], row["created_at"]), ("100", "post", "GM", T0))

    def test_asks_the_guard_about_an_own_post_with_the_emoji_cap(self):
        self.configure(MAX_EMOJI_PER_POST="2")
        publish.post({"text": "gm"})
        text, kwargs = self.guard.calls[0]
        self.assertEqual(text, "gm")
        self.assertEqual((kwargs["surface"], kwargs["kind"], kwargs["max_emoji"]), ("x", "post", 2))

    def test_a_refusal_never_reaches_x_and_asks_for_a_rewrite(self):
        self.use_guard(Guard(kind="refuse", reason="names a ticker that is not ours"))
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"text": "buy $FAKE"})
        self.assertEqual(caught.exception.reason, "names a ticker that is not ours")
        self.assertEqual(caught.exception.detail, {"rewrite": True})
        self.assertEqual(self.x.writes(), [])
        self.assertEqual(self.ledger(), [])

    def test_a_hold_never_reaches_x_and_names_the_hold(self):
        self.use_guard(Guard(kind="hold", reason="judge said no", category="politics", hold_id=12))
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"text": "vote"})
        self.assertEqual(caught.exception.reason, "held")
        self.assertEqual(caught.exception.detail, {"category": "politics", "hold_id": 12})
        self.assertEqual(self.x.writes(), [])

    def test_a_hold_the_judge_made_by_failing_says_so(self):
        # bin/lib/tick.py sends such a reply again next beat; a real hold never.
        self.use_guard(Guard(kind="hold", reason="held", category="unknown", hold_id=13, judge_failed=True))
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"text": "gm"})
        self.assertEqual(caught.exception.reason, "held")
        self.assertEqual(caught.exception.detail, {"category": "unknown", "hold_id": 13, "judge_failed": True})
        self.assertEqual(self.x.writes(), [])

    def test_an_answer_the_hand_does_not_know_fails_closed(self):
        self.use_guard(Guard(kind="maybe"))
        with self.assertRaises(hand.HandError):
            publish.post({"text": "gm"})
        self.assertEqual(self.x.writes(), [])

    def test_a_guard_that_breaks_fails_closed(self):
        def broken(text, **kwargs):
            raise RuntimeError("judge unreachable")

        self.use_guard(broken)  # type: ignore[arg-type]
        with self.assertRaises(RuntimeError):
            publish.post({"text": "gm"})
        self.assertEqual(self.x.writes(), [])

    def test_without_the_guard_installed_nothing_is_published(self):
        with self.assertRaisesRegex(hand.HandError, "guard"):
            publish.guard_unavailable("gm", surface="x", kind="post")

    def test_refuses_over_280_as_x_counts_after_the_guard(self):
        self.configure(DRY_RUN="1")
        self.assertTrue(publish.post({"text": "a" * 280})["dry_run"])
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"text": "a" * 281})
        self.assertEqual(caught.exception.detail["length"], 281)
        self.assertTrue(caught.exception.detail["rewrite"])
        # An emoji is two UTF-16 units, as the legacy `.length` counted it.
        self.assertTrue(publish.post({"text": "🐸" * 140})["dry_run"])
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"text": "🐸" * 141})
        self.assertEqual(caught.exception.detail["length"], 282)
        # The guard's rewrite is what is measured.
        self.use_guard(Guard(transform=lambda t: t + "!"))
        with self.assertRaises(hand.Refused):
            publish.post({"text": "a" * 280})
        self.assertEqual(self.x.requests(), [])

    def test_a_body_without_text_is_an_error(self):
        for body in (None, {}, {"text": ""}, {"text": 5}, ["gm"]):
            with self.assertRaises(hand.HandError, msg=repr(body)):
                publish.post(body)
        self.assertEqual(self.guard.calls, [])


class DryRunTest(Posting):
    def setUp(self):
        super().setUp()
        self.configure(DRY_RUN="1")

    def test_runs_the_guard_and_publishes_nothing(self):
        self.use_guard(Guard(transform=shout))
        result = publish.post({"text": "gm"})
        self.assertEqual(result, {"ok": True, "dry_run": True, "would_post": "GM", "kind": "post"})
        self.assertEqual(self.x.requests(), [], "no X request at all")
        self.assertEqual(self.ledger(), [])

    def test_a_refusal_is_still_a_refusal(self):
        self.use_guard(Guard(kind="refuse", reason="no"))
        with self.assertRaises(hand.Refused):
            publish.post({"text": "x"})

    def test_the_rules_still_run(self):
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        posting_rules.record_post(conn, "77", "post", "earlier", created_at=T0 - HOUR)
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"text": "gm"})
        self.assertEqual(caught.exception.detail["rule"], "gap")

    def test_on_by_default(self):
        self.configure(DRY_RUN=None)
        self.assertTrue(publish.post({"text": "gm"})["dry_run"])
        self.assertEqual(self.x.requests(), [])


class GapTest(Posting):
    settings = {"POST_MIN_GAP_HOURS": "8"}

    def test_lets_the_first_post_through_then_refuses_one_inside_the_gap(self):
        publish.post({"text": "gm"})
        self.at(T0 + 5 * HOUR)
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"text": "chart"})
        refused = caught.exception
        self.assertTrue(refused.reason.startswith("cadence: too soon after post 100"), refused.reason)
        self.assertEqual(refused.detail["rule"], "gap")
        self.assertEqual(refused.detail["last_post_id"], "100")
        self.assertEqual(refused.detail["last_posted_at"], "2026-09-14T07:00:00.000Z")
        self.assertEqual(refused.detail["next_at"], "2026-09-14T15:00:00.000Z")
        self.assertEqual(len(self.posted()), 1)

    def test_counts_the_tolerance_as_the_gap_so_a_slot_that_ran_late_still_posts(self):
        publish.post({"text": "gm"})
        self.at(T0 + 8 * HOUR - 25 * MINUTE)
        with self.assertRaises(hand.Refused):
            publish.post({"text": "chart"})
        self.at(T0 + 8 * HOUR - 15 * MINUTE)
        publish.post({"text": "chart"})
        self.assertEqual(len(self.posted()), 2)

    def test_the_default_gap_is_eight_hours_less_twenty_minutes(self):
        self.configure(POST_MIN_GAP_HOURS=None)
        publish.post({"text": "gm"})
        self.at(T0 + 8 * HOUR - 21 * MINUTE)
        with self.assertRaises(hand.Refused):
            publish.post({"text": "chart"})
        self.at(T0 + 8 * HOUR - 19 * MINUTE)
        publish.post({"text": "chart"})


    def test_a_quote_is_an_own_post_and_keeps_the_gap(self):
        publish.post({"text": "look", "quote_of": "555"})
        self.assertEqual(self.posted(), [{"text": "look", "quote_tweet_id": "555"}])
        self.assertEqual(self.ledger()[0]["kind"], "quote")
        self.assertEqual(self.ledger()[0]["quote_of"], "555")
        self.at(T0 + HOUR)
        with self.assertRaises(hand.Refused):
            publish.post({"text": "gm"})

    def test_a_reply_does_not_count_toward_the_gap(self):
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        posting_rules.record_post(conn, "90", "reply", "hi", in_reply_to="5", created_at=T0 - MINUTE)
        publish.post({"text": "gm"})
        self.assertEqual(len(self.posted()), 1)

    def test_two_own_posts_at_once_cannot_both_pass_the_gap(self):
        # Publishing takes a moment, as X does: without the posting lock the
        # second claim reads the ledger before the first post is recorded.
        self.x.route("POST", "/2/tweets", {"data": {"id": "100"}}, status=201, delay=0.3)
        outcomes = []

        def attempt(text):
            try:
                outcomes.append(publish.post({"text": text}))
            except hand.Refused as refused:
                outcomes.append(refused)

        threads = [threading.Thread(target=attempt, args=(t,)) for t in ("chart", "moon")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(isinstance(o, hand.Refused) for o in outcomes), 1, outcomes)
        self.assertEqual(len(self.posted()), 1)
        self.assertEqual(len(self.ledger()), 1)

    def test_a_failed_publish_records_nothing(self):
        self.x.route("POST", "/2/tweets", {"title": "Service Unavailable"}, status=503)
        with self.assertRaisesRegex(XError, "503"):
            publish.post({"text": "gm"})
        self.assertEqual(self.ledger(), [])
        self.x.route("POST", "/2/tweets", {"data": {"id": "101"}}, status=201)
        self.assertEqual(publish.post({"text": "gm"})["id"], "101")

    def test_a_ledger_that_cannot_record_is_logged_and_the_post_still_counts(self):
        broken = mock.patch.object(posting_rules, "record_post", side_effect=sqlite3.OperationalError("disk I/O error"))
        with broken, contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = publish.post({"text": "gm"})
        self.assertEqual(result["id"], "100")
        self.assertIn("published 100 but could not record it", stderr.getvalue())
        self.assertEqual(len(self.posted()), 1, "never retried")


class DailyCapTest(Posting):
    """Three own posts a UTC day by default (X_POSTS_MAX_PER_DAY); replies are
    never counted."""

    settings = {"POST_MIN_GAP_HOURS": "0", "POST_GAP_TOLERANCE_MINUTES": "0"}
    TEXTS = ("shipped a crab tracker", "the tracker found a hermit crab", "why the beach has no wifi")

    def earlier(self, kinds=("post", "post", "post"), at=T0 - 6 * HOUR) -> None:
        """Own posts already in the ledger (the fake X answers every post with
        id 100, so a test seeds the earlier ones)."""
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        for n, (kind, text) in enumerate(zip(kinds, self.TEXTS)):
            posting_rules.record_post(conn, f"8{n}", kind, text, created_at=at + n * 60, quote_of="5" if kind == "quote" else None)

    def test_a_fourth_post_in_a_day_is_refused_even_as_a_rehearsal(self):
        self.earlier()
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"text": "a fourth thing about lighthouses and moss"})
        self.assertEqual((caught.exception.detail["rule"], caught.exception.detail["cap"], caught.exception.detail["sent_today"]),
                         ("daily_cap", 3, 3))
        self.assertEqual(self.posted(), [])
        self.configure(DRY_RUN="1")
        with self.assertRaises(hand.Refused):
            publish.post({"text": "a fourth thing about lighthouses and moss"})

    def test_the_next_utc_day_starts_again(self):
        self.earlier()
        self.at(T0 + 17 * HOUR + 1)  # T0 is 07:00Z; this is just past midnight
        publish.post({"text": "a fourth thing about lighthouses and moss"})
        self.assertEqual(len(self.posted()), 1)

    def test_replies_do_not_count_and_a_quote_does(self):
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        for n in range(5):
            posting_rules.record_post(conn, f"9{n}", "reply", f"reply number {n} about sand", in_reply_to=str(n), created_at=T0 - HOUR)
        self.earlier(kinds=("post", "quote", "post"))
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"text": "one more about lighthouses and moss"})
        self.assertEqual(caught.exception.detail["rule"], "daily_cap")

    def test_the_cap_is_a_setting(self):
        self.configure(X_POSTS_MAX_PER_DAY="1")
        self.earlier(kinds=("post",))
        with self.assertRaises(hand.Refused):
            publish.post({"text": "a different thing about lighthouses"})

    def test_a_format_tag_is_kept_and_a_bad_one_is_an_error(self):
        publish.post({"text": self.TEXTS[0], "format": "Proof", "experiment": 4})
        self.assertEqual([r["format"] for r in self.ledger()], ["proof"])
        with self.assertRaises(hand.HandError):
            publish.post({"text": self.TEXTS[1], "format": "not a tag!"})
        with self.assertRaises(hand.HandError):
            publish.reply("5", {"text": "hi", "format": "gm"})

    def test_a_ledger_without_the_format_column_gains_it(self):
        conn = hand.db()
        conn.execute("CREATE TABLE x_posts (id TEXT PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL, in_reply_to TEXT,"
                     " quote_of TEXT, media TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL)")
        conn.commit()
        conn.close()
        publish.post({"text": self.TEXTS[0], "format": "gm"})
        self.assertEqual([r["format"] for r in self.ledger()], ["gm"])


class AttachmentsTest(Posting):
    def test_attaches_a_media_id_this_hand_minted(self):
        self.mint("m1")
        publish.post({"text": "gm", "media_ids": ["m1"]})
        self.assertEqual(self.posted(), [{"text": "gm", "media": {"media_ids": ["m1"]}}])
        self.assertEqual(self.ledger()[0]["media"], '["m1"]')

    def test_refuses_a_media_id_it_did_not_mint(self):
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"text": "gm", "media_ids": ["m-unknown"]})
        self.assertEqual(caught.exception.detail["rule"], "attachments")
        self.assertEqual(self.x.writes(), [])

    def test_refuses_a_url_as_an_attachment(self):
        for body in (
            {"text": "gm", "media_ids": ["https://evil.example/a.png"]},
            {"text": "gm", "gif_id": "https://giphy.com/gifs/abc"},
            {"text": "gm", "media_url": "https://evil.example/a.png"},
        ):
            with self.assertRaises((hand.Refused, hand.HandError), msg=repr(body)):
                publish.post(body)
        self.assertEqual(self.x.writes(), [])

    def test_refuses_a_gif_and_a_file_on_one_post(self):
        self.mint("m1")
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"text": "gm", "media_ids": ["m1"], "gif_id": "abc123"})
        self.assertIn("not both", caught.exception.reason)
        self.assertEqual(self.x.writes(), [])

    def test_a_rehearsal_s_media_id_never_goes_on_a_live_post(self):
        self.mint("dry-run-1", dry_run=True)
        with self.assertRaises(hand.Refused):
            publish.post({"text": "gm", "media_ids": ["dry-run-1"]})
        self.configure(DRY_RUN="1")
        self.assertTrue(publish.post({"text": "gm", "media_ids": ["dry-run-1"]})["dry_run"])

    def test_uploads_nothing_for_a_refused_or_rehearsed_post(self):
        self.configure(GIPHY_API_KEY="giphy-FAKE", GIPHY_API_BASE=self.x.base)
        self.use_guard(Guard(kind="refuse", reason="no"))
        with self.assertRaises(hand.Refused):
            publish.post({"text": "gm", "gif_id": "abc123"})
        self.use_guard(Guard())
        self.configure(DRY_RUN="1", GIPHY_API_KEY="giphy-FAKE", GIPHY_API_BASE=self.x.base)
        self.assertTrue(publish.post({"text": "gm", "gif_id": "abc123"})["dry_run"])
        self.assertEqual(self.x.requests(), [])


if __name__ == "__main__":
    import unittest

    unittest.main()


class LinkBudgetTest(Posting):
    """X bills a post carrying a URL $0.20 against $0.015 without, so link
    posts and replies are capped per UTC day."""

    settings = {"POST_MIN_GAP_HOURS": "0"}

    def record_link_post(self, post_id: str, at: float, kind: str = "post") -> None:
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        posting_rules.record_post(conn, post_id, kind, "shipped it: github.com/teenydroid/thing", created_at=at)

    def test_one_link_post_a_day_by_default_then_refused_without_calling_x(self):
        publish.post({"text": "shipped: github.com/teenydroid/thing"})
        self.record_link_post("101", T0)
        self.at(T0 + HOUR)
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"text": "and https://teeny.enamakel.com too"})
        self.assertEqual(caught.exception.detail["rule"], "links")
        self.assertEqual(len(self.posted()), 1)

    def test_a_post_without_a_link_is_never_counted_or_refused(self):
        self.record_link_post("101", T0)
        publish.post({"text": "gm tiny humans, v1.2 is out"})
        self.assertEqual(len(self.posted()), 1)

    def test_the_cap_resets_at_midnight_utc(self):
        self.record_link_post("101", T0)
        self.at(T0 + 17 * HOUR + 1)  # T0 is 07:00Z; this is just past midnight
        publish.post({"text": "new day: github.com/teenydroid/next"})
        self.assertEqual(len(self.posted()), 1)

    def test_zero_forbids_link_posts(self):
        self.configure(X_LINK_POSTS_MAX_PER_DAY="0")
        with self.assertRaises(hand.Refused):
            publish.post({"text": "look: github.com/teenydroid"})
        self.assertEqual(self.posted(), [])

    def test_a_link_reply_counts_against_the_same_cap(self):
        self.record_link_post("101", T0, kind="reply")
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"text": "www.example.com"})
        self.assertEqual(caught.exception.detail["rule"], "links")


class RepeatTest(Posting):
    """docs/roadmap.md, A2: an own post that says what a recent one said is
    refused, whatever the gap, and the refusal is counted by the guard."""

    settings = {"POST_MIN_GAP_HOURS": "0", "POST_GAP_TOLERANCE_MINUTES": "0"}
    YESTERDAY = "shipped a tiny crab tracker today, it counts crabs on the beach and posts the total"

    def test_a_repeat_of_yesterdays_post_is_refused_and_counted(self):
        from teeny_hands.guard import repeats

        publish.post({"text": self.YESTERDAY})
        self.at(T0 + 24 * HOUR)
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"text": "Shipped a tiny crab tracker today! It counts crabs on the beach and posts the total."})
        refused = caught.exception
        self.assertTrue(refused.reason.startswith("repeat: this says what post 100"), refused.reason)
        self.assertEqual((refused.detail["rule"], refused.detail["similar_to"]), ("repeat", "100"))
        self.assertGreaterEqual(refused.detail["score"], repeats.NEAR_DUPLICATE)
        self.assertEqual(len(self.posted()), 1)
        got = repeats.count(30)
        self.assertEqual((got["repeats"], got["by_surface"]), (1, {"x": 1}))
        # The count holds no text.
        conn = hand.db()
        self.assertEqual([tuple(r)[2:] for r in conn.execute("SELECT * FROM guard_repeats")], [("x", refused.detail["score"], "100")])

    def test_something_new_goes_out(self):
        publish.post({"text": self.YESTERDAY})
        self.at(T0 + 24 * HOUR)
        publish.post({"text": "the crab tracker found a hermit crab living in a bottle cap, which is the best news this week"})
        self.assertEqual(len(self.posted()), 2)

    def test_a_post_older_than_the_window_may_be_said_again(self):
        self.configure(REPEAT_WINDOW_DAYS="7")
        publish.post({"text": self.YESTERDAY})
        self.at(T0 + 8 * 24 * HOUR)
        publish.post({"text": self.YESTERDAY})
        self.assertEqual(len(self.posted()), 2)

    def test_short_posts_repeat_only_when_they_are_the_same(self):
        publish.post({"text": "gm"})
        self.at(T0 + 24 * HOUR)
        with self.assertRaises(hand.Refused):
            publish.post({"text": "GM"})
        publish.post({"text": "gn"})
        self.assertEqual(len(self.posted()), 2)

    def test_a_reply_counts_as_something_already_said(self):
        # Teeny's replies are most of what it writes: an own post may not
        # repeat one either.
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        posting_rules.record_post(conn, "90", "reply", self.YESTERDAY, in_reply_to="5", created_at=T0 - HOUR)
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"text": self.YESTERDAY})
        self.assertEqual((caught.exception.detail["rule"], caught.exception.detail["similar_to"]), ("repeat", "90"))
        self.assertEqual(self.posted(), [])

    def test_a_rehearsal_is_refused_the_same(self):
        publish.post({"text": self.YESTERDAY})
        self.configure(DRY_RUN="1")
        self.at(T0 + HOUR)
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"text": self.YESTERDAY})
        self.assertEqual(caught.exception.detail["rule"], "repeat")
