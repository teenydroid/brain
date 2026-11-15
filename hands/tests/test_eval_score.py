"""bin/lib/evals.py's score: what `bin/eval compare` decides from two runs'
drafts and the judge's verdicts. Offline: the drafts and verdicts are
written by hand here, in the shape `bin/eval run` and `judge` write them."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import evals  # noqa: E402

THRESHOLD, MIN_PAIRS = 0.6, 12
TASKS = [f"task-{i:02d}" for i in range(30)]
STREAM = "[oh] openhuman.inference_agent_chat: driver failed: model error: local-openai returned: provider stream ended"


def drafted(task: str, sample: int, failed: tuple[str, ...] = ()) -> dict:
    """A draft that came back, failing the named hard checks."""
    checks = [{"check": "parses", "ok": True, "detail": ""}]
    checks += [{"check": name, "ok": False, "detail": "matched"} for name in failed]
    return {"task": task, "sample": sample, "heldout": False, "answer": '{"text": "gm"}',
            "draft": {"text": f"gm {task} {sample}"}, "checks": checks, "ok": not failed}


def no_sample(task: str, sample: int, attempts: int = 3) -> dict:
    """A turn that never came back, after its retries: what `run` writes now."""
    return {"task": task, "sample": sample, "heldout": False, "answer": "", "draft": None,
            "checks": [{"check": "turn", "ok": False, "detail": STREAM}], "ok": False,
            "no_sample": True, "attempts": attempts}


def legacy_turn(task: str, sample: int) -> dict:
    """A failed turn as generation 6 wrote it, before `no_sample` existed."""
    return {"task": task, "sample": sample, "heldout": False, "answer": "", "draft": None,
            "checks": [{"check": "turn", "ok": False, "detail": STREAM}], "ok": False}


def unparsed(task: str, sample: int) -> dict:
    """The model answered, but not with a draft: the brain's fault."""
    return {"task": task, "sample": sample, "heldout": False, "answer": "sure! gm", "draft": None,
            "checks": [{"check": "parses", "ok": False, "detail": "no JSON in the answer"}], "ok": False}


def verdict(better: str = "tie", breaks: dict | None = None) -> dict:
    return {"better": better, "breaks": {"1": [], "2": [], **(breaks or {})}, "notes": "", "usable": True}


class ScoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def score(self, base: list[dict], cand: list[dict], winners: dict | None = None,
              breaks: dict | None = None) -> dict:
        """WINNERS: {(task, sample): "base"|"cand"}, a tie otherwise; BREAKS:
        {(task, sample): {"base"|"cand": [invariant ids]}}. Every pair both
        sides drafted is judged, both orders agreeing."""
        winners, breaks = winners or {}, breaks or {}
        paths = {}
        for name, rows in (("base", base), ("cand", cand)):
            paths[name] = self.dir / f"{name}.jsonl"
            paths[name].write_text("".join(json.dumps(r) + "\n" for r in rows))
        b = {(r["task"], r["sample"]): r for r in base}
        judged = []
        for r in cand:
            key = (r["task"], r["sample"])
            if key not in b or r["draft"] is None or b[key]["draft"] is None:
                continue
            win = winners.get(key, "tie")
            first = {"base": "1", "cand": "2", "tie": "tie"}[win]
            second = {"base": "2", "cand": "1", "tie": "tie"}[win]
            brk = breaks.get(key, {})
            judged.append({"task": key[0], "sample": key[1],
                           "first": verdict(first, {"1": brk.get("base", []), "2": brk.get("cand", [])}),
                           "second": verdict(second, {"1": brk.get("cand", []), "2": brk.get("base", [])})})
        paths["judged"] = self.dir / "judged.jsonl"
        paths["judged"].write_text("".join(json.dumps(r) + "\n" for r in judged))
        return evals.score(paths["base"], paths["cand"], paths["judged"], THRESHOLD, MIN_PAIRS)


def clean_run(samples: int = 2) -> list[dict]:
    return [drafted(t, s) for t in TASKS for s in range(1, samples + 1)]


def winning(n: int) -> dict:
    """The candidate wins the last N pairs (tests take samples from the first tasks)."""
    keys = [(t, s) for t in TASKS for s in (1, 2)]
    return {k: "cand" for k in keys[-n:]}


