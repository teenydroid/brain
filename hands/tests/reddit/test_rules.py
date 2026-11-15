"""hands/reddit's rules beyond the caps: the anchor a post must name, the
age past which an unasked comment is refused, the new-account defaults,
the back-offs (a 403, a ban, a removal, a suspended account) and the slot
a playbook asks before a model turn."""

from __future__ import annotations

from .support import DAY, HOUR, ME, T0, Scratch, form, t1, t3
from .test_actions import Refusing, rows
from teeny_hands import hand, stimuli
from teeny_hands.reddit import actions, reads, rules


def backoff_stimuli() -> list[dict]:
    conn = stimuli.db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM stimuli WHERE surface = 'reddit' AND kind = 'backoff'")]
    finally:
        conn.close()


class AnchorTest(Scratch, Refusing):
    settings = {"REDDIT_MAX_POSTS_PER_DAY": "1"}

    def setUp(self):
        super().setUp()
        self.community()
        self.submitted("p1")

    def body(self, **more):
        return {"subreddit": "learnpython", "title": "a tiny cli that explains tracebacks", "body": "it reads the last frame.", **more}

    def test_a_post_without_an_anchor_is_refused_and_nothing_is_sent(self):
        for body in (self.body(), self.body(anchor=""), self.body(anchor="   "), self.body(anchor=7)):
            self.refused(actions.post, body, "anchor")
        self.assertEqual(self.rd.requests("POST", "/api/submit"), [])
        self.assertEqual(rows("reddit_posts"), [])

    def test_the_anchor_is_recorded_and_not_published(self):
        actions.post(self.body(anchor="receipt r-42"))
        self.assertEqual([r["anchor"] for r in rows("reddit_posts")], ["receipt r-42"])
        self.assertNotIn("receipt r-42", self.rd.requests("POST", "/api/submit")[0].body.decode())

    def test_dry_run_names_the_anchor_and_writes_nothing(self):
        self.configure(DRY_RUN="1")
        out = actions.post(self.body(anchor="PR #291"))
        self.assertEqual(out["would_post"]["anchor"], "PR #291")
        self.assertEqual(rows("reddit_posts"), [])


class AgeTest(Scratch, Refusing):
    def setUp(self):
        super().setUp()
        self.things(
            t3("fresh1", created_utc=T0 - 2 * HOUR),
            t3("stale1", created_utc=T0 - 4 * HOUR),
            t3("mine9", author=ME, created_utc=T0 - 30 * HOUR),
            t1("m1", "ana", "mine9"),
        )
        self.commented("c9")

    def test_an_unasked_comment_on_a_stale_post_is_refused(self):
        error = self.refused(actions.comment, {"post_id": "stale1", "body": "late to this"}, "too_old")
        self.assertEqual((error.detail["limit_hours"], error.detail["age_hours"]), (3, 4.0))
        actions.comment({"post_id": "fresh1", "body": "on time"})
        self.assertEqual(len(self.rd.requests("POST", "/api/comment")), 1)

    def test_answering_a_reply_to_teeny_has_no_age_limit(self):
        actions.comment({"post_id": "mine9", "parent_id": "m1", "body": "thanks ana"})

    def test_the_limit_is_a_setting_clamped_to_1_to_48_hours(self):
        self.assertEqual(rules.max_comment_age_hours(), 3)
        for raw, want in (("6", 6), ("0", 1), ("100", 48)):
            self.configure(REDDIT_MAX_COMMENT_AGE_HOURS=raw)
            self.assertEqual(rules.max_comment_age_hours(), want)
        self.configure(REDDIT_MAX_COMMENT_AGE_HOURS="6")
        actions.comment({"post_id": "stale1", "body": "fine now"})


class DefaultsTest(Scratch, Refusing):
    def test_a_fresh_install_comments_and_does_not_post(self):
        self.assertEqual((rules.max_posts_per_day(), rules.max_comments_per_day()), (0, 6))
        self.community()
        self.refused(actions.post, {"subreddit": "learnpython", "title": "t", "body": "b", "anchor": "PR #1"}, "daily_cap")
        self.assertEqual(self.rd.writes()[1:], [])

    def test_the_upper_clamps_stay(self):
        self.configure(REDDIT_MAX_POSTS_PER_DAY="50", REDDIT_MAX_COMMENTS_PER_DAY="500")
        self.assertEqual((rules.max_posts_per_day(), rules.max_comments_per_day()), (10, 100))


