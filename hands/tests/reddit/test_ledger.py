"""hands/reddit's ledger when the desk settles a task: the permalink of what
Teeny wrote, failures freeing the caps, and a settle that fails never
breaking the desk."""

from __future__ import annotations

from teeny_hands.reddit import actions, ledger
from teeny_hands.tasks import queue

from .support import COMMENT, THREAD, Scratch


class SettleTest(Scratch):
    def setUp(self) -> None:
        super().setUp()
        self.ready()

    def test_an_ok_write_records_its_permalink_as_teenys_own(self):
        out = actions.comment({"thread": THREAD, "body": "a comment"})
        settled = self.finish(out["task_id"], reddit={"permalink": COMMENT})
        self.assertEqual(settled["reddit"], {"rows": 1, "status": "done", "seen": 0,
                                             "permalink": "https://old.reddit.com/r/AI_Agents/comments/abc123/_/def456/"})
        [row] = self.rows()
        self.assertEqual((row["status"], row["own"]), ("done", "t1_def456"))
        self.assertIsNotNone(row["settled_at"])

    def test_a_bad_permalink_is_left_out_not_raised(self):
        out = actions.comment({"thread": THREAD, "body": "a comment"})
        settled = self.finish(out["task_id"], reddit={"permalink": "https://example.com/x"})
        self.assertEqual(settled["reddit"]["permalink_refused"], "https://example.com/x")
        self.assertEqual(self.rows()[0]["status"], "done")

    def test_a_failed_task_marks_its_rows_failed(self):
        self.seen(THREAD)
        self.seen(COMMENT)
        out = actions.upvote({"targets": [THREAD, COMMENT]})
        self.finish(out["task_id"], "needs_human", "reddit said: you are doing that too much")
        self.assertEqual([r["status"] for r in self.rows()], ["failed", "failed"])
        # A failed vote may be tried again.
        actions.upvote({"targets": [THREAD]})

    def test_a_report_object_only_goes_with_a_reddit_task(self):
        from teeny_hands import hand

        tid = queue.add({"goal": "g", "start_url": "https://github.com/x", "done_when": "d"}, source="operator")["task_id"]
        queue.next_task()
        with self.assertRaises(hand.HandError):
            queue.done({"task_id": tid, "outcome": "ok", "notes": "n", "reddit": {"permalink": COMMENT}})

    def test_a_settle_that_fails_never_breaks_the_desk(self):
        out = actions.comment({"thread": THREAD, "body": "a comment"})

        def broken(*_a, **_k):
            raise RuntimeError("the ledger is gone")

        real = queue._reddit_settle
        queue._reddit_settle = broken
        self.addCleanup(setattr, queue, "_reddit_settle", real)
        settled = self.finish(out["task_id"], reddit={"permalink": COMMENT})
        self.assertEqual(settled["status"], "ok")
        self.assertNotIn("reddit", settled)
        # The row is closed from the task's own status on the next look.
        conn = __import__("teeny_hands.reddit.rules", fromlist=["db"]).db()
        try:
            self.assertEqual(ledger.sync(conn), 1)
        finally:
            conn.close()
        self.assertEqual(self.rows()[0]["status"], "done")
