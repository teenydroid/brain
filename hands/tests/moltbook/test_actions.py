"""hands/moltbook's acting verbs: the guard on every word, the limits (and
new-agent mode), the comment rules, consent for collaborations, the crypto
rule, the verification challenge and its lockout, DRY_RUN and the ledger,
against the fake Moltbook."""

from __future__ import annotations

import json

from guard.support import FakeCore, result
from .support import CORRECTED, DAY, HOUR, KEY, MINUTE, T0, UNREADABLE, Scratch, verification
from teeny_hands import hand
from teeny_hands.moltbook import actions, rules


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
        with mock.patch.object(judge, "RETRY_BACKOFFS", (0.0, 0.0)), self.assertRaises(hand.Refused) as caught:
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