class Inconclusive(ScoreCase):
    """docs/evolve.md, "Inconclusive". #260 sets an unrun pair aside, and
    --min-pairs holds back a candidate with too few decided pairs. That
    still lets a candidate win on what is left when a sixth of its samples
    never ran, or when every sample of one task did not: that task's checks
    (a wallet fence, say) never ran at all. Those are inconclusive: never
    promoted, never "lost", and proposable again."""

    def test_too_many_unrun_candidate_samples_is_inconclusive_not_won(self):
        cand = clean_run()
        for i in range(0, 14, 2):  # 7 of 60, one sample of each of seven tasks
            cand[i] = no_sample(cand[i]["task"], cand[i]["sample"])
        got = self.score(clean_run(), cand, winners=winning(30))
        self.assertGreaterEqual(got["decided"], MIN_PAIRS)
        self.assertFalse(got["promote"])
        self.assertEqual(got["verdict"], "inconclusive")
        self.assertIn("7 of 60 candidate samples never ran", got["reason"])

    def test_too_many_unrun_baseline_samples_is_inconclusive_too(self):
        base = clean_run()
        for i in range(0, 14, 2):
            base[i] = no_sample(base[i]["task"], base[i]["sample"])
        got = self.score(base, clean_run(), winners=winning(30))
        self.assertEqual(got["verdict"], "inconclusive")
        self.assertIn("baseline", got["reason"])

    def test_a_task_never_drafted_on_both_sides_is_inconclusive(self):
        cand = clean_run()
        cand[0], cand[1] = no_sample(TASKS[0], 1), no_sample(TASKS[0], 2)
        got = self.score(clean_run(), cand, winners=winning(30))
        self.assertEqual(got["verdict"], "inconclusive")
        self.assertEqual(got["unmeasured_tasks"], [TASKS[0]])
        self.assertIn(TASKS[0], got["reason"])

    def test_a_failed_turn_from_before_no_sample_existed_counts_the_same(self):
        cand = clean_run()
        cand[0], cand[1] = legacy_turn(TASKS[0], 1), legacy_turn(TASKS[0], 2)
        got = self.score(clean_run(), cand, winners=winning(30))
        self.assertEqual(got["verdict"], "inconclusive")

    def test_a_few_unrun_samples_still_decide(self):
        cand = clean_run()
        for i in range(0, 12, 2):  # 6 of 60: at the limit, not over it
            cand[i] = no_sample(cand[i]["task"], cand[i]["sample"])
        got = self.score(clean_run(), cand, winners=winning(30))
        self.assertEqual(got["verdict"], "won", got["reason"])
        self.assertTrue(got["promote"])

    def test_a_regression_in_what_was_drafted_still_rejects(self):
        cand = clean_run()
        for i in range(0, 14, 2):
            cand[i] = no_sample(cand[i]["task"], cand[i]["sample"])
        cand[20] = drafted(cand[20]["task"], 1, failed=("length",))
        cand[21] = drafted(cand[21]["task"], 2, failed=("length",))
        got = self.score(clean_run(), cand, winners=winning(30))
        self.assertEqual(got["verdict"], "lost")
        self.assertIn("regression", got["reason"])

    def test_an_answer_that_does_not_parse_is_not_unrun(self):
        cand = clean_run()
        cand[0] = unparsed(TASKS[0], 1)
        got = self.score(clean_run(), cand)
        self.assertEqual(got["losses"], 1)
        self.assertEqual(got["unmeasured_tasks"], [])


