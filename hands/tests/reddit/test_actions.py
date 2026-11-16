"""hands/reddit's writes: each verb queues the right desk task under
DRY_RUN=0 and nothing under DRY_RUN; the caps, the gap, the ramp, the
allowlist, the rules read first, the guard, and the vote rules."""

from __future__ import annotations

from teeny_hands import hand
from teeny_hands.reddit import actions, rules

from .support import COMMENT, DAY, ELSEWHERE, HOUR, RULES, THREAD, Scratch


class VerbTest(Scratch):
    def setUp(self) -> None:
        super().setUp()
        self.ready()

    def test_comment_queues_a_reddit_comment_task_with_the_exact_words(self):
        out = actions.comment({"thread": THREAD, "body": "Back off exponentially and cap the retries at three."})
        [task] = self.tasks()
        self.assertEqual((out["queued"], out["task_id"], out["kind"]), ("comment", task["id"], "reddit_comment"))
        self.assertEqual((task["kind"], task["source"], task["status"]), ("reddit_comment", "reddit", "pending"))
        self.assertEqual(task["start_url"], "https://old.reddit.com/r/AI_Agents/comments/abc123/")
        self.assertEqual(task["text"], "Back off exponentially and cap the retries at three.")
        self.assertIn("u/teenydroid", task["goal"])
        self.assertIn("No self-promotion", task["goal"])  # the subreddit's rules ride along
        [row] = self.rows()
        self.assertEqual((row["verb"], row["target"], row["subreddit"], row["status"], row["task_id"]),
                         ("comment", "t3_abc123", "AI_Agents", "queued", task["id"]))

    def test_reply_queues_on_the_comment(self):
        out = actions.reply({"comment": COMMENT, "body": "Thanks, that helped."})
        [task] = self.tasks()
        self.assertEqual((out["kind"], task["start_url"]),
                         ("reddit_reply", "https://old.reddit.com/r/AI_Agents/comments/abc123/_/def456/"))
        self.assertEqual(self.rows()[0]["target"], "t1_def456")

    def test_post_queues_a_submit_task_with_the_title_in_the_goal(self):
        out = actions.post({"subreddit": "r/ai_agents", "title": "what my retries taught me", "body": "Three things."})
        [task] = self.tasks()
        self.assertEqual((out["kind"], task["start_url"]), ("reddit_post", "https://old.reddit.com/r/AI_Agents/submit"))
        self.assertIn('"what my retries taught me"', task["goal"])
        self.assertEqual(task["text"], "Three things.")

    def test_upvote_batches_targets_into_one_vote_task(self):
        targets = [self.seen(THREAD), self.seen(COMMENT)]
        out = actions.upvote({"targets": [THREAD, COMMENT, THREAD]})
        [task] = self.tasks()
        self.assertEqual((out["kind"], task["kind"], out["targets"]), ("reddit_vote", "reddit_vote", targets))
        self.assertIn("abc123/_/def456", task["goal"])
        self.assertEqual([(r["verb"], r["task_id"]) for r in self.rows()], [("upvote", task["id"])] * 2)

    def test_subscribe(self):
        out = actions.subscribe({"subreddit": "LocalLLaMA"})
        [task] = self.tasks()
        self.assertEqual((out["kind"], task["start_url"]), ("reddit_subscribe", "https://old.reddit.com/r/LocalLLaMA/"))
        with self.assertRaises(hand.Refused) as caught:
            actions.subscribe({"subreddit": "localllama"})
        self.assertEqual(caught.exception.detail["rule"], "duplicate")

    def test_dry_run_queues_nothing_and_writes_no_row(self):
        self.seen(THREAD)
        self.configure(DRY_RUN=None)  # unset is on
        for verb, body in ((actions.comment, {"thread": THREAD, "body": "hi there"}),
                           (actions.reply, {"comment": COMMENT, "body": "hi there"}),
                           (actions.post, {"subreddit": "AI_Agents", "title": "a title", "body": "words"}),
                           (actions.upvote, {"targets": [THREAD]}),
                           (actions.subscribe, {"subreddit": "AI_Agents"})):
            with self.subTest(verb=verb.__name__):
                out = verb(body)
                self.assertTrue(out["dry_run"])
                self.assertTrue(out["would_queue"]["kind"].startswith("reddit_"))
        self.assertEqual((self.tasks(), self.rows()), ([], []))

    def test_bad_bodies(self):
        for verb, body in ((actions.comment, {"thread": COMMENT, "body": "x"}),
                           (actions.comment, {"thread": "https://example.com/r/x/comments/1/", "body": "x"}),
                           (actions.comment, {"thread": THREAD}),
                           (actions.reply, {"comment": THREAD, "body": "x"}),
                           (actions.post, {"subreddit": "AI_Agents", "title": "t"}),
                           (actions.post, {"subreddit": "not a sub", "title": "t", "body": "b"}),
                           (actions.upvote, {"targets": []}),
                           (actions.upvote, {"targets": [THREAD] * 6}),
                           (actions.comment, {"thread": THREAD, "body": "x", "extra": 1}),
                           (actions.comment, None)):
            with self.subTest(body=body), self.assertRaises(hand.HandError):
                verb(body)
        self.assertEqual(self.tasks(), [])