class BackoffTest(Scratch, Refusing):
    settings = {"REDDIT_MAX_POSTS_PER_DAY": "3"}

    def setUp(self):
        super().setUp()
        self.community()
        self.community("SideProject")
        self.things(t3("post1"), t3("post3"), t3("post2", subreddit="SideProject"))
        self.commented("c9")

    def post(self, sub="SideProject"):
        return {"subreddit": sub, "title": "a tiny cli", "body": "it reads the last frame.", "anchor": "PR #7"}

    def test_a_403_on_a_write_backs_off_that_subreddit(self):
        self.rd.route("POST", "/api/comment", {"message": "Forbidden"}, status=403,
                      when=lambda s: form(s).get("thing_id") == "t3_post1")
        self.refused(actions.comment, {"post_id": "post1", "body": "hello"}, "forbidden")
        self.at(T0 + HOUR)
        error = self.refused(actions.comment, {"post_id": "post3", "body": "hello"}, "backoff")
        self.assertEqual(error.detail["subreddit"], "learnpython")
        self.assertEqual(error.detail["wait_seconds"], int(7 * DAY - HOUR) + 1)
        self.assertIn("left", error.reason)
        self.assertEqual(len(self.rd.requests("POST", "/api/comment")), 1)
        # Another community is not touched.
        actions.comment({"post_id": "post2", "body": "hello"})
        self.assertEqual(len(backoff_stimuli()), 1)
        self.at(T0 + 7 * DAY + 1)
        self.things(t3("post3", created_utc=T0 + 7 * DAY - HOUR))
        actions.comment({"post_id": "post3", "body": "back"})

    def test_a_ban_seen_by_the_community_check_backs_off(self):
        self.community("SideProject", user_is_banned=True)
        self.refused(actions.post, self.post(), "banned")
        self.community("SideProject")
        self.refused(actions.post, self.post(), "backoff")
        self.refused(actions.comment, {"post_id": "post2", "body": "hi"}, "backoff")
        self.assertEqual(len(backoff_stimuli()), 1, "one stimulus per back-off, not per refused write")
        self.assertEqual(self.rd.requests("POST", "/api/submit"), [])

    def test_clear_backoff_lifts_a_subreddits(self):
        self.community("SideProject", user_is_banned=True)
        self.refused(actions.post, self.post(), "banned")
        self.community("SideProject")
        self.assertEqual(rules.clear_backoff("r/sideproject")["cleared"], 1)
        self.submitted("p5")
        actions.post(self.post())

    def test_a_suspended_account_stops_every_write_until_the_operator_clears_it(self):
        self.rd.route("GET", "/api/v1/me", {"name": ME, "is_suspended": True})
        reads.whoami()
        self.at(T0 + 30 * DAY)
        self.things(t3("post1", created_utc=T0 + 30 * DAY - HOUR))
        error = self.refused(actions.comment, {"post_id": "post1", "body": "hi"}, "backoff")
        self.assertNotIn("wait_seconds", error.detail)
        self.assertEqual(rules.clear_backoff("SideProject")["cleared"], 0)
        self.refused(actions.comment, {"post_id": "post1", "body": "hi"}, "backoff")
        self.assertEqual(rules.clear_backoff(None)["cleared"], 1)
        actions.comment({"post_id": "post1", "body": "hi"})
        self.assertEqual(len(backoff_stimuli()), 1)

    def test_a_403_on_the_account_stops_every_write(self):
        self.rd.route("GET", "/api/v1/me", {"message": "Forbidden"}, status=403)
        with self.assertRaises(Exception):
            reads.whoami()
        self.refused(actions.comment, {"post_id": "post2", "body": "hi"}, "backoff")
        self.assertEqual([b["scope"] for b in rules.backoffs()], [rules.ACCOUNT])

    def test_the_rules_verb_shows_the_backoffs_in_force(self):
        self.community("SideProject", user_is_banned=True)
        self.refused(actions.post, self.post(), "banned")
        got = rules.backoffs()
        self.assertEqual([(b["scope"], b["signal"]) for b in got], [("sideproject", "banned")])
        self.assertEqual(got[0]["until"], T0 + 7 * DAY)
        self.configure(REDDIT_BACKOFF_DAYS="2")
        self.assertEqual(rules.backoff_days(), 2)

    def test_dry_run_records_no_backoff(self):
        self.configure(DRY_RUN="1")
        self.community("SideProject", user_is_banned=True)
        self.refused(actions.post, self.post(), "banned")
        self.assertEqual((rules.backoffs(), backoff_stimuli()), ([], []))


class SlotTest(Scratch):
    def test_posts_are_shut_by_the_default_cap(self):
        got = rules.slot("post")
        self.assertEqual((got["open"], got["why"].split(":")[0]), (False, "daily_cap"))

    def test_open_when_nothing_stands_in_the_way(self):
        self.configure(REDDIT_MAX_POSTS_PER_DAY="1")
        self.assertEqual(rules.slot("post"), {"open": True})
        self.assertEqual(rules.slot("comment"), {"open": True})

    def test_a_gap_gives_its_wait_and_a_short_one_is_sat_out(self):
        rules.db().execute("INSERT INTO reddit_comments (comment_id, post_id, subreddit, at, status)"
                           " VALUES ('c1', 'p1', 'learnpython', ?, 'posted')", (T0 - 60,))
        got = rules.slot("comment")
        self.assertEqual((got["open"], got["wait_seconds"]), (False, 61))
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            self.at(T0 + seconds)

        self.assertEqual(rules.slot("comment", wait=90, sleep=sleep), {"open": True, "waited": 61})
        self.at(T0)
        self.assertFalse(rules.slot("comment", wait=30, sleep=sleep)["open"])
        self.assertEqual(slept, [61])

    def test_a_backoff_shuts_it(self):
        conn = rules.db()
        rules.start_backoff(conn, rules.ACCOUNT, "suspended", "the account is suspended")
        got = rules.slot("comment")
        self.assertEqual((got["open"], got["why"].split(":")[0]), (False, "backoff"))
        rules.clear_backoff(None)
        for sub in ("learnpython", "sideproject"):
            rules.start_backoff(conn, sub, "forbidden", "403")
        got = rules.slot("comment")
        self.assertEqual((got["open"], got["why"].split(":")[0]), (False, "backoff"))
        self.assertGreater(got["wait_seconds"], 6 * DAY)
        rules.clear_backoff("learnpython")
        self.assertTrue(rules.slot("comment")["open"])

    def test_an_empty_allowlist_is_shut(self):
        self.configure(REDDIT_SUBREDDITS="")
        self.assertEqual(rules.slot("comment")["why"].split(":")[0], "subreddit")

    def test_a_shut_slot_reads_the_same_under_hand_rules(self):
        self.assertIsInstance(rules.slot("post")["why"], str)
        with self.assertRaises(hand.HandError):
            rules.slot("vote")
