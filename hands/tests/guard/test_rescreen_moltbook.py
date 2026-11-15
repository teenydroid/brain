"""A Moltbook draft of Teeny's own held only because the judge could not
answer is judged again once it answers, and goes out or stays held on its
merits (guard/rescreen.py, RESENDERS).

Before, every such draft expired unsent: between 2026-11-11 and 11-13 nine
Moltbook drafts (seven comments, two posts) waited 10 to 199 minutes and
were closed without anyone reading them. Now, within RESEND_SECS, the
re-screen asks the judge about each stored field; a fresh `clean` sends it
through hands/moltbook (the guard again, every posting rule again); a real
verdict keeps it held for that reason; and anything the hand refuses closes
it. Against a fake Moltbook and a fake core, on a pinned clock.
"""

from __future__ import annotations

import argparse
from unittest import mock

from guard.support import FakeCore, error, verdict
from moltbook.support import MINUTE, T0, Scratch
from teeny_hands import hand
from teeny_hands.guard import cli, holds, judge, rescreen
from teeny_hands.moltbook import actions, rules

DOWN = error("model error: local-openai returned: provider stream ended before a completion signal")
CLEAN = verdict("clean", "none", "a friendly technical note")
LATER = 3 * MINUTE  # past the two-minute grace


class ResendBase(Scratch):
    settings = {"SAFETY_JUDGE": "1"}

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

    def judge_says(self, *replies):
        self.core.replies = list(replies)
        self.core.calls.clear()

    def held(self, call, body) -> dict:
        """A draft sent while the judge was down, held through the real hand."""
        self.judge_says(DOWN)
        with self.assertRaises(hand.Refused) as caught:
            call(body)
        self.assertEqual((caught.exception.reason, caught.exception.detail.get("judge_failed")), ("held", True))
        return holds.get(caught.exception.detail["hold_id"])

    def run_at(self, offset: float) -> dict:
        self.at(T0 + offset)
        return rescreen.run()

    def posts(self) -> list[tuple]:
        conn = hand.open_db(rules.SCHEMA)
        return [tuple(r) for r in conn.execute("SELECT post_id, submolt, title, body, format FROM moltbook_posts")]


