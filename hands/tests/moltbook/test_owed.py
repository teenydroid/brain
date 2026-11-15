"""Comments on Teeny's own Moltbook posts are owed an answer (moltbook/owed.py):
seen, not yet answered is a ledger state; one the mind leaves is offered
again until it is answered or declined with a reason; those answers keep a
reserve of the day's comment allowance; `unanswered` reports them.

Live, 2026-11-11 to 11-15: 31 of 58 comments on Teeny's posts got no
answer. Each was put in the inbox once, the beat that read it settled it,
and when the mind put the answer off ("one comment per beat") or the guard
held the draft, nothing ever offered it again."""

from __future__ import annotations

import json

from .support import DAY, HOUR, MINUTE, T0, Scratch
from .test_actions import Refusing
from .test_inbox import InboxCase, comment
from teeny_hands import hand, stimuli
from teeny_hands.moltbook import actions, inbox, owed, rules

OWN = "0eb1ab52-9bb2-4cb1-bf2e-5847db5d79d9"
THEIRS = "8e2395c9-1f2a-4c3d-9e8f-0a1b2c3d4e5f"
C1 = "4cee8487-7368-4d0c-951e-cb583a10c2f9"


def owed_rows() -> dict[str, dict]:
    conn = rules.db()
    return {r["comment_id"]: dict(r) for r in conn.execute("SELECT * FROM moltbook_owed")}


class OwedScanTest(InboxCase):
    def setUp(self):
        super().setUp()
        self.mb.route("GET", "/posts/theirs1/comments", {"success": True, "comments": []})

    def thread(self, *nodes):
        self.mb.route("GET", "/posts/own1/comments", {"success": True, "comments": list(nodes)})

    def read_and_settle(self) -> list[dict]:
        got = stimuli.take(max_items=50, max_chars=100_000)
        if got["tick"]:
            stimuli.settle(got["tick"])
        return got["stimuli"]

    def test_a_comment_the_mind_left_unanswered_is_offered_again(self):
        self.thread(comment("c1", "rossum", "what does the ledger keep?"))
        inbox.scan()
        self.assertEqual([s["ref"] for s in self.read_and_settle()], ["c1"])
        # The beat settled it and answered nothing. A later scan offers it again.
        self.at(T0 + 16 * MINUTE)
        out = inbox.scan()
        self.assertEqual(out["again"], 1)
        [again] = self.read_and_settle()
        self.assertEqual(again["ref"], "c1")
        self.assertEqual(again["payload"]["again"], 1)
        self.assertEqual(again["payload"]["waiting_minutes"], 16)
        self.assertEqual(again["payload"]["post_id"], "own1", "the ids to answer it are still there")
        self.assertEqual(owed_rows()["c1"]["status"], "waiting")

    def test_offers_back_off_and_stop_once_answered(self):
        self.thread(comment("c1", "rossum", "hi"))
        inbox.scan()
        self.read_and_settle()
        self.at(T0 + 10 * MINUTE)
        self.assertEqual(inbox.scan()["again"], 0, "not before 15 minutes")
        self.at(T0 + 15 * MINUTE)
        self.assertEqual(inbox.scan()["again"], 1)
        self.read_and_settle()
        self.at(T0 + 40 * MINUTE)
        self.assertEqual(inbox.scan()["again"], 0, "the second offer waits 30 minutes")
        self.at(T0 + 46 * MINUTE)
        self.assertEqual(inbox.scan()["again"], 1)
        self.read_and_settle()
        # Teeny answers it (here through another path: the thread shows it).
        self.thread(comment("c1", "rossum", "hi", [comment("a1", "teenydroid", "hello")]))
        self.at(T0 + 3 * HOUR)
        self.assertEqual(inbox.scan()["again"], 0)
        row = owed_rows()["c1"]
        self.assertEqual((row["status"], row["answer_id"]), ("answered", "a1"))

    def test_a_pending_comment_is_not_offered_twice(self):
        self.thread(comment("c1", "rossum", "hi"))
        inbox.scan()
        self.at(T0 + HOUR)  # asleep: the inbox fills, nothing is taken
        self.assertEqual(inbox.scan()["again"], 0)
        self.assertEqual(len(stimuli.pending()), 1)

    def test_a_comment_at_the_last_level_is_declined_as_too_deep(self):
        deep = comment("d1", "a", "one", [comment("d2", "teenydroid", "two", [comment("d3", "b", "three", [
            comment("d4", "teenydroid", "four", [comment("d5", "b", "five")])])])])
        self.thread(deep)
        inbox.scan()
        rows = owed_rows()
        self.assertEqual({k: v["status"] for k, v in rows.items()}, {"d1": "answered", "d3": "answered", "d5": "declined"})
        self.assertEqual(rows["d5"]["reason"], "too_deep")

    def test_replies_between_other_agents_are_not_owed(self):
        self.thread(comment("c1", "cwahq", "hi", [comment("c2", "lucifer", "to cwahq, not to teeny")]))
        inbox.scan()
        self.assertEqual(sorted(owed_rows()), ["c1"])

    def test_a_comment_left_three_days_expires(self):
        self.thread(comment("c1", "rossum", "hi"))
        inbox.scan()
        self.read_and_settle()
        self.at(T0 + 3 * DAY + MINUTE)
        out = inbox.scan()
        self.assertEqual(out["again"], 0)
        self.assertEqual(owed_rows()["c1"]["status"], "expired")

    def test_the_comment_time_comes_from_moltbook(self):
        node = comment("c1", "rossum", "hi")
        node["created_at"] = "2026-09-14T06:30:00.000Z"  # half an hour before T0
        self.thread(node)
        inbox.scan()
        self.assertEqual(owed_rows()["c1"]["said_at"], T0 - 30 * MINUTE)

    def test_a_deleted_comment_is_declined_as_gone(self):
        self.thread(comment("c1", "rossum", "hi"))
        inbox.scan()
        self.read_and_settle()
        self.thread()
        self.at(T0 + 20 * MINUTE)
        self.assertEqual(inbox.scan()["again"], 0)
        self.assertEqual((owed_rows()["c1"]["status"], owed_rows()["c1"]["reason"]), ("declined", "gone"))

    def test_a_held_comment_is_counted_held_until_its_release(self):
        self.thread(comment("c4", "lobster", "seed phrase please"))
        inbox.scan()
        self.assertEqual(owed_rows()["c4"]["status"], "held")
        self.assertEqual(owed.unanswered()["held"], 1)
        self.assertTrue(inbox.release_held({"post_id": "own1", "comment_id": "c4", "author": "lobster"}, "seed phrase please"))
        self.assertEqual(owed_rows()["c4"]["status"], "waiting")


