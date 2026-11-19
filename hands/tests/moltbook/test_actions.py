"""hands/moltbook's acting verbs: the guard on every word, the limits (and
new-agent mode), the comment rules, consent for collaborations, the crypto
rule, the verification challenge and its lockout, DRY_RUN and the ledger,
against the fake Moltbook."""

from __future__ import annotations

import json
import os

from guard.support import FakeCore, result
from .support import CORRECTED, DAY, HOUR, KEY, MINUTE, T0, UNREADABLE, Scratch, verification
from teeny_hands import hand
from teeny_hands.moltbook import actions, reads, rules


#: Moltbook ids are UUIDs; the beat's batch carries them whole.
FULL = "8e2395c9-1f2a-4c3d-9e8f-0a1b2c3d4e5f"
PARENT = "bab55589-6d7e-4f80-a1b2-c3d4e5f60718"


def rows(table: str) -> list[dict]:
    conn = hand.open_db(rules.SCHEMA)
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


def failures() -> int:
    conn = hand.open_db(rules.SCHEMA)
    return rules.failures(conn)


class Refusing:
    def refused(self, call, body, rule):
        with self.assertRaises(hand.Refused) as caught:
            call(body)
        self.assertEqual(caught.exception.detail.get("rule"), rule, caught.exception.reason)
        return caught.exception


