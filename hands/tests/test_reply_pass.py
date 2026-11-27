"""The mind's inbox-pass block (bin/lib/reply_pass.py) and the tick leaving
what the heartbeat drains (docs/mind.md, "The wake")."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))
sys.path.insert(0, str(REPO / "hands" / "lib"))

import reply_pass  # noqa: E402
from teeny_hands import hand, stimuli  # noqa: E402


def mention(i, text, who="a"):
    return {"id": str(i), "author_username": who, "text": text}


class Block(unittest.TestCase):
    def test_empty_inbox_adds_nothing(self):
        self.assertEqual(reply_pass.render(0, False, [], 0, []), "")

    def test_it_lists_the_count_and_the_best_few_with_ids_and_authors(self):
        ms = [mention(i, "gm") for i in range(9, 1, -1)] + [mention(1, "what is your plan for $TINY this week?", "bob")]
        top = reply_pass.candidates(ms)
        self.assertEqual(top[0]["id"], "1", "a real question outranks newer one-word mentions")
        text = reply_pass.render(9, False, top, 0, [])
        self.assertIn("X mentions waiting: 9", text)
        self.assertIn("- x 1 @bob: what is your plan", text)
        self.assertEqual(len(top), reply_pass.X_SHOWN)

    def test_it_stays_under_its_size_cap_however_much_waits(self):
        ms = [mention(i, "x" * 500, "u" * 15) for i in range(100)]
        mail = [{"ref": f"m{i}", "who": "w" * 80, "text": "t" * 900} for i in range(2)]
        text = reply_pass.render(100, True, reply_pass.candidates(ms, 50), 5, mail)
        self.assertLessEqual(len(text), reply_pass.MAX_CHARS)
        self.assertIn("100+", text)
        for line in text.splitlines():
            self.assertLessEqual(len(line), 260)

    def test_the_block_names_the_caps_the_guardrails_enforce(self):
        text = reply_pass.render(1, False, [mention(1, "hello there friend")], 0, [])
        self.assertIn("at most 3 X replies and 2 mail replies", text)


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        (Path(self.tmp.name) / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = self.tmp.name
        os.environ["TEENY_NOW"] = "1789369200"
        (Path(self.tmp.name) / "secrets" / "teeny.env").write_text("MIND=1\n")
        os.environ.pop("DRY_RUN", None)
        hand._ENV_CACHE = None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def mention(self, i, status="offered", text="what is the plan this week?"):
        from teeny_hands.x import mentions
        conn = mentions._db()
        conn.execute("INSERT INTO x_mentions (id, author_id, author_username, text, post, status, seen_at, updated_at)"
                     " VALUES (?, 'u', 'ana', ?, '{}', ?, 1, 1)", (str(i), text, status))
        return conn


class Backlogs(Scratch):
    def test_a_mention_answered_through_hands_x_reply_is_not_offered_again(self):
        conn = self.mention(5)
        self.mention(6)
        conn.execute("INSERT INTO x_replies (in_reply_to, claimed_at, reply_id, replied_at) VALUES ('5', 1, '900', 1)")
        stimuli.add("x", "mention", "5", "what is the plan", who="ana")
        text = reply_pass.build()
        self.assertIn("X mentions waiting: 1", text)
        self.assertNotIn("- x 5 ", text)
        self.assertIn("- x 6 ", text)
        self.assertEqual(conn.execute("SELECT status FROM x_mentions WHERE id = '5'").fetchone()["status"], "replied")
        self.assertEqual(stimuli.counts()["pending"], 0, "its stimulus is closed too, so the tick never sees it")

    def test_a_claimed_reply_that_never_went_out_does_not_hide_the_mention(self):
        conn = self.mention(5)
        conn.execute("INSERT INTO x_replies (in_reply_to, claimed_at) VALUES ('5', 1)")
        self.assertIn("- x 5 ", reply_pass.build())

    def test_two_hundred_older_x_mentions_do_not_hide_the_mail_waiting(self):
        for i in range(250):
            stimuli.add("x", "mention", str(i), f"hello {i}", who="ana")
        stimuli.add("mail", "message", "m1", "a question for you", who="bo@example.com")
        text = reply_pass.build()
        self.assertIn("Mail waiting: 1", text)
        self.assertIn("- mail m1 ", text)


class TickLeavesTheDrain(unittest.TestCase):
    def test_the_drained_kinds_are_x_mentions_and_mail(self):
        self.assertEqual(set(stimuli.DRAINED), {("x", "mention"), ("mail", "message")})

    def test_the_switch_missing_means_drain_only_while_the_mind_is_on(self):
        from unittest import mock
        values = {}
        with mock.patch.object(stimuli.hand, "setting", side_effect=lambda n, d=None: values.get(n, d)):
            self.assertFalse(stimuli.heartbeat_drains(), "no mind awake: the tick keeps answering")
            values["MIND"] = "1"
            self.assertTrue(stimuli.heartbeat_drains(), "switch missing means the new behaviour")
            self.assertEqual(stimuli.drained(), stimuli.DRAINED)
            values["TEENY_HEARTBEAT_DRAIN"] = "0"
            self.assertEqual(stimuli.drained(), ())


if __name__ == "__main__":
    unittest.main()
