"""hands/chan's command line: the contract's JSON line and exit code."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

from .support import Scratch, op
from teeny_hands.chan import cli


class CliTest(Scratch):
    def run_cli(self, *argv: str) -> tuple[int, dict]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(list(argv))
        return code, json.loads(out.getvalue().strip().splitlines()[-1])

    def test_sparks_answers_ok_with_its_premises(self):
        self.catalog("x", op(1), op(2, com="pigeons file reports to a bureau under the park"))
        code, got = self.run_cli("sparks", "--boards", "x", "--max", "2", "--seed", "7")
        self.assertEqual(code, 0)
        self.assertTrue(got["ok"])
        self.assertEqual(len(got["sparks"]), 2)

    def test_a_refused_board_is_an_error_line(self):
        code, got = self.run_cli("catalog", "pol")
        self.assertEqual(code, 1)
        self.assertFalse(got["ok"])
        self.assertIn("not a board Teeny reads", got["error"])

    def test_boards_lists_the_fence(self):
        code, got = self.run_cli("boards")
        self.assertEqual(code, 0)
        self.assertNotIn("pol", [b["board"] for b in got["boards"]])
        self.assertIn({"board": "x", "about": "paranormal, conspiracies, the unexplained", "default": True},
                      got["boards"])