class PostTest(Scratch, Refusing):
    def body(self, **more):
        # Every post after the first says something new: the same words twice
        # are a retelling (retell.py, `retold`), and these tests are about the
        # limits.
        self.sent = getattr(self, "sent", -1) + 1
        if self.sent:
            return {"submolt": "general", "title": f"t{self.sent:03d}x", "body": f"b{self.sent:03d}x", **more}
        return {"submolt": "general", "title": "hello — moltbook", "body": "first post", **more}

    def test_publishes_the_guards_rewrite_and_records_it(self):
        self.created()
        out = actions.post(self.body())
        self.assertEqual(out["posted"], "p1")
        sent = self.mb.requests("POST", "/posts")[0]
        self.assertEqual(sent.json(), {"submolt_name": "general", "title": "hello, moltbook", "content": "first post", "type": "text"})
        self.assertEqual(sent.authorization, f"Bearer {KEY}")
        self.assertEqual([(r["post_id"], r["status"]) for r in rows("moltbook_posts")], [("p1", "posted")])

    def test_the_daily_cap_defaults_to_twenty_with_the_thirty_minute_gap(self):
        self.created()
        for i in range(20):
            self.at(T0 + i * 31 * MINUTE)
            actions.post(self.body())
        self.at(T0 + 21 * 31 * MINUTE)
        self.refused(actions.post, self.body(), "daily_cap")

    def test_a_format_tag_is_recorded_and_an_experiment_id_accepted(self):
        self.created()
        actions.post(self.body(format="Confession", experiment=3))
        self.assertEqual([r["format"] for r in rows("moltbook_posts")], ["confession"])
        self.at(T0 + HOUR)
        actions.post(self.body())
        self.assertEqual([r["format"] for r in rows("moltbook_posts")], ["confession", None])

    def test_a_bad_format_tag_is_an_error_and_sends_nothing(self):
        with self.assertRaises(hand.HandError):
            actions.post(self.body(format="not a tag!"))
        self.assertEqual(self.mb.seen, [])

    def test_a_ledger_made_before_the_format_column_gains_it(self):
        conn = hand.db()
        conn.execute("CREATE TABLE moltbook_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id TEXT, at REAL NOT NULL,"
                     " submolt TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}')")
        conn.commit()
        conn.close()
        self.created()
        actions.post(self.body(format="claim"))
        self.assertEqual([r["format"] for r in rows("moltbook_posts")], ["claim"])

    def test_dry_run_touches_nothing(self):
        self.configure(DRY_RUN="1")
        out = actions.post(self.body())
        self.assertEqual((out["dry_run"], out["would_post"]["title"]), (True, "hello, moltbook"))
        self.assertEqual(self.mb.seen, [])
        self.assertEqual(rows("moltbook_posts"), [])

    def test_a_held_title_publishes_nothing(self):
        with self.assertRaises(hand.Refused) as caught:
            actions.post(self.body(title="this is fucking great"))
        self.assertEqual(caught.exception.reason, "held")
        self.assertEqual(self.mb.seen, [])

    def test_a_link_post_must_be_on_the_allowlist(self):
        self.refused(actions.post, {"submolt": "general", "title": "look", "url": "https://evil.example/x"}, "link")
        self.assertEqual(self.mb.seen, [])
        self.created()
        actions.post({"submolt": "general", "title": "my code", "url": "https://github.com/teenydroid/hello"})
        self.assertEqual(self.mb.requests("POST", "/posts")[0].json()["type"], "link")

    def test_one_post_per_30_minutes_when_established(self):
        self.created()
        actions.post(self.body())
        self.at(T0 + 29 * MINUTE)
        error = self.refused(actions.post, self.body(), "rate_limit")
        self.assertGreater(error.detail["wait_seconds"], 0)
        self.at(T0 + 31 * MINUTE)
        actions.post(self.body())
        self.assertEqual(len(self.mb.requests("POST", "/posts")), 2)

    def test_new_agent_mode_is_one_post_per_two_hours(self):
        self.configure(MOLTBOOK_CLAIMED_AT=str(T0 - HOUR))
        self.created()
        out = actions.post(self.body())
        self.at(T0 + 90 * MINUTE)
        self.refused(actions.post, self.body(), "rate_limit")
        self.at(T0 + 2 * HOUR + 1)
        actions.post(self.body())
        # Past the first day, the established gap.
        self.at(T0 + DAY + 1)
        actions.post(self.body())
        self.assertEqual(len(self.mb.requests("POST", "/posts")), 3)
        self.assertIn("posted", out)

    def test_without_a_claim_date_the_first_post_starts_the_clock(self):
        self.configure(MOLTBOOK_CLAIMED_AT=None)
        self.configure(DRY_RUN="1", MOLTBOOK_CLAIMED_AT=None)
        self.assertTrue(actions.post(self.body())["new_agent"])
        self.configure(MOLTBOOK_CLAIMED_AT=None)
        self.created()
        actions.post(self.body())
        self.at(T0 + DAY + HOUR)
        self.configure(DRY_RUN="1", MOLTBOOK_CLAIMED_AT=None)
        self.assertFalse(actions.post(self.body())["new_agent"])

    def test_our_daily_cap(self):
        self.configure(MOLTBOOK_MAX_POSTS_PER_DAY="2")
        self.created()
        for i in range(2):
            self.at(T0 + i * HOUR)
            actions.post(self.body())
        self.at(T0 + 3 * HOUR)
        self.refused(actions.post, self.body(), "daily_cap")
        self.at(T0 + DAY + 1)
        actions.post(self.body())

    def test_a_collaboration_needs_consent_on_record(self):
        self.refused(actions.post, self.body(body="built this with u/crabby"), "collab")
        error = self.refused(actions.post, self.body(title="a collab with u/crabby and u/shelly"), "collab")
        self.assertEqual(error.detail["names"], ["crabby", "shelly"])
        self.refused(actions.post, self.body(body="our collab is live"), "collab")
        self.assertEqual(self.mb.seen, [])
        rules.collab_add("crabby", "said yes in m/general, 2026-09-13")
        self.created()
        actions.post(self.body(body="built this with u/crabby"))
        self.assertEqual(len(self.mb.requests("POST", "/posts")), 1)
        # Mentioning someone without claiming a collaboration needs nothing.
        self.at(T0 + HOUR)
        actions.post(self.body(body="thanks u/shelly for the tip"))

    def test_crypto_goes_only_where_the_submolt_allows_it(self):
        self.submolt("general", False)
        self.refused(actions.post, self.body(body="the $TINY chart is up"), "crypto")
        self.submolt("aithoughts", None)
        self.refused(actions.post, self.body(submolt="aithoughts", body="minting an NFT"), "crypto")
        self.assertEqual(self.mb.writes(), [])
        self.submolt("defi", True)
        self.created()
        actions.post(self.body(submolt="defi", body="TINY is a token on solana"))
        # Plain words never ask about the submolt.
        self.at(T0 + HOUR)
        before = len(self.mb.requests("GET"))
        actions.post(self.body(body="tiny humans, big plans"))
        self.assertEqual(len(self.mb.requests("GET")), before)

    def test_a_confident_challenge_is_answered(self):
        self.created(ver=verification())
        self.verified()
        out = actions.post(self.body())
        self.assertEqual((out["verification"], out["answer"]), ("verified", "15.00"))
        self.assertEqual(self.mb.requests("POST", "/verify")[0].json(), {"verification_code": "moltbook_verify_abc", "answer": "15.00"})
        self.assertEqual(rows("moltbook_posts")[0]["status"], "verified")
        self.assertEqual(failures(), 0)

    def test_an_unreadable_challenge_is_never_guessed(self):
        self.created(ver=verification(UNREADABLE))
        error = self.refused(actions.post, self.body(), "verification")
        self.assertEqual(self.mb.requests("POST", "/verify"), [])
        pending = rows("moltbook_pending")
        self.assertEqual([(p["status"], p["content_id"], p["kind"]) for p in pending], [("needs_verification", "p1", "post")])
        self.assertEqual(error.detail["pending_id"], pending[0]["id"])
        self.assertEqual(rows("moltbook_posts")[0]["status"], "needs_verification")
        self.assertEqual(failures(), 1)

    def wire_model(self, *replies):
        fake = FakeCore(*replies)
        self.addCleanup(fake.close)
        (self.root / "state").mkdir(exist_ok=True)
        (self.root / "state" / "core.url").write_text(fake.url + "\n")
        (self.root / "secrets" / "core.token").write_text("tok\n")
        self.configure(MOLTBOOK_CHALLENGE_MODEL="1")
        return fake

    def test_a_challenge_the_parser_refuses_is_answered_by_two_agreeing_model_reads(self):
        fake = self.wire_model(result({"response": json.dumps({"a": 23, "op": "*", "b": 7})}))
        self.created(ver=verification(CORRECTED))
        self.verified()
        out = actions.post(self.body())
        self.assertEqual((out["verification"], out["answer"]), ("verified", "161.00"))
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(self.mb.requests("POST", "/verify")[0].json()["answer"], "161.00")
        self.assertEqual(rows("moltbook_posts")[0]["status"], "verified")
        self.assertEqual(rows("moltbook_pending"), [])
        self.assertEqual(failures(), 0)

    def test_model_reads_that_disagree_send_nothing_and_count_a_failure(self):
        self.wire_model(
            result({"response": json.dumps({"a": 23, "op": "*", "b": 7})}),
            result({"response": json.dumps({"a": 50, "op": "*", "b": 7})}),
        )
        self.created(ver=verification(CORRECTED))
        self.refused(actions.post, self.body(), "verification")
        self.assertEqual(self.mb.requests("POST", "/verify"), [])
        self.assertEqual(rows("moltbook_pending")[0]["status"], "needs_verification")
        self.assertEqual(failures(), 1)

    def test_a_wrong_answer_counts_and_three_lock_every_write(self):
        self.configure(MOLTBOOK_MAX_POSTS_PER_DAY="10")
        self.verified(ok=False, status=400)
        for i in range(3):
            self.at(T0 + i * HOUR)
            self.created(ver=verification())
            error = self.refused(actions.post, self.body(), "verification_failed")
            self.assertEqual(error.detail["consecutive_failures"], i + 1)
        self.at(T0 + 5 * HOUR)
        posts = len(self.mb.requests("POST", "/posts"))
        self.refused(actions.post, self.body(), "verification_lockout")
        self.refused(actions.comment, {"post_id": "x1", "body": "hi"}, "verification_lockout")
        self.assertEqual(len(self.mb.requests("POST", "/posts")), posts)
        self.assertEqual(actions.unlock(), {"unlocked": True, "consecutive_failures_were": 3})
        self.created()
        actions.post(self.body())

    def test_a_correct_answer_resets_the_count(self):
        self.created(ver=verification(UNREADABLE))
        self.refused(actions.post, self.body(), "verification")
        self.assertEqual(failures(), 1)
        self.at(T0 + HOUR)
        self.created(ver=verification())
        self.verified()
        actions.post(self.body())
        self.assertEqual(failures(), 0)

    def test_an_expired_challenge_is_a_failure(self):
        self.created(ver=verification())
        self.mb.route("POST", "/verify", {"success": False, "error": "expired"}, status=410)
        self.refused(actions.post, self.body(), "verification_failed")
        self.assertEqual(failures(), 1)

    def test_a_429_is_refused_with_the_wait(self):
        self.mb.route("POST", "/posts", {"statusCode": 429, "message": "Rate limit exceeded", "retry_after_minutes": 12},
                      status=429, headers={"Retry-After": "720"})
        error = self.refused(actions.post, self.body(), "rate_limit")
        self.assertEqual(error.detail["wait_seconds"], 721)
        self.assertEqual(rows("moltbook_posts"), [])

    def test_the_write_rate(self):
        conn = hand.open_db(rules.SCHEMA)
        for i in range(30):
            conn.execute("INSERT INTO moltbook_actions (at, verb, target) VALUES (?, 'upvote-post', ?)", (T0 - 10, f"x{i}"))
        self.refused(actions.post, self.body(), "rate_limit")
        self.refused(actions.upvote, {"post_id": "y"}, "rate_limit")
        self.at(T0 + 61)
        self.created()
        actions.post(self.body())

    def test_a_post_refused_for_the_gap_never_reaches_the_guard(self):
        # A post the beat holds for the gap is sent again every beat until it
        # opens (bin/lib/tick.py, PACED): the limits come first, so a refusal
        # for the gap spends no guard judge turn.
        from unittest import mock

        self.created()
        actions.post(self.body())
        self.at(T0 + 12 * MINUTE)
        with mock.patch.object(actions, "check_fields", side_effect=AssertionError("the guard ran")) as guard:
            error = self.refused(actions.post, self.body(), "rate_limit")
        guard.assert_not_called()
        self.assertEqual(error.detail["wait_seconds"], 18 * 60 + 1)

    def test_bad_bodies(self):
        for body in (None, {}, {"submolt": "general"}, {"submolt": "General Chat", "title": "x"},
                     {"submolt": "general", "title": "x", "type": "video"}, {"submolt": "general", "title": "x", "type": "link"},
                     {"submolt": "general", "title": "x", "extra": 1}):
            with self.assertRaises(hand.HandError, msg=body):
                actions.post(body)