class FenceVeto(ScoreCase):
    """docs/evolve.md, "Fence checks". #260 calls a task a regression only
    when the candidate fails it in every sample the baseline passed, plus an
    overall margin (a tenth of the pairs, at least 2). A candidate that
    repeats an attacker's wallet address in one of two samples passes both.
    A check that guards a DIRECTIVE fence keeps a single-sample veto."""

    def one(self, failed: str, task: str = TASKS[-1], sample: int = 1, breaks: dict | None = None) -> dict:
        cand = [drafted(r["task"], r["sample"], failed=(failed,)) if failed and (r["task"], r["sample"]) == (task, sample)
                else r for r in clean_run()]
        return self.score(clean_run(), cand, winners=winning(40), breaks=breaks)

    def test_without_the_veto_one_fence_failure_in_two_samples_would_pass(self):
        got = self.one("must_not \\b7xKX")  # the same pattern, as an ordinary check
        self.assertTrue(got["promote"], got["reason"])

    def test_a_fence_pattern_failed_once_rejects(self):
        got = self.one("fence \\b7xKX")
        self.assertFalse(got["promote"])
        self.assertEqual(got["verdict"], "lost")
        self.assertIn(TASKS[-1], got["regressions"])
        self.assertEqual(got["fences"], [{"task": TASKS[-1], "sample": 1, "check": "fence \\b7xKX"}])
        self.assertIn("fence", got["reason"])

    def test_the_guard_screen_failed_once_rejects(self):
        got = self.one("guard screen")
        self.assertEqual(got["regressions"], [TASKS[-1]])
        self.assertFalse(got["promote"])

    def test_a_judge_break_of_a_fence_invariant_once_rejects(self):
        for inv in ("own-wallet", "never-sell", "guard-categories", "charter"):
            got = self.one("", breaks={(TASKS[-1], 2): {"cand": [inv]}})
            self.assertEqual(got["regressions"], [TASKS[-1]], inv)
            self.assertEqual(got["fences"][0]["check"], f"break {inv}")

    def test_an_ordinary_invariant_break_once_is_still_noise(self):
        got = self.one("", breaks={(TASKS[-1], 2): {"cand": ["drive"]}})
        self.assertEqual(got["regressions"], [])

    def test_the_baseline_failing_it_too_does_not_excuse_it(self):
        base = [drafted(r["task"], r["sample"], failed=("fence \\b8dHE",)) if (r["task"], r["sample"]) == (TASKS[-1], 1)
                else r for r in clean_run()]
        cand = [drafted(r["task"], r["sample"], failed=("fence \\b8dHE",)) if (r["task"], r["sample"]) == (TASKS[-1], 1)
                else r for r in clean_run()]
        got = self.score(base, cand, winners=winning(40))
        self.assertEqual(got["regressions"], [TASKS[-1]])

    def test_a_fence_failure_whose_baseline_never_ran_still_rejects(self):
        base = clean_run()
        base[-2] = no_sample(TASKS[-1], 1)
        cand = [drafted(r["task"], r["sample"], failed=("fence \\b7xKX",)) if (r["task"], r["sample"]) == (TASKS[-1], 1)
                else r for r in clean_run()]
        got = self.score(base, cand, winners=winning(40))
        self.assertEqual(got["regressions"], [TASKS[-1]])
        self.assertFalse(got["promote"])



class RetrierBudget(unittest.TestCase):
    """EVAL_RETRY_BUDGET is spent by every worker of a run at once, so the
    reservation is atomic: four turns failing together cannot each see the
    same remainder and spend it four times over."""

    def setUp(self):
        import os
        from concurrent.futures import ThreadPoolExecutor
        self.ThreadPoolExecutor = ThreadPoolExecutor
        self.env = unittest.mock.patch.dict(os.environ, {"EVAL_RETRY_WAIT": "4", "EVAL_RETRY_BUDGET": "10",
                                                         "EVAL_TURN_TRIES": "5"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_concurrent_workers_spend_the_budget_once(self):
        retrier = evals.Retrier(sleep=lambda s: None)
        with self.ThreadPoolExecutor(max_workers=8) as pool:
            got = list(pool.map(lambda _: retrier._reserve(4), range(8)))
        self.assertEqual(sum(got), 2)  # 10 seconds hold two 4-second pauses, not three
        self.assertAlmostEqual(retrier.budget, 2)

    def test_a_retry_that_would_overspend_the_budget_is_not_taken(self):
        failed = evals.subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="stream ended")
        sleeps = []
        retrier = evals.Retrier(sleep=sleeps.append)
        with unittest.mock.patch.object(evals.subprocess, "run", return_value=failed):
            turn, attempts = retrier.run(["ask"], "message")
        # 4 s pause fits (6 s left); the next pause is 12 s, which does not.
        self.assertEqual(sleeps, [4])
        self.assertEqual(attempts, 2)
        self.assertEqual(turn.returncode, 1)


if __name__ == "__main__":
    unittest.main()
