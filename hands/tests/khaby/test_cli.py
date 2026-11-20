"""hands/khaby's verbs through the hand's runner: one JSON object out, exit
0, 2 or 1; and `used`, the one write, dry and live."""

from __future__ import annotations

import contextlib
import io
import json
from unittest import mock

from .support import TT_URL, Scratch
from teeny_hands.khaby import cli
from teeny_hands.remake import ledger

BODY = {"video_id": "7300", "source": "tiktok", "url": TT_URL, "title": "the hack", "study_id": "tt-7300"}


class CliTest(Scratch):
    def run_cli(self, argv, body=None):
        out = io.StringIO()
        stdin = io.StringIO(json.dumps(body) if body is not None else "")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), mock.patch("sys.stdin", stdin):
            code = cli.main(argv)
        return code, json.loads(out.getvalue())

    def test_no_verb_is_an_error(self):
        code, got = self.run_cli([])
        self.assertEqual(code, 1)
        self.assertEqual(got["error"], "hands/khaby needs a verb")

    def test_dissect_with_no_model_set_exits_two_with_the_model_rule(self):
        code, got = self.run_cli(["dissect"], {"url": TT_URL})
        self.assertEqual((code, got["ok"], got["rule"]), (2, False, "model"))
        self.assertIn("KHABY_DISSECT_MODEL", got["refused"])

    def test_dissect_off_exits_two(self):
        self.secrets("KHABY_DISSECT_MODEL=m\nKHABY_DISSECT_MAX_PER_DAY=0\n")
        code, got = self.run_cli(["dissect"], {"url": TT_URL})
        self.assertEqual((code, got["rule"]), (2, "off"))

    def test_find_with_no_account_answers_an_empty_find(self):
        self.secrets("KHABY_ACCOUNTS=,\n")
        code, got = self.run_cli(["find", "--max", "5"])
        self.assertEqual(code, 0)
        self.assertEqual((got["ok"], got["candidates"], got["unavailable"]), (True, [], [{"provider": "tiktok", "reason": "off"}]))

    def test_find_max_is_bounded(self):
        code, got = self.run_cli(["find", "--max", "0"])
        self.assertEqual(code, 1)
        self.assertFalse(got["ok"])

    def test_used_in_a_dry_room_says_what_it_would_mark(self):
        code, got = self.run_cli(["used"], BODY)
        self.assertEqual((code, got["ok"], got["dry_run"]), (0, True, True))
        self.assertEqual(got["would_mark"]["video_id"], "7300")
        self.assertFalse(ledger.seen("7300"))

    def test_used_live_marks_the_video_for_both_pipelines(self):
        self.secrets("DRY_RUN=0\n")
        code, got = self.run_cli(["used"], BODY)
        self.assertEqual((code, got["marked"]), (0, True))
        self.assertTrue(ledger.seen("7300"))
        self.assertEqual(self.run_cli(["used"], BODY)[1]["marked"], False)

    def test_used_checks_its_body(self):
        self.secrets("DRY_RUN=0\n")
        code, got = self.run_cli(["used"], {**BODY, "video_id": "../x"})
        self.assertEqual((code, got["ok"]), (1, False))
        self.assertFalse(ledger.seen("7300"))