class CommentTest(Scratch, Refusing):
    def test_a_hold_the_judge_made_by_failing_says_so(self):
        # bin/lib/tick.py sends such an answer again next beat; a real hold never.
        from unittest import mock
        from guard.support import FakeCore, error
        from teeny_hands.guard import judge

        core = FakeCore(error("model error: local-openai returned: provider stream ended before a completion signal"))
        self.addCleanup(core.close)
        (self.root / "state").mkdir(exist_ok=True)
        (self.root / "state" / "core.url").write_text(core.url + "\n")
        (self.root / "secrets" / "core.token").write_text("tok\n")
        self.configure(SAFETY_JUDGE="1")
        self.a_post("x1")
        with mock.patch.object(judge, "RETRY_BACKOFFS", (0.0, 0.0, 0.0, 0.0)), self.assertRaises(hand.Refused) as caught:
            actions.comment({"post_id": "x1", "body": "nice"})
        self.assertEqual(caught.exception.reason, "held")
        self.assertEqual((caught.exception.detail["category"], caught.exception.detail.get("judge_failed")), ("unknown", True))

    def test_comments_with_the_guards_rewrite(self):
        self.a_post("x1")
        self.created("comment", "c1", path="/posts/x1/comments")
        out = actions.comment({"post_id": "x1", "body": "nice — really"})
        self.assertEqual(out["commented"], "c1")
        self.assertEqual(self.mb.requests("POST", "/posts/x1/comments")[0].json(), {"content": "nice, really"})
        self.assertEqual(rows("moltbook_comments")[0]["post_id"], "x1")

    def test_dry_run_touches_nothing_written(self):
        self.configure(DRY_RUN="1")
        self.a_post("x1")
        out = actions.comment({"post_id": "x1", "body": "nice"})
        self.assertEqual(out["would_comment"], {"post_id": "x1", "body": "nice"})
        self.assertEqual(self.mb.writes(), [])
        self.assertEqual(rows("moltbook_comments"), [])

    def test_never_twice_on_one_post(self):
        self.a_post("x1")
        self.created("comment", "c1", path="/posts/x1/comments")
        actions.comment({"post_id": "x1", "body": "nice"})
        self.at(T0 + HOUR)
        self.refused(actions.comment, {"post_id": "x1", "body": "also"}, "once_per_post")

    def test_unless_answering_a_reply_to_teeny(self):
        self.a_post("x1")
        self.created("comment", "c1", path="/posts/x1/comments")
        actions.comment({"post_id": "x1", "body": "nice"})
        tree = [
            {"id": "c1", "author": {"name": "teenydroid"}, "replies": [{"id": "r1", "author": {"name": "crabby"}, "replies": []}]},
            {"id": "o1", "author": {"name": "shelly"}, "replies": []},
        ]
        self.a_post("x1", comments=tree)
        self.created("comment", "c2", path="/posts/x1/comments")
        self.at(T0 + HOUR)
        actions.comment({"post_id": "x1", "body": "thanks", "parent_id": "r1"})
        self.at(T0 + 2 * HOUR)
        self.refused(actions.comment, {"post_id": "x1", "body": "again", "parent_id": "r1"}, "once_per_parent")
        self.refused(actions.comment, {"post_id": "x1", "body": "hey", "parent_id": "o1"}, "once_per_post")
        self.refused(actions.comment, {"post_id": "x1", "body": "me", "parent_id": "c1"}, "own_comment")
        self.refused(actions.comment, {"post_id": "x1", "body": "?", "parent_id": "zz"}, "unknown_parent")

    def test_a_reply_under_a_comment_at_the_drawing_depth_is_refused(self):
        # Moltbook draws a thread five levels down; a reply below that is never seen.
        def chain(depth: int) -> list:
            node: dict = {"id": f"d{depth}", "author": {"name": "crabby"}, "replies": []}
            for level in range(depth - 1, 0, -1):
                node = {"id": f"d{level}", "author": {"name": "crabby"}, "replies": [node]}
            return [node]

        self.a_post("x1", comments=chain(5))
        self.created("comment", "c1", path="/posts/x1/comments")
        self.refused(actions.comment, {"post_id": "x1", "body": "late", "parent_id": "d5"}, "too_deep")
        self.assertEqual(self.mb.writes(), [])
        self.assertEqual(actions.comment({"post_id": "x1", "body": "in time", "parent_id": "d4"})["commented"], "c1")

    def test_a_short_post_id_is_refused_before_anything_is_sent(self):
        # Live, 2026-11-13: the mind copied "post 8e2395c9" out of its own
        # memory; Moltbook answered a bare 400 and the beat said only "error".
        caught = self.refused(actions.comment, {"post_id": "8e2395c9", "body": "nice", "parent_id": FULL}, "short_id")
        self.assertEqual((caught.detail.get("rewrite"), caught.detail.get("field")), (True, "post_id"))
        self.assertIn("8e2395c9", caught.reason)
        self.assertIn("full id", caught.reason)
        self.assertEqual(self.mb.seen, [])
        self.assertEqual(rows("moltbook_comments"), [])

    def test_a_short_parent_id_is_refused_before_anything_is_sent(self):
        for short in ("bab55589", "BAB55589-1F2A", "bab55589-1f2a-4c3d-9e8f-0a1b2c3d4e5"):
            caught = self.refused(actions.comment, {"post_id": FULL, "body": "thanks", "parent_id": short}, "short_id")
            self.assertEqual(caught.detail.get("field"), "parent_id")
        self.assertEqual(self.mb.seen, [])

    def test_full_ids_pass_the_short_id_rule(self):
        self.configure(DRY_RUN="1")
        self.a_post(FULL, comments=[{"id": PARENT, "author": {"name": "crabby"}, "replies": []}])
        out = actions.comment({"post_id": FULL, "body": "thanks", "parent_id": PARENT})
        self.assertEqual(out["would_comment"], {"post_id": FULL, "body": "thanks", "parent_id": PARENT})

    # Live, 2026-11-15: the mind put a comment's id in post_id twice; Moltbook
    # answered a bare 404 and the mind parked two replies it owed as "dead ids".
    THEIRS = "b2e848b7-5960-497a-82f7-7bc4129f572d"
    ITS_POST = "8f817b7b-e26d-425a-bf6f-e81f25292c2c"

    def test_a_comment_id_from_the_inbox_as_post_id_is_refused_with_its_post(self):
        from teeny_hands import stimuli

        stimuli.add("moltbook", "comment", self.THEIRS, "a ruling, please", who="ironheights",
                    payload={"post_id": self.ITS_POST, "parent_id": PARENT, "depth": 2, "can_answer": True})
        caught = self.refused(actions.comment, {"post_id": self.THEIRS, "body": "the ruling"}, "comment_as_post")
        self.assertEqual((caught.detail.get("rewrite"), caught.detail.get("field")), (True, "post_id"))
        self.assertEqual(caught.detail.get("its_post_id"), self.ITS_POST)
        self.assertIn(f"{self.THEIRS} is a comment, not a post", caught.reason)
        self.assertIn(f'"parent_id": "{self.THEIRS}"', caught.reason)
        self.assertIn(f'"post_id": "{self.ITS_POST}"', caught.reason)
        self.assertEqual(self.mb.seen, [])

    def test_teenys_own_comment_id_as_post_id_is_refused_with_its_post(self):
        conn = hand.open_db(rules.SCHEMA)
        conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES (?, ?, ?, 'posted')",
                     (self.THEIRS, self.ITS_POST, T0 - DAY))
        caught = self.refused(actions.comment, {"post_id": self.THEIRS, "body": "more"}, "comment_as_post")
        self.assertEqual(caught.detail.get("its_post_id"), self.ITS_POST)
        self.assertEqual(self.mb.seen, [])

    def test_a_comment_id_from_the_read_cache_as_post_id_is_refused_with_its_post(self):
        reply = {"id": self.THEIRS, "post_id": self.ITS_POST, "author": {"name": "xiaoyaonight"}, "replies": []}
        self.a_post(self.ITS_POST, comments=[{"id": PARENT, "author": {"name": "crabby"}, "replies": [reply]}])
        from teeny_hands.moltbook import reads

        reads.comments(self.ITS_POST)  # the read that put it in the cache
        seen = len(self.mb.seen)
        caught = self.refused(actions.comment, {"post_id": self.THEIRS, "body": "yes"}, "comment_as_post")
        self.assertEqual(caught.detail.get("its_post_id"), self.ITS_POST)
        self.assertEqual(len(self.mb.seen), seen)

    def test_a_known_comment_id_whose_post_is_unknown_says_so(self):
        conn = hand.open_db(rules.SCHEMA)
        from teeny_hands import stimuli

        stimuli.add("moltbook", "comment", self.THEIRS, "hi", payload={"on_own_post": False})
        caught = self.refused(actions.comment, {"post_id": self.THEIRS, "body": "yes"}, "comment_as_post")
        self.assertIsNone(caught.detail.get("its_post_id"))
        self.assertIn("is a comment, not a post", caught.reason)
        self.assertIn("its post's id is not known here", caught.reason)
        self.assertEqual(self.mb.seen, [])

    def test_a_post_id_as_parent_id_is_refused(self):
        from teeny_hands import stimuli

        stimuli.add("moltbook", "comment", self.THEIRS, "hi", payload={"post_id": self.ITS_POST})
        caught = self.refused(actions.comment, {"post_id": self.ITS_POST, "body": "yes", "parent_id": self.ITS_POST},
                              "post_as_parent")
        self.assertEqual((caught.detail.get("rewrite"), caught.detail.get("field")), (True, "parent_id"))
        self.assertIn(f"{self.ITS_POST} is a post, not a comment", caught.reason)
        caught = self.refused(actions.comment, {"post_id": FULL, "body": "yes", "parent_id": self.ITS_POST},
                              "post_as_parent")
        self.assertIn(f'"post_id": "{self.ITS_POST}"', caught.reason)
        self.assertEqual(self.mb.seen, [])

    def test_an_unknown_id_moltbook_404s_explains_the_404(self):
        with self.assertRaises(hand.HandError) as caught:
            actions.comment({"post_id": self.THEIRS, "body": "the ruling"})
        message = str(caught.exception)
        self.assertIn(f"Moltbook has no post with the id {self.THEIRS} (404)", message)
        self.assertIn("parent_id", message)
        self.assertEqual(self.mb.writes(), [])

    def test_a_correct_answer_to_a_known_comment_is_unchanged(self):
        from teeny_hands import stimuli

        stimuli.add("moltbook", "comment", self.THEIRS, "a ruling, please",
                    payload={"post_id": self.ITS_POST, "parent_id": PARENT})
        self.a_post(self.ITS_POST, comments=[{"id": PARENT, "author": {"name": "teenydroid"},
                                              "replies": [{"id": self.THEIRS, "author": {"name": "ironheights"}, "replies": []}]}])
        conn = hand.open_db(rules.SCHEMA)
        conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES (?, ?, ?, 'posted')",
                     (PARENT, self.ITS_POST, T0 - DAY))
        self.created("comment", "c9", path=f"/posts/{self.ITS_POST}/comments")
        out = actions.comment({"post_id": self.ITS_POST, "body": "the ruling", "parent_id": self.THEIRS})
        self.assertEqual(out, {"commented": "c9", "post_id": self.ITS_POST, "parent_id": self.THEIRS})

    def test_own_post_only_its_first_comment(self):
        self.a_post("mine", author="teenydroid")
        self.created("comment", "c1", path="/posts/mine/comments")
        actions.comment({"post_id": "mine", "body": "context: built in a night"})
        self.a_post("mine2", author="teenydroid", comments=[{"id": "o1", "author": {"name": "crabby"}, "replies": []}])
        self.at(T0 + HOUR)
        self.refused(actions.comment, {"post_id": "mine2", "body": "more"}, "own_post")
        # Answering a comment on its own post is answering a reply to Teeny.
        self.created("comment", "c2", path="/posts/mine2/comments")
        actions.comment({"post_id": "mine2", "body": "thanks", "parent_id": "o1"})

    def test_a_playbooks_comment_is_tagged_with_its_name_and_the_post_it_found(self):
        os.environ["TEENY_WRITER"] = "moltbook-threads"
        self.mb.route("GET", "/posts/x1", {"success": True, "post": {
            "id": "x1", "created_at": "2026-09-14T06:20:00Z", "comment_count": 4, "score": 7,
            "author": {"name": "shelly", "karma": 812, "followerCount": 31}, "submolt": {"name": "memory"}}})
        self.created("comment", "c1", path="/posts/x1/comments")
        actions.comment({"post_id": "x1", "body": "nice"})
        row = rows("moltbook_comments")[0]
        self.assertEqual(row["format"], "threads")
        self.assertEqual(json.loads(row["detail"]), {
            "own_post": False, "writer": "moltbook-threads", "post_age_minutes": 40.0, "post_comments": 4,
            "post_score": 7, "post_author": "shelly", "author_karma": 812, "author_followers": 31})

    def test_the_minds_comment_is_tagged_by_where_it_went(self):
        os.environ["TEENY_WRITER"] = "mind"
        self.a_post("mine", author="teenydroid", comments=[{"id": "o1", "author": {"name": "crabby"}, "replies": []}])
        self.created("comment", "c1", path="/posts/mine/comments")
        actions.comment({"post_id": "mine", "body": "thanks", "parent_id": "o1"})
        self.a_post("x1", comments=[{"id": "o2", "author": {"name": "crabby"}, "replies": []}])
        self.created("comment", "c2", path="/posts/x1/comments")
        self.at(T0 + HOUR)
        actions.comment({"post_id": "x1", "body": "hm", "parent_id": "o2"})
        os.environ.pop("TEENY_WRITER")
        self.a_post("x2")
        self.created("comment", "c3", path="/posts/x2/comments")
        self.at(T0 + 2 * HOUR)
        actions.comment({"post_id": "x2", "body": "a detail"})
        got = [(r["format"], json.loads(r["detail"])) for r in rows("moltbook_comments")]
        self.assertEqual(got, [
            ("own-thread", {"own_post": True, "writer": "mind", "post_author": "teenydroid"}),
            ("reply", {"own_post": False, "writer": "mind", "post_author": "someone"}),
            ("comment", {"own_post": False, "post_author": "someone"}),
        ])

    def test_a_format_the_caller_names_wins_and_a_dry_run_says_it(self):
        os.environ["TEENY_WRITER"] = "moltbook-helpdesk"
        self.configure(DRY_RUN="1")
        self.a_post("x1")
        self.assertEqual(actions.comment({"post_id": "x1", "body": "nice"})["format"], "helpdesk")
        self.assertEqual(actions.comment({"post_id": "x1", "body": "nice", "format": "answer"})["format"], "answer")

    # The comment age backstop (docs/audit/moltbook-plan.md, section 4): 34 of
    # 45 comments on other agents' posts went onto posts over a day old.
    def dated_post(self, post_id: str, hours_old: float, author: str = "someone", comments: list | None = None, **more) -> None:
        from datetime import datetime, timezone

        at = datetime.fromtimestamp(T0 - hours_old * HOUR, timezone.utc).isoformat().replace("+00:00", "Z")
        self.mb.route("GET", f"/posts/{post_id}", {"success": True, "post": {
            "id": post_id, "author": {"name": author}, "submolt": {"name": "general"}, "created_at": at, **more}})
        self.mb.route("GET", f"/posts/{post_id}/comments", {"success": True, "comments": comments or []})

    def test_a_top_level_comment_on_an_old_post_is_refused(self):
        self.dated_post("old1", 45.7)
        caught = self.refused(actions.comment, {"post_id": "old1", "body": "late to this"}, "too_old")
        self.assertEqual((caught.detail["age_hours"], caught.detail["limit_hours"]), (45.7, 6))
        self.assertEqual(self.mb.writes(), [])
        self.configure(DRY_RUN="1")
        self.refused(actions.comment, {"post_id": "old1", "body": "late to this"}, "too_old")

    def test_a_young_post_takes_a_comment(self):
        self.dated_post("new1", 2)
        self.created("comment", "c1", path="/posts/new1/comments")
        self.assertEqual(actions.comment({"post_id": "new1", "body": "same here: 7 hours down"})["commented"], "c1")

    def test_the_age_limit_is_a_setting(self):
        self.configure(MOLTBOOK_MAX_COMMENT_AGE_HOURS="48")
        self.dated_post("old1", 45.7)
        self.created("comment", "c1", path="/posts/old1/comments")
        self.assertEqual(actions.comment({"post_id": "old1", "body": "a detail"})["commented"], "c1")
        self.configure(MOLTBOOK_MAX_COMMENT_AGE_HOURS="1")
        self.dated_post("mid", 2)
        self.at(T0 + MINUTE)
        self.refused(actions.comment, {"post_id": "mid", "body": "a detail"}, "too_old")

    def test_a_reply_on_an_old_post_is_exempt(self):
        # Answering a comment said to Teeny is part B's path: age never stops it.
        tree = [{"id": "c1", "author": {"name": "teenydroid"}, "replies": [{"id": "r1", "author": {"name": "crabby"}, "replies": []}]}]
        self.dated_post("old1", 80, comments=tree)
        conn = hand.open_db(rules.SCHEMA)
        conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES ('c1', 'old1', ?, 'posted')", (T0 - 3 * DAY,))
        self.created("comment", "c2", path="/posts/old1/comments")
        out = actions.comment({"post_id": "old1", "body": "thanks", "parent_id": "r1"})
        self.assertEqual(out["commented"], "c2")

    def test_teenys_own_old_post_is_exempt(self):
        self.dated_post("mine", 72, author="teenydroid", comments=[{"id": "o1", "author": {"name": "crabby"}, "replies": []}])
        self.created("comment", "c1", path="/posts/mine/comments")
        self.assertEqual(actions.comment({"post_id": "mine", "body": "thanks", "parent_id": "o1"})["commented"], "c1")
        # Its first comment on its own post, old or not, is not refused for age.
        self.dated_post("mine2", 72, author="teenydroid")
        self.created("comment", "c2", path="/posts/mine2/comments")
        self.at(T0 + MINUTE)
        self.assertEqual(actions.comment({"post_id": "mine2", "body": "context"})["commented"], "c2")

    def test_an_old_post_that_names_teeny_is_exempt(self):
        self.dated_post("ment", 30, title="question for teenydroid", content="@teenydroid how do you hold drafts?")
        self.created("comment", "c1", path="/posts/ment/comments")
        self.assertEqual(actions.comment({"post_id": "ment", "body": "the guard holds them"})["commented"], "c1")
        # A longer name that only contains Teeny's is not a mention.
        self.dated_post("other", 30, content="teenydroid_fan says hi")
        self.at(T0 + MINUTE)
        self.refused(actions.comment, {"post_id": "other", "body": "hi"}, "too_old")

    def test_comment_gap_and_new_agent_mode(self):
        for name in ("x1", "x2", "x3"):
            self.a_post(name)
            self.created("comment", f"c-{name}", path=f"/posts/{name}/comments")
        actions.comment({"post_id": "x1", "body": "a"})
        self.at(T0 + 10)
        self.refused(actions.comment, {"post_id": "x2", "body": "b"}, "rate_limit")
        self.at(T0 + 21)
        actions.comment({"post_id": "x2", "body": "b"})
        self.configure(MOLTBOOK_CLAIMED_AT=str(T0 - HOUR))
        self.at(T0 + 50)
        self.refused(actions.comment, {"post_id": "x3", "body": "c"}, "rate_limit")
        self.at(T0 + 82)
        actions.comment({"post_id": "x3", "body": "c"})

    def test_daily_caps(self):
        self.configure(MOLTBOOK_MAX_COMMENTS_PER_DAY="3")
        conn = hand.open_db(rules.SCHEMA)
        for i in range(3):
            conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES (?, ?, ?, 'posted')", (f"c{i}", f"p{i}", T0 - HOUR))
        self.a_post("x1")
        self.refused(actions.comment, {"post_id": "x1", "body": "a"}, "daily_cap")
        # A new agent's 20 a day wins over a higher setting.
        self.configure(MOLTBOOK_MAX_COMMENTS_PER_DAY="50", MOLTBOOK_CLAIMED_AT=str(T0 - HOUR))
        for i in range(3, 20):
            conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES (?, ?, ?, 'posted')", (f"c{i}", f"p{i}", T0 - HOUR))
        self.refused(actions.comment, {"post_id": "x1", "body": "a"}, "daily_cap")

    def test_the_comment_allowance_says_what_is_left_and_when_the_next_frees(self):
        self.configure(MOLTBOOK_MAX_COMMENTS_PER_DAY="3")
        conn = hand.open_db(rules.SCHEMA)
        self.assertEqual(rules.comment_allowance(), {"left": 3, "cap": 3})
        for i, ago in enumerate((20 * HOUR, 10 * HOUR, HOUR)):
            conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES (?, ?, ?, 'posted')",
                         (f"c{i}", f"p{i}", T0 - ago))
        # A comment refused at Moltbook never counted, and does not here.
        conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES ('r', 'p', ?, 'failed')", (T0,))
        self.assertEqual(rules.comment_allowance(), {"left": 0, "cap": 3, "frees_at": T0 - 20 * HOUR + DAY})
        self.at(T0 - 20 * HOUR + DAY + 1)
        self.assertEqual(rules.comment_allowance(), {"left": 1, "cap": 3})

    def test_crypto_comment_in_a_submolt_that_forbids_it(self):
        self.a_post("x1", submolt="general")
        self.submolt("general", False)
        self.refused(actions.comment, {"post_id": "x1", "body": "buy $TINY"}, "crypto")
        self.assertEqual(self.mb.writes(), [])

    def test_a_comment_challenge_left_pending(self):
        self.a_post("x1")
        self.created("comment", "c1", ver=verification(UNREADABLE), path="/posts/x1/comments")
        self.refused(actions.comment, {"post_id": "x1", "body": "nice"}, "verification")
        self.assertEqual(rows("moltbook_pending")[0]["kind"], "comment")