class RulesTest(Scratch):
    def test_nothing_is_written_before_an_account_exists(self):
        self.rules_read()
        with self.assertRaises(hand.Refused) as caught:
            actions.comment({"thread": THREAD, "body": "hello"})
        self.assertEqual(caught.exception.detail["rule"], "no_account")

    def test_only_allowlisted_subreddits_and_only_after_their_rules_were_read(self):
        self.account()
        with self.assertRaises(hand.Refused) as caught:
            actions.comment({"thread": ELSEWHERE, "body": "nice cat"})
        self.assertEqual(caught.exception.detail["rule"], "subreddit")
        with self.assertRaises(hand.Refused) as caught:
            actions.comment({"thread": THREAD, "body": "hello"})
        self.assertEqual(caught.exception.detail["rule"], "no_rules")
        self.rules_read()
        self.assertEqual(actions.comment({"thread": THREAD, "body": "hello"})["kind"], "reddit_comment")
        self.assertEqual(self.tasks()[0]["goal"].count(RULES[:20]), 1)

    def test_comments_and_replies_share_the_daily_cap_and_pending_tasks_count(self):
        self.ready()
        self.configure(REDDIT_COMMENTS_PER_DAY="2")
        actions.comment({"thread": THREAD, "body": "one"})
        self.at(rules.HOUR * 0 + 1_789_369_200.0 + 600)
        actions.reply({"comment": COMMENT, "body": "two"})
        with self.assertRaises(hand.Refused) as caught:
            actions.comment({"thread": "https://old.reddit.com/r/AI_Agents/comments/x1/", "body": "three"})
        self.assertEqual((caught.exception.detail["rule"], caught.exception.detail["cap"]), ("daily_cap", 2))
        self.assertEqual(caught.exception.detail["wait_seconds"], int(DAY - 600) + 1)
        self.assertEqual([t["status"] for t in self.tasks()], ["pending", "pending"])

    def test_the_cap_is_clamped_to_its_ceiling(self):
        self.configure(REDDIT_COMMENTS_PER_DAY="500", REDDIT_VOTES_PER_DAY="x")
        self.assertEqual((rules.cap("comment"), rules.cap("upvote")), (20, 15))

    def test_a_failed_task_frees_its_place(self):
        self.ready()
        self.configure(REDDIT_COMMENTS_PER_DAY="1")
        first = actions.comment({"thread": THREAD, "body": "one"})
        with self.assertRaises(hand.Refused):
            actions.reply({"comment": COMMENT, "body": "two"})
        self.finish(first["task_id"], "failed", "the thread was locked")
        self.assertEqual(actions.reply({"comment": COMMENT, "body": "two"})["kind"], "reddit_reply")

    def test_a_task_the_desk_handed_to_a_person_frees_its_place_without_a_settle(self):
        from teeny_hands.tasks import queue

        self.ready()
        self.configure(REDDIT_COMMENTS_PER_DAY="1")
        first = actions.comment({"thread": THREAD, "body": "one"})
        conn = queue.db()
        conn.execute("UPDATE browser_tasks SET status = 'needs_human' WHERE id = ?", (first["task_id"],))
        conn.close()
        actions.reply({"comment": COMMENT, "body": "two"})
        self.assertEqual([r["status"] for r in self.rows()], ["failed", "queued"])

    def test_one_comment_per_thread_and_one_reply_per_comment(self):
        self.ready()
        actions.comment({"thread": THREAD, "body": "one"})
        with self.assertRaises(hand.Refused) as caught:
            actions.comment({"thread": THREAD.replace("www.", "old."), "body": "again"})
        self.assertEqual(caught.exception.detail["rule"], "duplicate")

    def test_the_new_account_ramp_refuses_posts_but_not_comments(self):
        self.account(days_ago=2, karma_comment=3)
        self.rules_read()
        with self.assertRaises(hand.Refused) as caught:
            actions.post({"subreddit": "AI_Agents", "title": "hello", "body": "first post"})
        self.assertEqual(caught.exception.detail["rule"], "ramp")
        self.assertAlmostEqual(caught.exception.detail["wait_seconds"], 5 * DAY, delta=60)
        actions.comment({"thread": THREAD, "body": "a helpful comment"})

    def test_an_old_account_without_karma_still_waits(self):
        self.account(days_ago=30, karma_comment=10)
        self.rules_read()
        with self.assertRaises(hand.Refused) as caught:
            actions.post({"subreddit": "AI_Agents", "title": "hello", "body": "first post"})
        self.assertEqual(caught.exception.detail["rule"], "ramp")
        self.assertNotIn("wait_seconds", caught.exception.detail)

    def test_six_hours_between_posts(self):
        self.ready()
        self.configure(REDDIT_POSTS_PER_DAY="3")
        actions.post({"subreddit": "AI_Agents", "title": "one", "body": "first"})
        self.at(1_789_369_200.0 + 2 * HOUR)
        with self.assertRaises(hand.Refused) as caught:
            actions.post({"subreddit": "AI_Agents", "title": "two", "body": "second"})
        self.assertEqual((caught.exception.detail["rule"], caught.exception.detail["wait_seconds"]),
                         ("rate_limit", int(4 * HOUR) + 1))
        self.at(1_789_369_200.0 + 6 * HOUR + 1)
        actions.post({"subreddit": "AI_Agents", "title": "two", "body": "second"})

    def test_crypto_is_never_written(self):
        self.ready()
        with self.assertRaises(hand.Refused) as caught:
            actions.comment({"thread": THREAD, "body": "buy $SOL now, airdrop soon"})
        self.assertEqual(caught.exception.detail["rule"], "crypto")

    def test_a_guard_hold_refuses_as_held(self):
        from teeny_hands.guard.gate import FieldsVerdict

        self.ready()
        real = actions.check_fields
        actions.check_fields = lambda fields, **kw: FieldsVerdict("hold", {}, "no", "abuse", 7, "body")
        self.addCleanup(setattr, actions, "check_fields", real)
        with self.assertRaises(hand.Refused) as caught:
            actions.comment({"thread": THREAD, "body": "something the guard holds"})
        self.assertEqual((caught.exception.reason, caught.exception.detail["hold_id"]), ("held", 7))
        self.assertEqual((self.tasks(), self.rows()), ([], []))

    def test_the_guard_sees_the_draft_with_where_it_was_going(self):
        self.ready()
        seen = {}
        real = actions.check_fields

        def spy(fields, **kw):
            seen.update(fields=fields, **kw)
            return real(fields, **kw)

        actions.check_fields = spy
        self.addCleanup(setattr, actions, "check_fields", real)
        actions.reply({"comment": COMMENT, "body": "a reply"})
        self.assertEqual((seen["surface"], seen["kind"], seen["fields"]), ("reddit", "reply", {"body": "a reply"}))
        self.assertEqual(seen["context"]["comment"], "https://old.reddit.com/r/AI_Agents/comments/abc123/_/def456/")

    def test_an_over_long_comment_is_refused_for_a_rewrite(self):
        self.ready()
        with self.assertRaises(hand.Refused) as caught:
            actions.comment({"thread": THREAD, "body": "a" * 10001})
        self.assertTrue(caught.exception.detail["rewrite"])


