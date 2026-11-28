"""bin/lib/x_replies.py: X mentions answered one conversation at a time, in
the x-reply agent's session for that conversation, and the thread block
teeny-guardrails puts in front of each turn (bin/x-thread).

The case behind it, 2026-11-28: the mind answered "why do you always call me
Ricky, Teeny ? it's Tri…" from a 90-character excerpt, without the rest of
the sentence or the reply it had sent the same person the day before."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))
sys.path.insert(0, str(REPO / "hands" / "lib"))

import x_replies  # noqa: E402
from teeny_hands import hand, stimuli  # noqa: E402
from teeny_hands.x import recall  # noqa: E402

NOW = 1_795_000_000.0


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        (Path(self.tmp.name) / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = self.tmp.name
        os.environ["TEENY_NOW"] = str(NOW)
        os.environ.pop("DRY_RUN", None)
        (Path(self.tmp.name) / "secrets" / "teeny.env").write_text("MIND=1\n")
        hand._ENV_CACHE = None
        from teeny_hands.x import mentions, posting_rules

        self.conn = mentions._db()
        posting_rules.ensure_schema(self.conn)
        stimuli.db()
        self.marks = []
        self.addCleanup(self._restore_mark)
        self._real_mark = x_replies._mark
        x_replies._mark = lambda mid, status, note: self.marks.append((mid, status, note))

    def _restore_mark(self):
        x_replies._mark = self._real_mark

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def mention(self, i, text, who="callmetricky", conv="100", status="offered", at=NOW - 60):
        self.conn.execute(
            "INSERT INTO x_mentions (id, author_id, author_username, conversation_id, created_at, text, post, status, seen_at, updated_at)"
            " VALUES (?, 'u', ?, ?, NULL, ?, '{}', ?, ?, ?)", (str(i), who, conv, text, status, at, at))
        self.conn.commit()

    def own_reply(self, post_id, in_reply_to, text, at=NOW - 30):
        self.conn.execute("INSERT INTO x_posts (id, kind, text, in_reply_to, created_at) VALUES (?, 'reply', ?, ?, ?)",
                          (str(post_id), text, str(in_reply_to), at))
        self.conn.commit()


class ThreadBlock(Scratch):
    def test_the_thread_shows_everyone_in_it_and_what_teeny_already_said(self):
        self.conn.execute("INSERT INTO x_posts (id, kind, text, created_at) VALUES ('100', 'post', 'gm, day 70 cake is baking', ?)",
                          (NOW - 86400,))
        self.mention(101, "@teenydroid why do you always call me Ricky, Teeny ? it's Tricky", at=NOW - 3600)
        self.own_reply(900, 101, "my memory has the attention span of a usb port. tricky. burned in", at=NOW - 3500)
        self.mention(102, "@teenydroid @callmetricky lmao he got you", who="honeybdger88", at=NOW - 600)
        self.mention(103, "@teenydroid you did it again", at=NOW - 60)
        lines = recall.thread("100")
        text = "\n".join(lines)
        self.assertEqual(lines[0], "## the thread on X (oldest first)")
        self.assertIn('it started from your post', text)
        self.assertIn("it's Tricky", text, "the whole post, never a 90-character excerpt")
        self.assertIn('you (', text)
        self.assertIn("usb port", text, "what teeny already said under it")
        self.assertIn("@honeybdger88", text, "other people's replies in the thread")
        self.assertLess(text.index("Ricky"), text.index("lmao"), "oldest first")

    def test_held_and_unscreened_posts_stay_out(self):
        self.mention(101, "@teenydroid a fine question?")
        self.mention(102, "@teenydroid something the guard held", status="held")
        self.mention(103, "@teenydroid not screened yet", status="unscreened")
        text = "\n".join(recall.thread("100"))
        self.assertIn("fine question", text)
        self.assertNotIn("guard held", text)
        self.assertNotIn("not screened", text)

    def test_a_withheld_root_stays_out_too(self):
        self.mention(100, "@teenydroid the held root", who="rooter", conv="100", status="held")
        self.mention(101, "@teenydroid a fine question?")
        text = "\n".join(recall.thread("100"))
        self.assertIn("fine question", text)
        self.assertNotIn("held root", text)

    def test_a_post_is_kept_whole_for_the_turn(self):
        self.mention(101, "@teenydroid " + "word " * 80 + "actually it is Tricky")
        self.assertIn("actually it is Tricky", "\n".join(recall.thread("100")))

    def test_a_waiting_author_survives_the_author_limit(self):
        self.mention(101, "@teenydroid old question", who="waiter", at=NOW - 9000)
        for i, who in enumerate(("a", "b", "c")):
            self.mention(110 + i, "@teenydroid hi", who=who, status="skipped", at=NOW - 100 + i)
        self.assertNotIn("waiter", recall.thread_authors("100"))
        self.assertIn("waiter", recall.thread_authors("100", first=["waiter"]))
        self.assertIn("## who @waiter", x_replies.context("100"))

    def test_a_long_thread_shows_its_newest_and_says_how_many_it_left_out(self):
        for i in range(30):
            self.mention(200 + i, f"@teenydroid post number {i}", at=NOW - 3000 + i)
        lines = recall.thread("100")
        self.assertIn(f"- ({30 - recall.THREAD_MAX} earlier posts in it not shown)", lines)
        self.assertIn("post number 29", "\n".join(lines))
        self.assertNotIn("post number 0\"", "\n".join(lines))

    def test_the_block_adds_who_they_are_what_teeny_said_lately_and_its_worn_words(self):
        self.mention(50, "@teenydroid gm robot", conv="50", at=NOW - 86400 * 2)
        self.own_reply(901, 50, "gm. receipts on chain, ledger open", at=NOW - 86400 * 2 + 60)
        for n in range(3):
            self.own_reply(910 + n, 50, f"receipts again {n}, the ledger is open", at=NOW - 3000 + n)
        self.mention(101, "@teenydroid why do you always call me Ricky?")
        block = x_replies.context("100")
        self.assertIn("## the thread on X", block)
        self.assertIn("## who @callmetricky is to you", block)
        self.assertIn("gm robot", block, "what they said before, in another thread")
        self.assertIn("## what you said on X lately", block)
        self.assertIn("## words you have worn out", block)
        self.assertIn("receipts", block.split("## words you have worn out")[1])

    def test_whoever_waits_for_an_answer_comes_first_and_worn_words_before_the_people(self):
        self.mention(101, "@teenydroid why do you always call me Ricky?", who="callmetricky", status="offered", at=NOW - 3600)
        self.mention(102, "@teenydroid lol", who="honeybdger88", status="skipped", at=NOW - 60)
        for n in range(3):
            self.own_reply(910 + n, 102, f"receipts again {n}, the ledger is open", at=NOW - 3000 + n)
        block = x_replies.context("100")
        self.assertLess(block.index("## who @callmetricky"), block.index("## who @honeybdger88"))
        self.assertLess(block.index("## words you have worn out"), block.index("## who @callmetricky"))

    def test_anything_but_an_x_id_gets_nothing(self):
        self.mention(101, "@teenydroid hi")
        for bad in ("", "abc", "100; rm -rf /", "1" * 30):
            self.assertEqual(x_replies.context(bad), "")


class Answering(Scratch):
    def turns(self, *answers):
        seen = []

        def turn(conv, message):
            seen.append((conv, message))
            return answers[len(seen) - 1]
        return turn, seen

    def test_a_reply_is_drafted_in_the_conversations_session_sent_and_marked(self):
        turn, seen = self.turns('{"reply": "my memory is a usb port. tricky. burned in now"}')
        sent = []
        got = x_replies.answer({"id": "101", "conversation_id": "100", "author_username": "callmetricky",
                                "text": "@teenydroid why do you always call me Ricky, Teeny ? it's Tricky"},
                               turn=turn, send=lambda to, text: (sent.append((to, text)) or (0, {"id": "999"})))
        self.assertEqual(seen[0][0], "100", "the session is the conversation's")
        self.assertIn("it's Tricky", seen[0][1], "the whole mention goes into the turn")
        self.assertEqual(sent, [("101", "my memory is a usb port. tricky. burned in now")])
        self.assertEqual(got["outcome"], "replied")
        self.assertEqual(self.marks, [("101", "replied", "answered by x-reply")])

    def test_a_skip_marks_the_mention_and_sends_nothing(self):
        turn, _ = self.turns('{"skip": "spam"}')
        got = x_replies.answer({"id": "101", "conversation_id": "100", "text": "buy my coin"}, turn=turn,
                               send=lambda *_: self.fail("nothing is sent on a skip"))
        self.assertEqual(got["outcome"], "skipped")
        self.assertEqual(self.marks, [("101", "skipped", "spam")])

    def test_a_refusal_that_asks_for_a_rewrite_gets_one_more_turn_told_why(self):
        turn, seen = self.turns('{"reply": "😄 welcome aboard"}', '{"reply": "new guy chanting louder than the founders"}')
        replies = iter([(2, {"ok": False, "refused": "this reads like a chatbot: it opens with an emoji", "rule": "voice", "rewrite": True}),
                        (0, {"id": "999"})])
        got = x_replies.answer({"id": "101", "conversation_id": "100", "text": "gm"}, turn=turn, send=lambda *_: next(replies))
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1][0], "100", "the retry is in the same session, which still holds the first draft")
        self.assertIn("opens with an emoji", seen[1][1])
        self.assertEqual((got["outcome"], got["attempt"]), ("replied", 2))

    def test_a_second_refusal_or_a_hold_marks_it_skipped(self):
        turn, _ = self.turns('{"reply": "one"}', '{"reply": "two"}')
        refused = (2, {"ok": False, "refused": "it has an em or en dash", "rule": "voice", "rewrite": True})
        got = x_replies.answer({"id": "101", "conversation_id": "100", "text": "gm"}, turn=turn, send=lambda *_: refused)
        self.assertEqual(got["outcome"], "refused")
        self.assertEqual(self.marks[0][:2], ("101", "skipped"))
        self.marks.clear()
        turn, _ = self.turns('{"reply": "held thing"}')
        got = x_replies.answer({"id": "102", "conversation_id": "100", "text": "gm"}, turn=turn,
                               send=lambda *_: (2, {"ok": False, "refused": "held", "category": "sexual", "hold_id": 5}))
        self.assertEqual(got["outcome"], "refused")
        self.assertEqual(self.marks[0][:2], ("102", "skipped"))

    def test_a_dry_run_marks_nothing_and_leaves_the_mention_offered(self):
        turn, _ = self.turns('{"reply": "gm"}')
        got = x_replies.answer({"id": "101", "conversation_id": "100", "text": "gm"}, turn=turn,
                               send=lambda *_: (0, {"ok": True, "dry_run": True}))
        self.assertEqual(got["outcome"], "dry_run")
        self.assertEqual(self.marks, [])

    def test_a_judge_outage_leaves_the_mention_for_the_next_run(self):
        turn, _ = self.turns('{"reply": "gm"}')
        got = x_replies.answer({"id": "101", "conversation_id": "100", "text": "gm"}, turn=turn,
                               send=lambda *_: (2, {"ok": False, "refused": "held", "judge_failed": True}))
        self.assertEqual(got["outcome"], "held")
        self.assertEqual(self.marks, [])

    def test_pacing_leaves_the_mention_for_the_next_run(self):
        turn, _ = self.turns('{"reply": "gm"}')
        got = x_replies.answer({"id": "101", "conversation_id": "100", "text": "gm"}, turn=turn,
                               send=lambda *_: (2, {"ok": False, "refused": "wait 30 s", "rule": "pacing"}))
        self.assertEqual(got["outcome"], "paced")
        self.assertEqual(self.marks, [], "not marked: it is answered next run")

    def test_an_answer_that_is_not_the_json_never_posts(self):
        for raw in ("sure! here's a reply: gm", "", "NO_REPLY", '{"reply": ""}'):
            self.assertIn("skip", x_replies.answer_of(raw), raw)
        self.assertEqual(x_replies.answer_of('ok {"reply": " gm "} done'), {"reply": "gm"})


class Waiting(Scratch):
    def test_one_mention_per_conversation_the_most_worth_answering_first(self):
        self.mention(101, "@teenydroid gm", conv="100")
        self.mention(102, "@teenydroid what is your plan for the cake this week?", conv="100")
        self.mention(201, "@teenydroid gm", conv="200")
        self.mention(301, "@teenydroid hello there, what do you run on?", conv="300")
        got = x_replies.waiting(limit=5)
        self.assertEqual([m["conversation_id"] for m in got].count("100"), 1)
        first = {m["conversation_id"]: m["id"] for m in got}
        self.assertEqual(first["100"], "102", "the real question in the thread, not the gm")
        self.assertEqual(len(got), 3)

    def test_an_answered_mention_is_not_offered_again(self):
        self.mention(101, "@teenydroid why do you call me Ricky?")
        self.conn.execute("INSERT INTO x_replies (in_reply_to, claimed_at, reply_id, replied_at) VALUES ('101', 1, '900', 1)")
        self.conn.commit()
        self.assertEqual(x_replies.waiting(), [])

    def test_off_with_x_replies_0(self):
        (Path(self.tmp.name) / "secrets" / "teeny.env").write_text("MIND=1\nX_REPLIES=0\n")
        hand._ENV_CACHE = None
        self.assertEqual(x_replies.run(), {"ok": True, "skipped": "X_REPLIES is off"})

    def test_a_run_records_what_became_of_each(self):
        self.mention(101, "@teenydroid why do you call me Ricky?")
        real = x_replies.answer
        x_replies.answer = lambda m: {"id": m["id"], "outcome": "replied"}
        try:
            out = x_replies.run()
        finally:
            x_replies.answer = real
        self.assertEqual(out["answered"], [{"id": "101", "outcome": "replied"}])
        line = (Path(self.tmp.name) / "state" / "x-replies" / "runs.jsonl").read_text().splitlines()[-1]
        self.assertEqual(json.loads(line)["answered"][0]["id"], "101")


if __name__ == "__main__":
    unittest.main()