class SmallActionsTest(Scratch, Refusing):
    def test_upvote_once_and_never_own(self):
        self.mb.route("POST", "/posts/x1/upvote", {"success": True})
        self.assertEqual(actions.upvote({"post_id": "x1"}), {"upvoted": {"post_id": "x1"}})
        self.refused(actions.upvote, {"post_id": "x1"}, "duplicate")
        self.mb.route("POST", "/comments/k1/upvote", {"success": True})
        actions.upvote({"comment_id": "k1"})
        conn = hand.open_db(rules.SCHEMA)
        conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES ('mine', ?, 'g', 't', 'posted')", (T0,))
        self.refused(actions.upvote, {"post_id": "mine"}, "own_post")
        with self.assertRaises(hand.HandError):
            actions.upvote({"post_id": "a", "comment_id": "b"})
        self.assertEqual(len(rows("moltbook_actions")), 2)

    def test_a_short_id_upvote_is_refused_before_anything_is_sent(self):
        self.refused(actions.upvote, {"post_id": "8e2395c9"}, "short_id")
        self.refused(actions.upvote, {"comment_id": "bab55589"}, "short_id")
        self.assertEqual(self.mb.seen, [])

    def test_a_comment_id_upvoted_as_a_post_is_refused(self):
        from teeny_hands import stimuli

        theirs, its_post = "b2e848b7-5960-497a-82f7-7bc4129f572d", "8f817b7b-e26d-425a-bf6f-e81f25292c2c"
        stimuli.add("moltbook", "comment", theirs, "hi", payload={"post_id": its_post})
        caught = self.refused(actions.upvote, {"post_id": theirs}, "comment_as_post")
        self.assertEqual((caught.detail.get("rewrite"), caught.detail.get("field")), (True, "post_id"))
        self.assertIn('"comment_id"', caught.reason)
        self.assertEqual(self.mb.seen, [])

    def test_dry_run_upvote(self):
        self.configure(DRY_RUN="1")
        self.assertEqual(actions.upvote({"post_id": "x1"}), {"dry_run": True, "would_upvote": {"post_id": "x1"}})
        self.assertEqual(self.mb.seen, [])
        self.assertEqual(rows("moltbook_actions"), [])

    def test_follow_and_subscribe(self):
        self.mb.route("POST", "/agents/crabby/follow", {"success": True})
        self.mb.route("POST", "/submolts/aithoughts/subscribe", {"success": True})
        self.assertEqual(actions.follow({"name": "crabby"}), {"followed": "crabby"})
        self.assertEqual(actions.subscribe({"submolt": "aithoughts"}), {"subscribed": "aithoughts"})
        self.refused(actions.follow, {"name": "Crabby"}, "duplicate")
        self.refused(actions.follow, {"name": "teenydroid"}, "own")

    def test_a_follow_needs_no_earlier_exchange(self):
        # Moltbook's top accounts follow thousands; Teeny follows whoever follows,
        # answers or posts well. The cap and the prune keep it honest.
        self.mb.route("POST", "/agents/stranger/follow", {"success": True})
        self.assertEqual(actions.follow({"name": "stranger"}), {"followed": "stranger"})

    def test_follows_have_a_daily_cap(self):
        self.configure(MOLTBOOK_MAX_FOLLOWS_PER_DAY="2")
        for name in ("a1", "b2", "c3"):
            self.mb.route("POST", f"/agents/{name}/follow", {"success": True})
        actions.follow({"name": "a1"})
        self.at(T0 + HOUR)
        actions.follow({"name": "b2"})
        self.refused(actions.follow, {"name": "c3"}, "daily_cap")
        self.at(T0 + DAY + HOUR)
        self.assertEqual(actions.follow({"name": "c3"}), {"followed": "c3"})

    def test_the_follow_cap_defaults_to_thirty(self):
        self.assertEqual((rules.max_follows_per_day(), rules.max_posts_per_day(), rules.max_comments_per_day()), (30, 20, 50))

    def test_unfollow_deletes_the_follow_and_it_is_never_taken_back(self):
        self.mb.route("POST", "/agents/crabby/follow", {"success": True})
        self.mb.route("DELETE", "/agents/crabby/follow", {"success": True})
        actions.follow({"name": "crabby"})
        self.assertEqual(actions.unfollow({"name": "crabby"}), {"unfollowed": "crabby"})
        self.assertEqual(len(self.mb.requests("DELETE", "/agents/crabby/follow")), 1)
        self.refused(actions.unfollow, {"name": "crabby"}, "duplicate")
        self.refused(actions.follow, {"name": "crabby"}, "duplicate")
        self.assertEqual(
            [(r["verb"], r["target"]) for r in rows("moltbook_actions")], [("follow", "crabby"), ("unfollow", "crabby")]
        )

    def test_dry_run_unfollow_sends_nothing(self):
        self.configure(DRY_RUN="1")
        self.assertEqual(actions.unfollow({"name": "crabby"}), {"dry_run": True, "would_unfollow": "crabby"})
        self.assertEqual((self.mb.seen, rows("moltbook_actions")), ([], []))

    def followed(self, name: str, days_ago: float, **profile) -> None:
        conn = hand.open_db(rules.SCHEMA)
        conn.execute("INSERT INTO moltbook_actions (at, verb, target) VALUES (?, 'follow', ?)", (T0 - days_ago * DAY, name))
        self.mb.route("GET", "/agents/profile", {"success": True, "agent": {"name": name, **profile}},
                      when=lambda seen, name=name: seen.query.get("name") == name)

    def test_prune_unfollows_only_old_follows_that_do_not_follow_back(self):
        self.followed("quiet", 9, follows_you=False)
        self.followed("friend", 9, follows_you=True)
        self.followed("mystery", 9)
        self.followed("fresh", 2, follows_you=False)
        self.mb.route("DELETE", "/agents/quiet/follow", {"success": True})
        out = actions.prune()
        self.assertEqual((out["unfollowed"], out["checked"], out["kept"]), (["quiet"], 3, 2))
        self.assertEqual([r.path for r in self.mb.requests("DELETE")], ["/agents/quiet/follow"])
        self.assertEqual(actions.prune()["unfollowed"], [])  # nothing left to do the next week

    def test_prune_never_unfollows_a_peer_who_does_not_follow_back(self):
        (self.root / "brain").mkdir(exist_ok=True)
        (self.root / reads.PEERS_FILE).write_text("Lilt\n")
        self.followed("lilt", 9, follows_you=False)
        self.followed("quiet", 9, follows_you=False)
        self.mb.route("DELETE", "/agents/quiet/follow", {"success": True})
        out = actions.prune()
        self.assertEqual((out["unfollowed"], out["spared"], out["kept"]), (["quiet"], ["lilt"], 0))
        self.assertEqual([r.path for r in self.mb.requests("DELETE")], ["/agents/quiet/follow"])

    def test_prune_never_unfollows_an_agent_with_a_recent_exchange(self):
        from teeny_hands import stimuli

        self.followed("talker", 9, follows_you=False)
        self.followed("oldtalk", 30, follows_you=False)
        self.mb.route("DELETE", "/agents/oldtalk/follow", {"success": True})
        stimuli.add("moltbook", "comment", "c1", "words", who="talker",
                    payload={"post_id": "p", "answering_teeny": True}, at=T0 - 3 * DAY)
        stimuli.add("moltbook", "comment", "c2", "words", who="oldtalk",
                    payload={"post_id": "p", "answering_teeny": True}, at=T0 - 20 * DAY)
        out = actions.prune()
        self.assertEqual((out["unfollowed"], out["spared"]), (["oldtalk"], ["talker"]))
        self.assertEqual([r.path for r in self.mb.requests("DELETE")], ["/agents/oldtalk/follow"])

    def test_stale_follows_reports_unknown_as_null(self):
        self.followed("mystery", 8)
        self.assertEqual(actions.stale_follows()["follows"], [{"name": "mystery", "followed_days_ago": 8, "follows_back": None}])

    def test_dry_run_prune_says_what_it_would_do(self):
        self.followed("quiet", 9, follows_you=False)
        self.configure(DRY_RUN="1")
        out = actions.prune()
        self.assertEqual(out["would_unfollow"], ["quiet"])
        self.assertEqual(self.mb.writes(), [])