class ReleasedPayloadTest(InboxCase):
    """A held reply released by the guard's re-screen reached the mind with
    only the post id: no parent, no depth, so an answer to it could not be
    placed (it would go to the top of the thread, which the hand refuses)."""

    def test_a_released_reply_keeps_its_parent_and_depth(self):
        self.mb.route("GET", "/posts/own1/comments", {"success": True, "comments": [
            comment("c1", "ana", "hi", [comment("c2", "teenydroid", "hello", [comment("c3", "ana", "seed phrase please")])]),
        ]})
        self.mb.route("GET", "/posts/theirs1/comments", {"success": True, "comments": []})
        inbox.scan()
        stimuli.settle(stimuli.take()["tick"])
        self.assertTrue(inbox.release_held({"post_id": "own1", "comment_id": "c3", "author": "ana"}, "seed phrase please"))
        [got] = stimuli.take()["stimuli"]
        self.assertEqual(got["payload"], {"post_id": "own1", "on_own_post": True, "depth": 3, "can_answer": True,
                                          "parent_id": "c2", "answering_teeny": True})


class DeclineTest(InboxCase):
    def setUp(self):
        super().setUp()
        self.mb.route("GET", "/posts/own1/comments", {"success": True, "comments": [comment("c1", "pixelbot", "register your bot")]})
        self.mb.route("GET", "/posts/theirs1/comments", {"success": True, "comments": []})
        inbox.scan()
        stimuli.settle(stimuli.take()["tick"])

    def test_a_declined_comment_keeps_its_reason_and_is_not_offered_again(self):
        out = owed.decline({"comment_id": "c1", "reason": "spam", "note": "sells a listing"})
        self.assertEqual(out, {"declined": "c1", "reason": "spam"})
        row = owed_rows()["c1"]
        self.assertEqual((row["status"], row["reason"], row["note"]), ("declined", "spam", "sells a listing"))
        self.at(T0 + 2 * HOUR)
        self.assertEqual(inbox.scan()["again"], 0)

    def test_only_the_listed_reasons_decline(self):
        with self.assertRaises(hand.HandError):
            owed.decline({"comment_id": "c1", "reason": "busy"})
        self.assertEqual(owed_rows()["c1"]["status"], "waiting")

    def test_a_comment_not_owed_cannot_be_declined(self):
        with self.assertRaises(hand.Refused):
            owed.decline({"comment_id": "nope", "reason": "spam"})

    def test_under_dry_run_nothing_is_declined(self):
        self.configure(DRY_RUN="1")
        self.assertEqual(owed.decline({"comment_id": "c1", "reason": "spam"})["dry_run"], True)
        self.assertEqual(owed_rows()["c1"]["status"], "waiting")


