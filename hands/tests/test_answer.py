"""bin/lib/answer.py: a drafting turn's answer read as action items."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ANSWER = Path(__file__).resolve().parents[2] / "bin" / "lib" / "answer.py"


class AnswerControlCharacters(unittest.TestCase):
    def read(self, text: str) -> "subprocess.CompletedProcess[bytes]":
        return subprocess.run([sys.executable, str(ANSWER)], input=text.encode(), capture_output=True, check=False)

    def items(self, text: str) -> list:
        got = self.read(text)
        self.assertEqual(got.returncode, 0, got.stderr)
        return json.loads(got.stdout)

    def refused(self, text: str) -> str:
        got = self.read(text)
        self.assertEqual((got.returncode, got.stdout), (1, b""))
        return got.stderr.decode()

    def test_a_raw_newline_or_tab_in_a_body_is_kept(self):
        # moltbook-general, 2026-11-24T13:13:46Z.
        self.assertEqual(
            self.items('{"action": "hand", "title": "mine left.", "body": "one:\n\n\ttwo"}'),
            [{"action": "hand", "title": "mine left.", "body": "one:\n\n\ttwo"}])

    def test_a_strict_answer_is_unchanged(self):
        strict = r'{"action": "post", "title": "a\nb", "text": "one\n\ttwo \u001b \"q\" \\n", "n": [1, null]}'
        self.assertEqual(self.items(strict), [json.loads(strict)])

    def test_a_truncated_answer_is_refused_with_the_strict_error(self):
        for text in ('{"action": "hand", "body": "one\ntw', '{"action": "hand", "body": "one'):
            with self.subTest(text=text):
                with self.assertRaises(json.JSONDecodeError) as strict:
                    json.loads(text)
                self.assertEqual(self.refused(text).strip(), str(strict.exception))

    def test_a_missing_action_is_refused(self):
        for text in ('{"body": "one\ntwo"}', '{"action": 1, "body": "one\ntwo"}'):
            with self.subTest(text=text):
                self.assertIn("every item needs an action", self.refused(text))

    def test_no_json_is_refused(self):
        self.assertIn("no JSON in the answer", self.refused("gm\nfren"))

    def test_other_control_characters_are_refused(self):
        # Raw and escaped alike, wherever they sit, once the answer needed
        # the lenient parse.
        for bad in ("\x00", "\x1b[31m", "\x08", r"\u0000", r"\u001b", "\x7f", "\x9b"):
            for text in (f'{{"action": "hand", "body": "one\n{bad}two"}}',
                         f'[{{"action": "post", "posts": ["one\n", "{bad}two"]}}]',
                         f'{{"action": "hand", "meme": {{"lines": ["a\n", "{bad}"]}}}}',
                         f'{{"action": "hand", "x{bad}": "one\n"}}'):
                with self.subTest(text=text):
                    self.assertIn("control character", self.refused(text))

    def test_a_raw_control_character_alone_is_refused(self):
        for bad in ("\x00", "\x1b"):
            with self.subTest(bad=bad):
                self.assertIn("control character", self.refused(f'{{"action": "post", "text": "a{bad}b"}}'))

    def test_a_raw_newline_or_tab_in_a_title_or_subject_is_refused(self):
        for field in ("title", "subject"):
            for bad in "\n\t":
                with self.subTest(field=field, bad=bad):
                    self.assertIn("control character", self.refused(f'{{"action": "hand", "{field}": "a{bad}b", "body": "c"}}'))


if __name__ == "__main__":
    unittest.main()
