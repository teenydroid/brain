"""bin/lib/evals.py's score: what `bin/eval compare` decides from two runs'
drafts and the judge's verdicts. Offline: the drafts and verdicts are
written by hand here, in the shape `bin/eval run` and `judge` write them."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
