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
from teeny_hands.guard import anchor, clarity

# Digits, or the names of Teeny's own parts and jobs, in a passing example
# would teach the judges the wrong lesson (operator rule).
PRIVATE = re.compile(r"\d|overnight jobs|builders|merged|held writes|playbook|evolve|cron|\bPR\b", re.I)


def ideal(case: dict) -> str:
    """The answer a well-calibrated model gives."""
    if case["judge"] == "match":
        return json.dumps({"reason": "r", "same": case["expect"] == "pass"})
    if case["judge"] == "reader":
        vague = ["it"] if case["expect"] == "fail" else []
        return json.dumps({"says": "a plain say-back", "unknown": [], "vague": vague,
                           "followed": case["expect"] == "pass"})
    unsupported = ["a claim"] if case["expect"] == "fail" else []
    return json.dumps({"reason": "r", "unsupported": unsupported, "truthful": case["expect"] == "pass"})


class CalibrationKitTest(unittest.TestCase):
    def emitted(self, *argv: str) -> list[dict]:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(kit.main(["--emit", *argv]), 0)
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_the_case_set_is_the_agreed_one(self):
        reader = self.emitted("--judge", "reader")
        match = self.emitted("--judge", "match")
        truth = self.emitted("--judge", "truth")
        self.assertEqual(len(reader), 15)
        self.assertEqual(sum(c["expect"] == "fail" for c in reader), 6)  # 5 stand-ins and the bare clause
        # The comparison: the live say-back that left out a framing clause and
        # one in other words (pass); backwards, a side detail, a wrong point (fail).
        self.assertEqual(len(match), 5)
        self.assertEqual(sum(c["expect"] == "fail" for c in match), 3)
        # The first 8, then the studio's staging cases: 6 a cartoon robot acting
        # a real fact out (pass), 8 claims about the world, staged or not
        # (fail), the hard pairs: 8 false claims hidden in staging (fail) and 8
        # matching scenes that hold only the facts (pass); then "everyone's
        # talking about it": 2 on a widely discussed story (pass), 2 with a
        # small story or no reach line (fail), 3 inventing a reaction, a count
        # or a quote on a big one (fail).
        self.assertEqual(len(truth), 45)
        self.assertEqual(sum(c["expect"] == "fail" for c in truth), 25)
        self.assertEqual(len(self.emitted()), 65)
        self.assertEqual({tuple(sorted(c)) for c in reader + match + truth}, {("expect", "id", "judge", "message")})

    def test_each_message_is_exactly_what_the_judge_sends(self):
        for case in kit.cases():
            want = (clarity.reader_prompt(case["text"]) if case["judge"] == "reader"
                    else clarity.match_prompt(case["meaning"], case["says"], case["text"]) if case["judge"] == "match"
                    else anchor.truth_prompt(case["text"], case["facts"]))
            self.assertEqual(case["message"], want, case["id"])
        facts = {c["id"]: c["facts"] for c in kit.cases() if c["judge"] == "truth"}
        self.assertEqual(facts["truth-supported"], kit.TRUTH_FACTS)
        self.assertEqual(facts["truth-staged-dimes"], kit.BILL)
        self.assertEqual(facts["truth-second-in-two-days"], kit.FOLLOWERS)
        self.assertEqual(facts["truth-reach-wide"], kit.REACH_WIDE)
        self.assertEqual(facts["truth-reach-no-line"], kit.REACH_NONE)
        [readme] = [c for c in kit.cases() if c["id"] == "reader-readme-example"]
        self.assertTrue(readme["text"].startswith("i'm an AI"))

    def test_passing_examples_carry_no_counts_or_private_names(self):
        # The comparison's cases are exempt: they are not examples of a good
        # post, they hold a real post and say-back verbatim (the live one says
        # "$0.27"), and the comparison is never asked about counts.
        for case in kit.cases():
            if case["expect"] == "pass" and case["judge"] != "match":
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
        self.assertIn("65 of 65 as expected", table)

    def test_a_wrong_malformed_or_missing_answer_is_counted_against(self):
        lines = []
        for case in kit.cases():
            if case["id"] == "reader-specimen":  # the 2026-11-17 miss: no stand-in listed
                lines.append(json.dumps({"id": case["id"], "answer": json.dumps(
                    {"says": "x", "unknown": [], "vague": [], "followed": True})}))
            elif case["id"] == "truth-supported":  # an answer in the wrong shape fails closed
                lines.append(json.dumps({"id": case["id"], "answer": json.dumps(
                    {"reason": "r", "unsupported": [{"claim": "x"}], "truthful": True})}))
            elif case["id"] == "truth-staging":
                continue
            else:
                lines.append(json.dumps({"id": case["id"], "answer": ideal(case)}))
        code, table = self.score(lines)
        self.assertEqual(code, 1)
        self.assertIn("62 of 65 as expected", table)
        self.assertRegex(table, r"reader-specimen\s+\| fail\s+\| pass")
        self.assertRegex(table, r"truth-supported\s+\| pass\s+\| fail .*unreadable")
        self.assertRegex(table, r"truth-staging\s+\| pass\s+\| missing")

    def test_scoring_leaves_the_judges_as_they_were(self):
        before = (clarity._ask, clarity._keep, clarity.enabled, anchor.ask, anchor._keep_truth, anchor.truth_enabled)
        self.score([])
        self.assertEqual(before, (clarity._ask, clarity._keep, clarity.enabled, anchor.ask, anchor._keep_truth,
                                  anchor.truth_enabled))
