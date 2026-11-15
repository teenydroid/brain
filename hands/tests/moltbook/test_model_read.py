"""The model read of a verification challenge: an answer only when two
differently worded readings agree and name numbers the text really has."""

from __future__ import annotations

import json
import unittest

from guard.support import FakeCore, error, result
from teeny_hands.moltbook import model_read

from .support import CHALLENGE, CORRECTED, Scratch


def reading(a, op, b) -> dict:
    """A core reply whose text is one reading of the challenge."""
    return result({"response": json.dumps({"a": a, "op": op, "b": b})})


class ModelReadTest(Scratch):
    def wire(self, *replies) -> FakeCore:
        fake = FakeCore(*replies)
        self.addCleanup(fake.close)
        (self.root / "state").mkdir(exist_ok=True)
        (self.root / "state" / "core.url").write_text(fake.url + "\n")
        (self.root / "secrets" / "core.token").write_text("tok\n")
        self.configure(MOLTBOOK_CHALLENGE_MODEL="1")
        return fake

    def test_two_agreeing_readings_answer_the_self_correction(self):
        fake = self.wire(reading(23, "*", 7))
        self.assertEqual(model_read.solve(CORRECTED), "161.00")
        self.assertEqual(len(fake.calls), 2)
        first, second = fake.messages()
        self.assertNotEqual(first, second)
        self.assertIn(CORRECTED, first)
        self.assertIn(CORRECTED, second)

    def test_readings_that_disagree_answer_nothing(self):
        self.wire(reading(23, "*", 7), reading(50, "*", 7))
        self.assertIsNone(model_read.solve(CORRECTED))

    def test_a_number_the_text_does_not_have_answers_nothing(self):
        self.wire(reading(24, "*", 7))
        self.assertIsNone(model_read.solve(CORRECTED))

    def test_the_operation_is_checked_and_division_by_zero_refused(self):
        for reply in (reading(23, "^", 7), reading(23, "/", 0), reading("x", "*", 7)):
            with self.subTest(reply=reply):
                self.wire(reply)
                self.assertIsNone(model_read.solve(CORRECTED))

    def test_an_unreadable_answer_or_a_core_error_answers_nothing(self):
        for reply in (result({"response": "the answer is 161"}), error("provider stream ended")):
            with self.subTest(reply=reply):
                self.wire(reply)
                self.assertIsNone(model_read.solve(CORRECTED))

    def test_a_fenced_answer_is_read(self):
        self.wire(result({"response": '```json\n{"a": 20, "op": "-", "b": 5}\n```'}))
        self.assertEqual(model_read.solve(CHALLENGE), "15.00")

    def test_off_asks_nothing(self):
        fake = self.wire(reading(23, "*", 7))
        self.configure(MOLTBOOK_CHALLENGE_MODEL="0")
        self.assertIsNone(model_read.solve(CORRECTED))
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
