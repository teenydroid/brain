"""bin/lib/post_length.py: bin/act measures a rewrite by the guard's own rule
before it sends one (on 2026-11-17 a 304-character rewrite reached hands/x,
was refused, and the slot's post was lost). bin/test/act_test.sh runs it
through bin/act; these pin the measure."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "bin" / "lib" / "post_length.py"
sys.path.insert(0, str(REPO / "bin" / "lib"))
sys.path.insert(0, str(REPO / "hands" / "lib"))

import post_length  # noqa: E402
from teeny_hands.guard.gate import _hard_limits  # noqa: E402


class MeasureTest(unittest.TestCase):
    def test_x_is_280_and_the_over_text_is_named(self):
        got = post_length.measure("x", ["a" * 280, "b" * 281])
        self.assertEqual(got, {"limit": 280, "lengths": [280, 281], "over": [1]})

    def test_counts_as_the_guard_refuses(self):
        # An emoji is two UTF-16 units, as X and the guard count it.
        text = "\U0001F600" * 141
        self.assertEqual(post_length.measure("x", [text])["lengths"], [282])
        self.assertIsNotNone(_hard_limits("text", text, 280, 999))
        fits = "\U0001F600" * 140
        self.assertEqual(post_length.measure("x", [fits])["over"], [])
        self.assertIsNone(_hard_limits("text", fits, 280, 999))

    def test_the_surface_limit_is_the_guards(self):
        self.assertEqual(post_length.measure("telegram", [])["limit"], 4096)


class CliTest(unittest.TestCase):
    def run_it(self, body: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), "x"], input=body, capture_output=True, text=True, check=False)

    def test_prints_the_measure(self):
        done = self.run_it(json.dumps(["hi"]))
        self.assertEqual(done.returncode, 0)
        self.assertEqual(json.loads(done.stdout), {"limit": 280, "lengths": [2], "over": []})

    def test_refuses_a_body_that_is_not_a_list_of_strings(self):
        self.assertEqual(self.run_it('{"text": "hi"}').returncode, 2)
        self.assertEqual(self.run_it("not json").returncode, 2)


if __name__ == "__main__":
    unittest.main()