class VoteTest(Scratch):
    def setUp(self) -> None:
        super().setUp()
        self.account()

    def test_never_on_something_no_browse_brought_back(self):
        with self.assertRaises(hand.Refused) as caught:
            actions.upvote({"targets": [THREAD]})
        self.assertEqual(caught.exception.detail["rule"], "unseen")

    def test_never_on_teenys_own_content(self):
        self.seen(THREAD, author="u/TeenyDroid")
        with self.assertRaises(hand.Refused) as caught:
            actions.upvote({"permalink": THREAD})
        self.assertEqual(caught.exception.detail["rule"], "own")
        # A comment Teeny wrote, as its settled row names it.
        self.rules_read()
        out = actions.reply({"comment": "https://old.reddit.com/r/AI_Agents/comments/abc123/_/zzz000/", "body": "hi"})
        self.finish(out["task_id"], reddit={"permalink": COMMENT})
        self.seen(COMMENT, author="someone_else")
        with self.assertRaises(hand.Refused) as caught:
            actions.upvote({"targets": [COMMENT]})
        self.assertEqual(caught.exception.detail["rule"], "own")

    def test_never_twice(self):
        self.seen(THREAD)
        actions.upvote({"targets": [THREAD]})
        with self.assertRaises(hand.Refused) as caught:
            actions.upvote({"targets": [THREAD]})
        self.assertEqual(caught.exception.detail["rule"], "duplicate")

    def test_each_target_counts_against_the_vote_cap(self):
        self.configure(REDDIT_VOTES_PER_DAY="2")
        links = [f"https://old.reddit.com/r/AI_Agents/comments/t{i}/" for i in range(3)]
        for link in links:
            self.seen(link)
        with self.assertRaises(hand.Refused) as caught:
            actions.upvote({"targets": links})
        self.assertEqual(caught.exception.detail["rule"], "daily_cap")
        actions.upvote({"targets": links[:2]})
        self.assertEqual(len(self.rows()), 2)
