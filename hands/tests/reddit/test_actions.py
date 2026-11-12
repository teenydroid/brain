"""hands/reddit's acting verbs: the allowlist, the guard on every word, the
caps and gaps, the comment rules, the community checks, the crypto rule,
Reddit's own refusals, DRY_RUN and the ledger, against the fake Reddit."""

from __future__ import annotations

from .support import DAY, HOUR, ME, MINUTE, T0, TOKEN, Scratch, form, t1, t3
from teeny_hands import hand
from teeny_hands.reddit import actions, rules


def rows(table: str) -> list[dict]:
    conn = hand.open_db(rules.SCHEMA)
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


class Refusing:
    def refused(self, call, body, rule):
        with self.assertRaises(hand.Refused) as caught:
            call(body)
        self.assertEqual(caught.exception.detail.get("rule"), rule, caught.exception.reason)
        return caught.exception


class PostTest(Scratch, Refusing):
    def setUp(self):
        super().setUp()
        self.community()
        self.community("SideProject")

    def body(self, **more):
        return {"subreddit": "learnpython", "title": "a tiny cli that explains tracebacks", "body": "it reads the last frame first.", **more}

    def test_publishes_a_self_post_and_records_it(self):
        self.submitted("p1")
        out = actions.post(self.body())
        self.assertEqual((out["posted"], out["subreddit"]), ("p1", "learnpython"))
        sent = self.rd.requests("POST", "/api/submit")[0]
        self.assertEqual(sent.authorization, f"Bearer {TOKEN}")
        self.assertEqual(
            {k: form(sent)[k] for k in ("api_type", "sr", "kind", "title", "text")},
            {"api_type": "json", "sr": "learnpython", "kind": "self", "title": "a tiny cli that explains tracebacks",
             "text": "it reads the last frame first."},
        )
        self.assertIn(f"(by /u/{ME})", sent.headers["user-agent"])
        self.assertEqual([(r["post_id"], r["status"]) for r in rows("reddit_posts")], [("p1", "posted")])

    def test_only_subreddits_on_the_allowlist(self):
        self.refused(actions.post, self.body(subreddit="AskReddit"), "subreddit")
        self.configure(REDDIT_SUBREDDITS="")
        self.refused(actions.post, self.body(), "subreddit")
        self.assertEqual(self.rd.writes(), [])

    def test_the_allowlist_ignores_case_and_the_r_prefix(self):
        self.community("sideproject")
        self.submitted("p1")
        actions.post(self.body(subreddit="r/sideproject"))
        self.assertEqual(form(self.rd.requests("POST", "/api/submit")[0])["sr"], "sideproject")

    def test_dry_run_touches_nothing(self):
        self.configure(DRY_RUN="1")
        out = actions.post(self.body())
        self.assertEqual((out["dry_run"], out["would_post"]["kind"]), (True, "self"))
        self.assertEqual([s for s in self.rd.writes() if s.path != "/api/v1/access_token"], [])
        self.assertEqual(rows("reddit_posts"), [])

    def test_a_held_title_publishes_nothing(self):
        with self.assertRaises(hand.Refused) as caught:
            actions.post(self.body(title="this is fucking great"))
        self.assertEqual(caught.exception.reason, "held")
        self.assertEqual(self.rd.requests("POST", "/api/submit"), [])

    def test_a_link_post_must_be_on_the_link_allowlist(self):
        self.refused(actions.post, {"subreddit": "learnpython", "title": "look", "url": "https://evil.example/x"}, "link")
        self.submitted("p2")
        actions.post({"subreddit": "learnpython", "title": "my code", "url": "https://github.com/teenydroid/hello"})
        sent = form(self.rd.requests("POST", "/api/submit")[0])
        self.assertEqual((sent["kind"], sent["url"]), ("link", "https://github.com/teenydroid/hello"))

    def test_one_post_a_day_and_a_gap_between_posts(self):
        self.submitted("p1")
        actions.post(self.body())
        self.at(T0 + 5 * HOUR)
        error = self.refused(actions.post, self.body(subreddit="SideProject"), "rate_limit")
        self.assertGreater(error.detail["wait_seconds"], 0)
        self.at(T0 + 7 * HOUR)
        self.refused(actions.post, self.body(subreddit="SideProject"), "daily_cap")
        self.at(T0 + DAY + 1)
        actions.post(self.body(subreddit="SideProject"))
        self.assertEqual(len(self.rd.requests("POST", "/api/submit")), 2)

    def test_one_post_per_subreddit_a_week(self):
        self.configure(REDDIT_MAX_POSTS_PER_DAY="5")
        self.submitted("p1")
        actions.post(self.body())
        self.at(T0 + 3 * DAY)
        self.refused(actions.post, self.body(), "rate_limit")
        self.at(T0 + 7 * DAY + 1)
        actions.post(self.body())

    def test_crypto_only_where_the_operator_allows_it(self):
        self.refused(actions.post, self.body(body="my solana wallet is open"), "crypto")
        self.configure(REDDIT_CRYPTO_SUBREDDITS="learnpython")
        self.submitted("p1")
        actions.post(self.body(body="my solana wallet is open"))

    def test_the_communitys_own_state_is_read_first(self):
        self.community("SideProject", user_is_banned=True)
        self.refused(actions.post, self.body(subreddit="SideProject"), "banned")
        self.community("SideProject", over18=True)
        self.refused(actions.post, self.body(subreddit="SideProject"), "nsfw")
        self.community("SideProject", submission_type="link")
        self.refused(actions.post, self.body(subreddit="SideProject"), "submission_type")
        self.assertEqual(self.rd.requests("POST", "/api/submit"), [])

    def test_reddits_ratelimit_is_a_refusal_with_its_wait(self):
        self.submitted(errors=[["RATELIMIT", "you are doing that too much. try again in 9 minutes.", "ratelimit"]], ratelimit=540.2)
        error = self.refused(actions.post, self.body(), "rate_limit")
        self.assertEqual(error.detail["wait_seconds"], 541)
        self.assertEqual(rows("reddit_posts"), [])

    def test_the_wait_is_read_from_the_message_when_reddit_gives_no_number(self):
        self.submitted(errors=[["RATELIMIT", "Looks like you've been doing that a lot. Take a break for 4 minutes", "ratelimit"]])
        self.assertEqual(self.refused(actions.post, self.body(), "rate_limit").detail["wait_seconds"], 241)

    def test_any_other_reddit_error_names_its_code(self):
        self.submitted(errors=[["SUBMIT_VALIDATION_FLAIR_REQUIRED", "Your post must contain post flair.", "flair"]])
        error = self.refused(actions.post, self.body(), "reddit")
        self.assertEqual(error.detail["code"], "SUBMIT_VALIDATION_FLAIR_REQUIRED")

    def test_a_429_is_never_retried(self):
        self.rd.route("POST", "/api/submit", {"message": "Too Many Requests"}, status=429, headers={"X-Ratelimit-Reset": "30"})
        self.assertEqual(self.refused(actions.post, self.body(), "rate_limit").detail["wait_seconds"], 31)
        self.assertEqual(len(self.rd.requests("POST", "/api/submit")), 1)

    def test_no_secret_reaches_a_file(self):
        self.submitted("p1")
        actions.post(self.body())
        self.assertEqual(self.leaked(), [])


