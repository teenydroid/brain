"""A Reddit draft of Teeny's own held only because the judge could not
answer is judged again once it answers and goes out through hands/reddit
(the guard again, every rule again: the anchor, the caps, the back-offs),
as a Moltbook draft does (guard/rescreen.py, RESENDERS)."""

from __future__ import annotations

from unittest import mock

from guard.support import FakeCore, error, verdict
from reddit.support import ME, MINUTE, T0, Scratch, form, t3
from teeny_hands import hand
from teeny_hands.guard import holds, judge, rescreen
from teeny_hands.reddit import actions, rules

DOWN = error("model error: local-openai returned: provider stream ended before a completion signal")
CLEAN = verdict("clean", "none", "a friendly technical note")
LATER = 3 * MINUTE


class RedditResendTest(Scratch):
    settings = {"SAFETY_JUDGE": "1", "REDDIT_MAX_POSTS_PER_DAY": "1"}

    def setUp(self):
        super().setUp()
        self.core = FakeCore(DOWN)
        self.addCleanup(self.core.close)
        (self.root / "state").mkdir(exist_ok=True)
        (self.root / "state" / "core.url").write_text(self.core.url + "\n")
        (self.root / "secrets" / "core.token").write_text("tok\n")
        backoff = mock.patch.object(judge, "RETRY_BACKOFFS", (0.0, 0.0, 0.0, 0.0))
        backoff.start()
        self.addCleanup(backoff.stop)
        self.community()

    def judge_says(self, *replies):
        self.core.replies = list(replies)
        self.core.calls.clear()

    def held(self, call, body) -> dict:
        self.judge_says(DOWN)
        with self.assertRaises(hand.Refused) as caught:
            call(body)
        self.assertEqual(caught.exception.reason, "held")
        return holds.get(caught.exception.detail["hold_id"])

    def ledger(self, sql):
        conn = rules.db()
        try:
            return [tuple(r) for r in conn.execute(sql)]
        finally:
            conn.close()

    def test_a_held_post_goes_out_with_its_anchor_and_tags(self):
        self.held(actions.post, {"subreddit": "learnpython", "title": "a cli that reads tracebacks",
                                 "body": "it starts at the last frame.", "anchor": "PR #291", "format": "show"})
        self.submitted("p9")
        self.judge_says(CLEAN, CLEAN)
        self.at(T0 + LATER)
        out = rescreen.run()
        self.assertEqual(out["sent"], 1, out)
        sent = form(self.rd.requests("POST", "/api/submit")[0])
        self.assertEqual((sent["sr"], sent["title"], sent["text"]),
                         ("learnpython", "a cli that reads tracebacks", "it starts at the last frame."))
        self.assertEqual(self.ledger("SELECT post_id, anchor, format FROM reddit_posts"), [("p9", "PR #291", "show")])

    def test_a_held_answer_goes_out_to_the_same_parent(self):
        from reddit.support import t1

        self.things(t3("mine1", author=ME), t1("m1", "ana", "mine1"))
        self.held(actions.comment, {"post_id": "mine1", "parent_id": "m1", "body": "glad it helped, ana"})
        self.commented("c9")
        self.judge_says(CLEAN)
        self.at(T0 + LATER)
        self.assertEqual(rescreen.run()["sent"], 1)
        self.assertEqual(form(self.rd.requests("POST", "/api/comment")[0])["thing_id"], "t1_m1")

    def test_a_held_post_is_dropped_when_teeny_posted_since(self):
        self.held(actions.post, {"subreddit": "learnpython", "title": "a cli", "body": "b.", "anchor": "PR #1"})
        conn = rules.db()
        conn.execute("INSERT INTO reddit_posts (post_id, at, subreddit, title, status) VALUES ('p2', ?, 'sideproject', 't', 'posted')",
                     (T0 + MINUTE,))
        conn.close()
        self.judge_says(CLEAN, CLEAN)
        self.at(T0 + LATER)
        out = rescreen.run()
        self.assertEqual(out["sent"], 0)
        self.assertEqual(self.rd.requests("POST", "/api/submit"), [])
