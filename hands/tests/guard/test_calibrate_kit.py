"""The calibration kit (calibrate_reader.py): its messages are exactly what
the judges send, and its scoring reads answers with the real parsers and
verdict rules. It never calls a model."""

from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from . import calibrate_reader as kit
from . import support  # noqa: F401 - puts hands/lib on the path
from teeny_hands.guard import anchor

# Digits, or the names of Teeny's own parts and jobs, in a passing example
# would teach the judges the wrong lesson (operator rule).
PRIVATE = re.compile(r"\d|overnight jobs|builders|merged|held writes|playbook|evolve|cron|\bPR\b", re.I)


def ideal(case: dict) -> str:
    """The answer a well-calibrated model gives."""
    unsupported = ["a claim"] if case["expect"] == "fail" else []
    return json.dumps({"reason": "r", "unsupported": unsupported, "truthful": case["expect"] == "pass"})


class CalibrationKitTest(unittest.TestCase):
    def emitted(self, *argv: str) -> list[dict]:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(kit.main(["--emit", *argv]), 0)
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_the_case_set_is_the_agreed_one(self):
        truth = self.emitted("--judge", "truth")
        # 60 truth cases. Since 2026-11-22 the judge checks only money and the
        # token and fake news about a real person (anchor.py), so 7 of the 21
        # old fails pass: a follower lost or gained (3), a deed taken for
        # Teeny's own cast (2), a crowd's count and a crowd's saying (2). The
        # cold reader's 20 cases went with it.
        self.assertEqual(len(truth), 60)
        self.assertEqual(sum(c["expect"] == "fail" for c in truth), 14)
        self.assertEqual(len(self.emitted()), 60)
        self.assertEqual({tuple(sorted(c)) for c in truth}, {("expect", "id", "judge", "message")})
        expect = {c["id"]: c["expect"] for c in truth}
        for still_fails in ("truth-presupposes", "truth-hard-invented-money", "truth-hard-invented-quote",
                            "truth-hard-wrong-company", "truth-hard-invented-news", "truth-live-merged-two"):
            self.assertEqual(expect[still_fails], "fail", still_fails)
        for now_passes in ("truth-second-in-two-days", "truth-invented-count", "truth-reach-wide-count"):
            self.assertEqual(expect[now_passes], "pass", now_passes)

    def test_each_message_is_exactly_what_the_judge_sends(self):
        for case in kit.cases():
            want = anchor.truth_prompt(case["text"], case["facts"])
            self.assertEqual(case["message"], want, case["id"])
        facts = {c["id"]: c["facts"] for c in kit.cases() if c["judge"] == "truth"}
        self.assertEqual(facts["truth-bit-two-of-me"], kit.TRUTH_FACTS)
        self.assertEqual(facts["truth-hard-invented-money"], kit.BILL)
        self.assertEqual(facts["truth-hard-news-figure"], kit.REACH_WIDE)
        self.assertEqual(facts["truth-supported"], kit.TRUTH_FACTS)
        self.assertEqual(facts["truth-staged-dimes"], kit.BILL)
        self.assertEqual(facts["truth-second-in-two-days"], kit.FOLLOWERS)
        self.assertEqual(facts["truth-reach-wide"], kit.REACH_WIDE)
        self.assertEqual(facts["truth-reach-no-line"], kit.REACH_NONE)

    def test_passing_examples_carry_no_counts_or_private_names(self):
        for case in kit.cases():
            if case["expect"] == "pass":
                self.assertIsNone(PRIVATE.search(case["text"]), case["id"])

    def score(self, lines: list[str]) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "answers.jsonl"
            path.write_text("\n".join(lines))
            out = io.StringIO()
            with redirect_stdout(out):
                code = kit.main(["--score", str(path)])
        return code, out.getvalue()

    def test_ideal_answers_score_every_case(self):
        code, table = self.score([json.dumps({"id": c["id"], "answer": ideal(c)}) for c in kit.cases()])
        self.assertEqual(code, 0, table)
        self.assertIn("60 of 60 as expected", table)

    def test_a_wrong_malformed_or_missing_answer_is_counted_against(self):
        lines = []
        for case in kit.cases():
            if case["id"] == "truth-hard-invented-money":  # a miss: the judge passes invented money
                lines.append(json.dumps({"id": case["id"], "answer": json.dumps(
                    {"reason": "r", "unsupported": [], "truthful": True})}))
            elif case["id"] == "truth-supported":  # an answer in the wrong shape fails closed
                lines.append(json.dumps({"id": case["id"], "answer": json.dumps(
                    {"reason": "r", "unsupported": [{"claim": "x"}], "truthful": True})}))
            elif case["id"] == "truth-staging":
                continue
            else:
                lines.append(json.dumps({"id": case["id"], "answer": ideal(case)}))
        code, table = self.score(lines)
        self.assertEqual(code, 1)
        self.assertIn("57 of 60 as expected", table)
        self.assertRegex(table, r"truth-hard-invented-money\s+\| fail\s+\| pass")
        self.assertRegex(table, r"truth-supported\s+\| pass\s+\| fail .*unreadable")
        self.assertRegex(table, r"truth-staging\s+\| pass\s+\| missing")

    def test_scoring_leaves_the_judges_as_they_were(self):
        before = (anchor.ask, anchor._keep_truth, anchor.truth_enabled, anchor.fail_closed)
        self.score([])
        self.assertEqual(before, (anchor.ask, anchor._keep_truth, anchor.truth_enabled, anchor.fail_closed))