class CommentTest(Scratch, Refusing):
    def setUp(self):
        super().setUp()
        self.things(
            t3("post1", author="ana"),
            t3("mine1", author=ME),
            t3("locked1", locked=True),
            t3("away1", subreddit="AskReddit"),
            t1("c1", "ana", "post1"),
            t1("c2", ME, "post1", parent="t1_c1"),
            t1("c3", "ana", "post1", parent="t1_c2"),
            t1("c4", "bob", "post1", parent="t1_c1"),
            t1("m1", "ana", "mine1"),
        )
        self.commented("new9")

    def test_comments_on_a_post_and_records_it(self):
        out = actions.comment({"post_id": "t3_post1", "body": "try running it with -X dev first."})
        self.assertEqual((out["commented"], out["subreddit"]), ("new9", "learnpython"))
        sent = form(self.rd.requests("POST", "/api/comment")[0])
        self.assertEqual((sent["thing_id"], sent["text"]), ("t3_post1", "try running it with -X dev first."))
        self.assertEqual([(r["comment_id"], r["post_id"], r["parent_id"]) for r in rows("reddit_comments")], [("new9", "post1", None)])

    def test_never_twice_on_one_post(self):
        actions.comment({"post_id": "post1", "body": "first"})
        self.at(T0 + 10 * MINUTE)
        self.refused(actions.comment, {"post_id": "post1", "body": "second"}, "once_per_post")
        # Nor a reply to somebody else's comment once Teeny is in the thread.
        self.refused(actions.comment, {"post_id": "post1", "parent_id": "c4", "body": "and you"}, "once_per_post")

    def test_answers_a_reply_to_teeny(self):
        conn = rules.db()
        conn.execute("INSERT INTO reddit_comments (comment_id, post_id, parent_id, subreddit, at, status) VALUES ('c2', 'post1', 'c1', 'learnpython', ?, 'posted')", (T0 - HOUR,))
        out = actions.comment({"post_id": "post1", "parent_id": "t1_c3", "body": "glad it helped"})
        self.assertEqual(out["parent_id"], "c3")
        self.assertEqual(form(self.rd.requests("POST", "/api/comment")[0])["thing_id"], "t1_c3")
        self.at(T0 + 10 * MINUTE)
        self.refused(actions.comment, {"post_id": "post1", "parent_id": "c3", "body": "again"}, "once_per_parent")

    def test_a_reply_to_teeny_is_known_from_reddit_when_the_ledger_has_none(self):
        # c2 is Teeny's on Reddit but not in this ledger (another box wrote it).
        conn = rules.db()
        conn.execute("INSERT INTO reddit_comments (comment_id, post_id, parent_id, subreddit, at, status) VALUES ('old', 'post1', NULL, 'learnpython', ?, 'posted')", (T0 - 2 * HOUR,))
        actions.comment({"post_id": "post1", "parent_id": "c3", "body": "yes"})

    def test_never_its_own_comment(self):
        self.refused(actions.comment, {"post_id": "post1", "parent_id": "c2", "body": "me again"}, "own_comment")

    def test_on_its_own_post_it_answers_rather_than_adds(self):
        self.refused(actions.comment, {"post_id": "mine1", "body": "bump"}, "own_post")
        actions.comment({"post_id": "mine1", "parent_id": "m1", "body": "thanks ana"})

    def test_a_parent_must_be_on_the_post(self):
        self.refused(actions.comment, {"post_id": "mine1", "parent_id": "c1", "body": "x"}, "unknown_parent")
        self.refused(actions.comment, {"post_id": "post1", "parent_id": "zz9", "body": "x"}, "unknown_parent")

    def test_locked_and_foreign_threads_are_refused(self):
        self.refused(actions.comment, {"post_id": "locked1", "body": "x"}, "locked")
        self.refused(actions.comment, {"post_id": "away1", "body": "x"}, "subreddit")
        self.refused(actions.comment, {"post_id": "nope1", "body": "x"}, "unknown_post")
        self.assertEqual(self.rd.requests("POST", "/api/comment"), [])

    def test_a_gap_and_a_daily_cap(self):
        self.configure(REDDIT_MAX_COMMENTS_PER_DAY="2")
        self.things(t3("post2"), t3("post3"), t3("post4"))
        actions.comment({"post_id": "post1", "body": "one"})
        self.at(T0 + 60)
        self.assertGreater(self.refused(actions.comment, {"post_id": "post2", "body": "two"}, "rate_limit").detail["wait_seconds"], 0)
        self.at(T0 + 121)
        actions.comment({"post_id": "post2", "body": "two"})
        self.at(T0 + 10 * MINUTE)
        self.refused(actions.comment, {"post_id": "post3", "body": "three"}, "daily_cap")

    def test_a_comment_gap_is_never_under_thirty_seconds(self):
        self.configure(REDDIT_COMMENT_GAP_SECONDS="1")
        self.assertEqual(rules.comment_gap(), 30.0)

    def test_ids_of_the_wrong_kind_are_errors(self):
        with self.assertRaises(hand.HandError):
            actions.comment({"post_id": "t1_c1", "body": "x"})

    def test_dry_run_runs_the_rules_and_sends_nothing(self):
        self.configure(DRY_RUN="1")
        out = actions.comment({"post_id": "post1", "body": "would say"})
        self.assertEqual(out["would_comment"]["subreddit"], "learnpython")
        self.assertEqual(self.rd.requests("POST", "/api/comment"), [])
        self.assertEqual(rows("reddit_comments"), [])
        self.refused(actions.comment, {"post_id": "away1", "body": "x"}, "subreddit")


class SubscribeTest(Scratch, Refusing):
    def test_subscribes_once_on_the_allowlist(self):
        self.community()
        self.rd.route("POST", "/api/subscribe", {})
        self.assertEqual(actions.subscribe({"subreddit": "learnpython"}), {"subscribed": "learnpython"})
        self.assertEqual(form(self.rd.requests("POST", "/api/subscribe")[0])["sr_name"], "learnpython")
        self.refused(actions.subscribe, {"subreddit": "LearnPython"}, "duplicate")
        self.refused(actions.subscribe, {"subreddit": "AskReddit"}, "subreddit")