class OperatorTest(Scratch, Refusing):
    def test_describe_sets_the_profile_text_through_the_guard(self):
        self.mb.route("PATCH", "/agents/me", {"success": True})
        out = actions.describe("Confident claims, zero vibes \u2014 argue with me")
        self.assertEqual(out["described"], "Confident claims, zero vibes, argue with me")
        self.assertEqual(self.mb.requests("PATCH", "/agents/me")[0].json(), {"description": out["described"]})

    def test_describe_under_dry_run_sends_nothing(self):
        self.configure(DRY_RUN="1")
        self.assertTrue(actions.describe("Argue with me")["dry_run"])
        self.assertEqual(self.mb.seen, [])


    def test_register_prints_the_key_once_and_stores_nothing(self):
        self.configure(MOLTBOOK_API_KEY=None)
        self.mb.route("POST", "/agents/register", {"agent": {"api_key": KEY, "claim_url": "https://www.moltbook.com/claim/x",
                                                             "verification_code": "reef-X4B2"}, "important": "save it"})
        out = actions.register("teenydroid", "an AI that codes — in public")
        self.assertEqual((out["api_key"], out["verification_code"]), (KEY, "reef-X4B2"))
        self.assertIn("MOLTBOOK_API_KEY", out["next"])
        sent = self.mb.requests("POST", "/agents/register")[0]
        self.assertEqual(sent.authorization, "")
        self.assertEqual(sent.json(), {"name": "teenydroid", "description": "an AI that codes, in public"})
        self.assertEqual(self.leaked(), [])

    def test_register_dry_run(self):
        self.configure(DRY_RUN="1")
        self.assertEqual(actions.register("teenydroid", "hi")["would_register"], "teenydroid")
        self.assertEqual(self.mb.seen, [])

    def test_the_operator_answers_a_pending_challenge(self):
        self.created(ver=verification(UNREADABLE))
        error = self.refused(actions.post, {"submolt": "general", "title": "t"}, "verification")
        self.verified()
        out = actions.verify(error.detail["pending_id"], "15")
        self.assertEqual((out["verified"], out["answer"]), (error.detail["pending_id"], "15.00"))
        self.assertEqual(rows("moltbook_posts")[0]["status"], "verified")
        self.assertEqual(rows("moltbook_pending")[0]["status"], "verified")
        self.assertEqual(failures(), 0)
        with self.assertRaises(hand.Refused):
            actions.verify(error.detail["pending_id"], "15")
        listing = actions.pending_list()
        self.assertNotIn("verification_code", listing["pending"][0])

    def test_collab_list_and_remove(self):
        rules.collab_add("crabby", "dm on moltbook")
        self.assertEqual([c["name"] for c in rules.collab_list()["collabs"]], ["crabby"])
        rules.collab_remove("crabby")
        self.assertEqual(rules.collab_list()["collabs"], [])


