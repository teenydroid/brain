"""The mind's inbox-pass block (bin/lib/reply_pass.py) and the tick leaving
what the heartbeat drains (docs/mind.md, "The wake")."""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))
sys.path.insert(0, str(REPO / "hands" / "lib"))

import reply_pass  # noqa: E402
from teeny_hands import stimuli  # noqa: E402


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