class PostResendTest(ResendBase):
    BODY = {"submolt": "memory", "title": "a cleared reminder proves the clearing", "body": "not the thing.",
            "format": "finding"}

    def test_a_post_held_by_an_outage_goes_out_once_the_judge_says_clean(self):
        hold = self.held(actions.post, self.BODY)
        self.assertEqual(self.mb.writes(), [])
        self.created("post", "p9")
        self.judge_says(CLEAN)
        out = self.run_at(LATER)
        self.assertEqual((out["checked"], out["sent"], out["expired"]), (1, 1, 0))
        sent = self.mb.requests("POST", "/posts")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].json(), {"submolt_name": "memory", "title": "a cleared reminder proves the clearing",
                                          "content": "not the thing.", "type": "text"})
        self.assertEqual(self.posts(), [("p9", "memory", "a cleared reminder proves the clearing", "not the thing.",
                                         "finding")])
        after = holds.get(hold["id"])
        self.assertEqual((after["status"], after["rescreen"]), ("cleared", "cleared"))
        self.assertIn("posted on Moltbook as p9", after["effect"])
        # Judged by the re-screen (title, body), and again by the hand's own guard.
        self.assertEqual(len(self.core.calls), 4)
        # Never twice.
        self.assertEqual(self.run_at(LATER + 5 * MINUTE)["checked"], 0)
        self.assertEqual(len(self.mb.requests("POST", "/posts")), 1)

    def test_a_link_post_goes_out_with_its_link(self):
        body = {"submolt": "builds", "title": "shipped: refusal-ledger", "url": "https://github.com/teenydroid/refusal-ledger"}
        self.held(actions.post, body)
        self.created("post", "p9")
        self.judge_says(CLEAN)
        self.assertEqual(self.run_at(LATER)["sent"], 1)
        self.assertEqual(self.mb.requests("POST", "/posts")[0].json(),
                         {"submolt_name": "builds", "title": "shipped: refusal-ledger", "type": "link",
                          "url": "https://github.com/teenydroid/refusal-ledger"})

    def test_a_rejudge_that_says_prohibited_keeps_it_held_for_that_reason(self):
        hold = self.held(actions.post, self.BODY)
        self.judge_says(verdict("prohibited", "violence", "a threat"))
        out = self.run_at(LATER)
        self.assertEqual((out["prohibited"], out["sent"]), (1, 0))
        after = holds.get(hold["id"])
        self.assertEqual((after["status"], after["category"], after["by"], after["reason"]),
                         ("pending", "violence", "judge", "a threat"))
        self.assertEqual(self.mb.writes(), [])
        # A verdict now: never asked about again.
        self.assertEqual(self.run_at(LATER + 5 * MINUTE)["checked"], 0)

    def test_a_judge_still_down_leaves_it_for_the_next_run(self):
        hold = self.held(actions.post, self.BODY)
        self.judge_says(DOWN)
        out = self.run_at(LATER)
        self.assertEqual((out["unreachable"], out["sent"]), (1, 0))
        self.assertEqual(holds.get(hold["id"])["status"], "pending")
        self.assertEqual(self.mb.writes(), [])
        self.created("post", "p9")
        self.judge_says(CLEAN)
        self.assertEqual(self.run_at(LATER + 5 * MINUTE)["sent"], 1)

    def test_a_draft_older_than_an_hour_expires_unsent(self):
        hold = self.held(actions.post, self.BODY)
        self.judge_says(CLEAN)
        out = self.run_at(rescreen.RESEND_SECS + MINUTE)
        self.assertEqual((out["expired"], out["sent"]), (1, 0))
        self.assertEqual(holds.get(hold["id"])["status"], "expired")
        self.assertEqual(self.core.calls, [])
        self.assertEqual(self.mb.writes(), [])

    def test_a_resend_the_posting_rules_refuse_closes_the_hold(self):
        hold = self.held(actions.post, self.BODY)
        # Teeny posted something else meanwhile: the hand's 30-minute gap refuses the re-send.
        self.created("post", "p8")
        self.judge_says(CLEAN)
        self.at(T0 + MINUTE)
        actions.post({"submolt": "general", "title": "another one", "body": "x"})
        out = self.run_at(LATER)
        self.assertEqual((out["refused"], out["sent"]), (1, 0))
        after = holds.get(hold["id"])
        self.assertEqual(after["status"], "expired")
        self.assertIn("refused the re-send", after["effect"])
        self.assertEqual(len(self.mb.requests("POST", "/posts")), 1)
        self.assertEqual(self.run_at(LATER + 5 * MINUTE)["checked"], 0)

    def test_a_post_is_not_sent_again_once_teeny_has_posted_since_the_hold(self):
        # Teeny posted something else after the hold, and the re-screen only
        # reaches the hold once the hand's 30-minute gap has passed (a backlog
        # of outage holds, ten a run): the stale draft must not go out as a
        # second post on top of the one Teeny wrote instead.
        hold = self.held(actions.post, self.BODY)
        self.created("post", "p8")
        self.judge_says(CLEAN)
        self.at(T0 + MINUTE)
        actions.post({"submolt": "general", "title": "another one", "body": "x"})
        out = self.run_at(MINUTE + rules.POST_GAP + MINUTE)
        self.assertEqual((out["refused"], out["sent"]), (1, 0))
        self.assertEqual(len(self.mb.requests("POST", "/posts")), 1)
        after = holds.get(hold["id"])
        self.assertEqual(after["status"], "expired")
        self.assertIn("posted on Moltbook since", after["effect"])

    def test_a_resend_the_guard_holds_again_on_a_judge_failure_is_not_tried_a_third_time(self):
        hold = self.held(actions.post, self.BODY)
        # Clean for the re-screen's two fields, then down for the hand's own guard.
        self.judge_says(CLEAN, CLEAN, DOWN)
        out = self.run_at(LATER)
        self.assertEqual(out["refused"], 1)
        self.assertEqual(self.mb.writes(), [])
        after = holds.get(hold["id"])
        self.assertEqual(after["status"], "expired")
        new_id = int(after["effect"].rsplit("hold ", 1)[1].rstrip(")"))
        self.assertEqual(holds.get(new_id)["status"], "expired")
        self.judge_says(CLEAN)
        self.assertEqual(self.run_at(LATER + 5 * MINUTE)["checked"], 0)

    def test_under_dry_run_nothing_is_sent_or_claimed(self):
        hold = self.held(actions.post, self.BODY)
        self.configure(DRY_RUN="1")
        self.judge_says(CLEAN)
        out = self.run_at(LATER)
        self.assertEqual(out["would_send"], 1)
        self.assertEqual((holds.get(hold["id"])["status"], holds.get(hold["id"])["rescreen"]), ("pending", "would_send"))
        self.assertEqual(self.mb.writes(), [])

    def test_a_hold_from_before_its_context_said_how_to_send_it_expires(self):
        # The nine held on the box: no `type` on a post, no `parent_id` on a comment.
        made = [holds.record(direction="outbound", kind="draft", context=context, category="unknown",
                             reason="the judge could not be reached: x", by="judge", text=text)
                for context, text in (
                    ({"surface": "moltbook", "post_kind": "post", "field": "body", "fields": ["body"]},
                     '{"body": "the mirrored failure is a clock"}'),
                    ({"surface": "moltbook", "post_kind": "comment", "post_id": "x1", "field": "body",
                      "fields": ["body"]}, '{"body": "honest answer: neither"}'),
                )]
        self.judge_says(CLEAN)
        out = self.run_at(LATER)
        self.assertEqual((out["expired"], out["sent"]), (2, 0))
        self.assertEqual([holds.get(h["id"])["status"] for h in made], ["expired", "expired"])
        self.assertEqual(self.core.calls, [])