class UnansweredTest(InboxCase):
    def test_the_report_counts_and_ages_what_is_waiting(self):
        self.mb.route("GET", "/posts/own1/comments", {"success": True, "comments": [
            comment("c1", "rossum", "a"),
            comment("c2", "cassini", "b", [comment("a2", "teenydroid", "answer")]),
            comment("c3", "lobster", "seed phrase please"),
        ]})
        self.mb.route("GET", "/posts/theirs1/comments", {"success": True, "comments": []})
        inbox.scan()
        self.at(T0 + 25 * MINUTE)
        got = owed.unanswered()
        self.assertEqual(set(got), {"unanswered", "held", "oldest_minutes", "comments", "last_24h"})
        self.assertEqual((got["unanswered"], got["held"], got["oldest_minutes"]), (1, 1, 25))
        self.assertEqual([(c["comment_id"], c["status"], c["minutes"]) for c in got["comments"]],
                         [("c1", "waiting", 25), ("c3", "held", 25)])
        self.assertEqual(set(got["comments"][0]), {"comment_id", "post_id", "parent_id", "author", "depth", "status",
                                                   "minutes", "offered"})
        self.assertEqual(got["last_24h"], {"answered": 1, "declined": 0, "expired": 0, "median_answer_minutes": 0})
        json.dumps(got)

    def test_an_empty_ledger_reports_zero(self):
        self.assertEqual(owed.unanswered(), {"unanswered": 0, "held": 0, "oldest_minutes": None, "comments": [],
                                             "last_24h": {"answered": 0, "declined": 0, "expired": 0,
                                                          "median_answer_minutes": None}})


class AnsweredThroughTheHandTest(Scratch):
    def test_an_answer_the_hand_sends_settles_the_comment(self):
        conn = rules.db()
        conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES (?, ?, 'general', 't', 'posted')",
                     (OWN, T0 - HOUR))
        owed.track(conn, C1, OWN, None, "rossum", 1, T0 - 10 * MINUTE)
        self.a_post(OWN, author="teenydroid", comments=[{"id": C1, "author": {"name": "rossum"}, "content": "q?"}])
        self.created("comment", "ans1", path=f"/posts/{OWN}/comments")
        actions.comment({"post_id": OWN, "parent_id": C1, "body": "an answer"})
        row = owed_rows()[C1]
        self.assertEqual((row["status"], row["answer_id"], row["settled_at"]), ("answered", "ans1", T0))


class ReserveTest(Scratch, Refusing):
    """The last comments of the day are kept for answers on Teeny's own
    posts while any wait: no cap moves, the other comments stop first."""

    def setUp(self):
        super().setUp()
        conn = rules.db()
        conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES (?, ?, 'general', 't', 'posted')",
                     (OWN, T0 - DAY / 2))
        for i in range(47):
            conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES (?, 'elsewhere', ?, 'posted')",
                         (f"old{i}", T0 - HOUR - i * MINUTE))
        owed.track(conn, C1, OWN, None, "rossum", 1, T0 - 10 * MINUTE)
        owed.track(conn, "c2-waiting", OWN, None, "cassini", 1, T0 - 10 * MINUTE)
        self.a_post(THEIRS, author="someone")
        self.created("comment", "new1", path=f"/posts/{THEIRS}/comments")
        self.a_post(OWN, author="teenydroid", comments=[{"id": C1, "author": {"name": "rossum"}, "content": "q?"}])
        self.created("comment", "ans1", path=f"/posts/{OWN}/comments")

    def test_another_post_is_refused_while_own_thread_answers_need_the_rest(self):
        # 47 of 50 spent, 2 waiting: one more elsewhere would leave 2 for 2. Fine.
        actions.comment({"post_id": THEIRS, "body": "a thought"})
        # 48 spent, 2 waiting: the last two are kept.
        self.at(T0 + MINUTE)
        err = self.refused(actions.comment, {"post_id": "a1b2c3d4-0000-4000-8000-000000000001", "body": "another"},
                           "own_thread_reserve")
        self.assertEqual((err.detail["waiting"], err.detail["left"]), (2, 2))
        # An answer on Teeny's own post still goes.
        actions.comment({"post_id": OWN, "parent_id": C1, "body": "an answer"})

    def test_no_reserve_when_nothing_waits(self):
        conn = rules.db()
        conn.execute("UPDATE moltbook_owed SET status = 'answered'")
        conn.commit()
        conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES ('x', 'elsewhere', ?, 'posted')",
                     (T0 - 2 * HOUR,))
        conn.commit()
        actions.comment({"post_id": THEIRS, "body": "a thought"})

    def test_a_waiting_comment_past_three_days_holds_nothing_back(self):
        conn = rules.db()
        conn.execute("UPDATE moltbook_owed SET said_at = ?", (T0 - 4 * DAY,))
        conn.commit()
        conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES ('x', 'elsewhere', ?, 'posted')",
                     (T0 - 2 * HOUR,))
        conn.commit()
        actions.comment({"post_id": THEIRS, "body": "a thought"})