class PostSlotTest(Scratch):
    """The gate a posting playbook runs first (`hands/moltbook post-slot`):
    open, or empty with the reason, so bin/act skips before the model turn."""

    def post_at(self, at: float, status: str = "verified") -> None:
        conn = hand.open_db(rules.SCHEMA)
        conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES ('p', ?, 'general', 't', ?)",
                     (at, status))
        conn.commit()

    def test_open_when_no_post_holds_the_gap(self):
        self.assertEqual(rules.post_slot(), {"open": True, "new_agent": False})

    def test_the_12_13_case_a_closed_gap_is_empty_with_the_reason(self):
        # 2026-11-15: a post at 11:48 (hidden, needs_verification) held the
        # gap; moltbook-general drafted at 12:13 and the hand refused it (wait
        # 329s). The gate says so before any draft, and its answer is empty but
        # for `why`, which bin/act reads as a skip.
        self.post_at(T0 - 25 * MINUTE, status="needs_verification")
        slept = []
        out = rules.post_slot(wait=0, sleep=slept.append)
        self.assertEqual(set(out), {"why"})
        self.assertIn("rate_limit", out["why"])
        self.assertIn("wait 301s", out["why"])
        self.assertEqual(slept, [])

    def test_a_short_wait_is_sat_out_and_then_the_slot_is_open(self):
        self.post_at(T0 - 29 * MINUTE)
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            self.at(T0 + seconds)

        out = rules.post_slot(wait=120, sleep=sleep)
        self.assertEqual(out, {"open": True, "new_agent": False, "waited": 61})
        self.assertEqual(slept, [61])

    def test_a_wait_longer_than_allowed_is_not_sat_out(self):
        self.post_at(T0 - 10 * MINUTE)
        slept = []
        out = rules.post_slot(wait=120, sleep=slept.append)
        self.assertEqual(set(out), {"why"})
        self.assertEqual(slept, [])

    def test_the_daily_cap_and_the_lockout_close_it_too(self):
        self.configure(MOLTBOOK_MAX_POSTS_PER_DAY="1")
        self.post_at(T0 - 2 * HOUR)
        self.assertIn("daily_cap", rules.post_slot()["why"])
        conn = hand.open_db(rules.SCHEMA)
        rules.set_failures(conn, rules.LOCKOUT_AFTER)
        conn.commit()
        self.assertIn("verification_lockout", rules.post_slot()["why"])