class CommentResendTest(ResendBase):
    def test_a_reply_held_by_an_outage_goes_out_under_its_parent(self):
        self.a_post("x1", comments=[{"id": "c1", "author": {"name": "nyna"}, "content": "how?", "replies": []}])
        conn = hand.open_db(rules.SCHEMA)
        conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES ('x1', ?, 'general', 't',"
                     " 'posted')", (T0 - 3600,))
        conn.commit()
        hold = self.held(actions.comment, {"post_id": "x1", "parent_id": "c1", "body": "by reading the ledger"})
        self.assertIsNone(hold["context"].get("format"))
        self.assertEqual(hold["context"]["parent_id"], "c1")
        self.created("comment", "c2", path="/posts/x1/comments")
        self.judge_says(CLEAN)
        out = self.run_at(LATER)
        self.assertEqual(out["sent"], 1, out)
        self.assertEqual(self.mb.requests("POST", "/posts/x1/comments")[0].json(),
                         {"content": "by reading the ledger", "parent_id": "c1"})
        self.assertIn("commented on Moltbook as c2", holds.get(hold["id"])["effect"])

    def test_a_comment_the_tick_already_sent_again_is_not_sent_twice(self):
        self.a_post("x1")
        hold = self.held(actions.comment, {"post_id": "x1", "body": "nice"})
        self.assertIsNone(hold["context"]["parent_id"])
        self.created("comment", "c2", path="/posts/x1/comments")
        self.judge_says(CLEAN)
        # The next beat of bin/tick sent it again first (bin/lib/tick.py, RETRIED).
        actions.comment({"post_id": "x1", "body": "nice"})
        out = self.run_at(LATER)
        self.assertEqual((out["refused"], out["sent"]), (1, 0))
        self.assertEqual(len(self.mb.requests("POST", "/posts/x1/comments")), 1)
        effect = holds.get(hold["id"])["effect"]
        self.assertIn("refused the re-send: Teeny has already commented", effect)


class VerbTest(ResendBase):
    def test_hands_guard_rescreen_runs_one_pass(self):
        self.judge_says(CLEAN)
        out = cli.rescreen_run(argparse.Namespace(), None)
        self.assertEqual(out["rescreen"]["checked"], 0)

    def test_a_second_run_while_one_is_under_way_skips(self):
        with hand.lock("guard-rescreen"):
            out = rescreen.run()
        self.assertEqual(out["skipped"], "another re-screen is running")
