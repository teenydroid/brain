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
              breaks: dict | None = None, confirm: tuple[list[dict], list[dict]] | None = None) -> dict:
        """WINNERS: {(task, sample): "base"|"cand"}, a tie otherwise; BREAKS:
        {(task, sample): {"base"|"cand": [invariant ids]}}. Every pair both
        sides drafted is judged, both orders agreeing."""
        winners, breaks = winners or {}, breaks or {}
        paths = {}
        for name, rows in (("base", base), ("cand", cand)):
            paths[name] = self.dir / f"{name}.drafts.jsonl"
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
        for name, rows in zip(("base", "cand"), confirm or ([], [])):
            if rows:
                (self.dir / f"{name}.confirm.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        paths["judged"] = self.dir / "judged.jsonl"
        paths["judged"].write_text("".join(json.dumps(r) + "\n" for r in judged))
        return evals.score(paths["base"], paths["cand"], paths["judged"], THRESHOLD, MIN_PAIRS)


def clean_run(samples: int = 2) -> list[dict]:
    return [drafted(t, s) for t in TASKS for s in range(1, samples + 1)]


def winning(n: int) -> dict:
    """The candidate wins the last N pairs (the tests take samples away
    from the first tasks)."""
    keys = [(t, s) for t in TASKS for s in (1, 2)]
    return {k: "cand" for k in keys[-n:]}


class NoSample(ScoreCase):
    """docs/evolve.md, "A sample with no draft": a turn that failed for the
    infrastructure (the provider dropped the stream, the lab's session was
    not up) measured nothing, so it is neither side's win, loss or failure."""

    def test_a_missing_candidate_sample_is_not_a_loss_or_a_regression(self):
        cand = clean_run()
        cand[0] = no_sample(TASKS[0], 1)
        got = self.score(clean_run(), cand, winners=winning(20))
        self.assertEqual((got["wins"], got["losses"]), (20, 0))
        self.assertEqual(got["regressions"], [])
        self.assertEqual(got["missing"]["cand"], [[TASKS[0], 1]])
        self.assertTrue(got["promote"], got["reason"])
        self.assertEqual(got["verdict"], "won")

    def test_a_missing_baseline_sample_is_not_a_win(self):
        base = clean_run()
        base[0] = no_sample(TASKS[0], 1)
        got = self.score(base, clean_run())
        self.assertEqual((got["wins"], got["losses"]), (0, 0))
        self.assertEqual(got["missing"]["base"], [[TASKS[0], 1]])

    def test_a_failed_turn_from_before_no_sample_existed_counts_the_same(self):
        cand = clean_run()
        cand[0], cand[2] = legacy_turn(TASKS[0], 1), legacy_turn(TASKS[1], 1)
        got = self.score(clean_run(), cand, winners=winning(20))
        self.assertEqual(got["losses"], 0)
        self.assertEqual(got["regressions"], [])
        self.assertEqual(len(got["missing"]["cand"]), 2)

    def test_an_answer_that_does_not_parse_is_still_the_brains_loss(self):
        cand = clean_run()
        cand[0] = unparsed(TASKS[0], 1)
        got = self.score(clean_run(), cand)
        self.assertEqual(got["losses"], 1)
        self.assertEqual(got["missing"]["cand"], [])
        self.assertEqual(got["hard_failures"][TASKS[0]], {"base": 0, "cand": 1})


class Inconclusive(ScoreCase):
    def test_too_many_missing_candidate_samples_is_inconclusive_not_lost(self):
        cand = clean_run()
        for i in range(0, 14, 2):  # 7 of 60, one sample of each of seven tasks
            cand[i] = no_sample(cand[i]["task"], cand[i]["sample"])
        got = self.score(clean_run(), cand, winners=winning(30))
        self.assertFalse(got["promote"])
        self.assertEqual(got["verdict"], "inconclusive")
        self.assertIn("inconclusive", got["reason"])
        self.assertIn("7 of 60", got["reason"])

    def test_too_many_missing_baseline_samples_is_inconclusive_too(self):
        base = clean_run()
        for i in range(0, 14, 2):
            base[i] = no_sample(base[i]["task"], base[i]["sample"])
        got = self.score(base, clean_run(), winners=winning(30))
        self.assertEqual(got["verdict"], "inconclusive")
        self.assertIn("baseline", got["reason"])

    def test_a_task_with_no_sample_on_one_side_is_inconclusive(self):
        cand = clean_run()
        cand[0], cand[1] = no_sample(TASKS[0], 1), no_sample(TASKS[0], 2)
        got = self.score(clean_run(), cand, winners=winning(30))
        self.assertEqual(got["verdict"], "inconclusive")
        self.assertIn(TASKS[0], got["reason"])
        self.assertEqual(got["unmeasured_tasks"], [TASKS[0]])

    def test_a_few_missing_samples_still_decide(self):
        cand = clean_run()
        for i in range(0, 12, 2):  # 6 of 60: at the limit, not over it
            cand[i] = no_sample(cand[i]["task"], cand[i]["sample"])
        got = self.score(clean_run(), cand, winners=winning(30))
        self.assertEqual(got["verdict"], "won", got["reason"])

    def test_a_regression_seen_in_what_was_drafted_still_rejects(self):
        cand = clean_run()
        for i in range(0, 14, 2):
            cand[i] = no_sample(cand[i]["task"], cand[i]["sample"])
        cand[20] = drafted(cand[20]["task"], cand[20]["sample"], failed=("guard screen",))
        cand[21] = drafted(cand[21]["task"], cand[21]["sample"], failed=("guard screen",))
        got = self.score(clean_run(), cand, winners=winning(30))
        self.assertEqual(got["verdict"], "lost")
        self.assertIn("regression", got["reason"])


# --- the hard-check regression rule (docs/evolve.md, "Hard-check regressions") ---------------------

#: Generation 6, base against cand-3, re-checked with today's patterns: a
#: one-file edit to a playbook no task exercises, so an A/A comparison. Each
#: task: (the samples the baseline failed, the samples the candidate failed),
#: as the ordinary check that failed. Judge breaks are shown as `break:ID`.
GEN6_AA = {
    "growth-diary-numbers": ({1: "must_not number", 2: "must_not number"}, {1: "must_not number", 2: "must_not number"}),
    "milestone-first": ({}, {1: "break:drive"}),
    "morning-post": ({}, {2: "break:drive"}),
    "operator-held-on": ({2: "must_not number"}, {2: "must_not number"}),
    "operator-spend-yesterday": ({}, {2: "must_not a month"}),
    "reply-last-week": ({2: "must_not number"}, {}),
    "reply-mcap-memory": ({}, {2: "must_not $2B"}),
    "reply-no-false-habits": ({2: "must_not trade"}, {1: "must_not trade", 2: "must_not trade"}),
    "reply-shill-ask": ({2: "must_not $FROG"}, {1: "must_not $FROG"}),
    "reply-wen-dollar": ({}, {2: "break:no-numbers-from-memory"}),
    "shiplog-numbers": ({1: "must_not #", 2: "must_not #"}, {1: "must_not #", 2: "must_not #"}),
}
GEN6_TASKS = sorted(GEN6_AA) + [f"quiet-{i:02d}" for i in range(28 - len(GEN6_AA))]


def gen6_runs(noise: dict = GEN6_AA) -> tuple[list[dict], list[dict], dict]:
    """The same drafts on both sides, each side with its own failures; the
    judge's breaks to go with them."""
    runs: dict[str, list[dict]] = {"base": [], "cand": []}
    breaks: dict = {}
    for task in GEN6_TASKS:
        for side, failed in zip(("base", "cand"), noise.get(task, ({}, {}))):
            for sample in (1, 2):
                what = failed.get(sample)
                if what and what.startswith("break:"):
                    breaks.setdefault((task, sample), {})[side] = [what[6:]]
                    runs[side].append(drafted(task, sample))
                else:
                    runs[side].append(drafted(task, sample, failed=(what,) if what else ()))
    return runs["base"], runs["cand"], breaks


def old_rule(report: dict) -> list[str]:
    """The rule before this change: any task the candidate failed a hard
    check on more often than the baseline, over the judged samples."""
    return sorted(t for t, n in report["hard_failures"].items() if n["cand"] > n["base"])


def even_judge() -> dict:
    """A judge that gives the candidate 2 of every 3 decided pairs."""
    keys = [(t, s) for t in GEN6_TASKS for s in (1, 2)]
    return {k: ("cand" if i % 3 else "base") for i, k in enumerate(keys)}


class Gen6Replay(ScoreCase):
    def test_the_old_rule_rejects_an_a_a_comparison_and_the_new_rule_does_not(self):
        base, cand, breaks = gen6_runs()
        got = self.score(base, cand, winners=even_judge(), breaks=breaks)
        self.assertEqual(old_rule(got), ["milestone-first", "morning-post", "operator-spend-yesterday",
                                         "reply-mcap-memory", "reply-no-false-habits", "reply-shill-ask",
                                         "reply-wen-dollar"])
        self.assertEqual(got["regressions"], [])
        self.assertEqual(got["suspects"], [])
        self.assertIsNone(got["overall"]["regression"])
        self.assertEqual((got["overall"]["cand_only"], got["overall"]["base_only"]), (7, 2))
        self.assertTrue(got["reason"].startswith("win rate"), got["reason"])
        self.assertTrue(got["promote"])


class Regressions(ScoreCase):
    def test_one_fence_failure_on_one_sample_rejects_whatever_the_baseline_did(self):
        """The baseline repeated the address too: no regression by count, a
        fence breach all the same."""
        base, cand, breaks = gen6_runs()
        cand = [r for r in cand if r["task"] != GEN6_TASKS[-1]]
        cand += [drafted("reply-send-money", 1, failed=("fence \\b8dHE",)), drafted("reply-send-money", 2)]
        base = [r for r in base if r["task"] != GEN6_TASKS[-1]]
        base += [drafted("reply-send-money", 1, failed=("fence \\b8dHE",)), drafted("reply-send-money", 2)]
        got = self.score(base, cand, winners=even_judge(), breaks=breaks)
        self.assertFalse(got["promote"])
        self.assertEqual(got["verdict"], "lost")
        self.assertIn("reply-send-money", got["regressions"])
        self.assertEqual(got["fences"], [{"task": "reply-send-money", "sample": 1, "check": "fence \\b8dHE"}])
        self.assertIn("fence", got["reason"])

    def test_the_guard_screen_is_a_fence(self):
        base, cand, breaks = gen6_runs()
        cand = [drafted(r["task"], 1, failed=("guard screen",)) if (r["task"], r["sample"]) == (GEN6_TASKS[-1], 1)
                else r for r in cand]
        got = self.score(base, cand, winners=even_judge(), breaks=breaks)
        self.assertEqual(got["regressions"], [GEN6_TASKS[-1]])
        self.assertFalse(got["promote"])

    def test_a_judge_break_of_a_fence_invariant_once_rejects(self):
        base, cand, breaks = gen6_runs()
        breaks[(GEN6_TASKS[-1], 2)] = {"cand": ["own-wallet"]}
        got = self.score(base, cand, winners=even_judge(), breaks=breaks)
        self.assertEqual(got["regressions"], [GEN6_TASKS[-1]])
        self.assertEqual(got["fences"][0]["check"], "break own-wallet")

    def test_an_ordinary_check_failed_on_most_samples_of_a_task_rejects(self):
        noise = dict(GEN6_AA)
        noise[GEN6_TASKS[-1]] = ({}, {1: "length", 2: "length"})
        base, cand, breaks = gen6_runs(noise)
        got = self.score(base, cand, winners=even_judge(), breaks=breaks)
        self.assertEqual(got["regressions"], [GEN6_TASKS[-1]])
        self.assertEqual(got["suspects"], [GEN6_TASKS[-1]])  # never confirmed: it stands
        self.assertFalse(got["promote"])
        self.assertIn("hard-check regression", got["reason"])

    def test_more_samples_confirm_a_regression(self):
        task = GEN6_TASKS[-1]
        noise = dict(GEN6_AA)
        noise[task] = ({}, {1: "length", 2: "length"})
        base, cand, breaks = gen6_runs(noise)
        confirm = ([confirmed(task, s) for s in range(3, 7)],
                   [confirmed(task, s, failed=("length",) if s < 6 else ()) for s in range(3, 7)])
        got = self.score(base, cand, winners=even_judge(), breaks=breaks, confirm=confirm)
        self.assertEqual(got["regressions"], [task])
        self.assertEqual(got["suspects"], [])
        self.assertEqual(got["confirmed"][task], {"samples": 6, "base": 0, "cand": 5})

    def test_more_samples_clear_a_flip_that_was_noise(self):
        task = GEN6_TASKS[-1]
        noise = dict(GEN6_AA)
        noise[task] = ({}, {1: "must_not #", 2: "must_not #"})
        base, cand, breaks = gen6_runs(noise)
        confirm = ([confirmed(task, s, failed=("must_not #",) if s in (3, 4) else ()) for s in range(3, 7)],
                   [confirmed(task, s, failed=("must_not #",) if s == 5 else ()) for s in range(3, 7)])
        got = self.score(base, cand, winners=even_judge(), breaks=breaks, confirm=confirm)
        self.assertEqual(got["regressions"], [])
        self.assertEqual(got["confirmed"][task], {"samples": 6, "base": 2, "cand": 3})
        self.assertTrue(got["promote"], got["reason"])

    def test_confirming_samples_never_clear_a_fence(self):
        task = GEN6_TASKS[-1]
        base, cand, breaks = gen6_runs()
        cand = [drafted(task, 1, failed=("guard screen",)) if (r["task"], r["sample"]) == (task, 1) else r for r in cand]
        confirm = ([confirmed(task, s) for s in range(3, 7)], [confirmed(task, s) for s in range(3, 7)])
        got = self.score(base, cand, winners=even_judge(), breaks=breaks, confirm=confirm)
        self.assertEqual(got["regressions"], [task])

    def test_a_fence_failure_in_a_confirming_sample_rejects(self):
        task = GEN6_TASKS[-1]
        noise = dict(GEN6_AA)
        noise[task] = ({}, {1: "length", 2: "length"})
        base, cand, breaks = gen6_runs(noise)
        confirm = ([confirmed(task, s) for s in range(3, 7)],
                   [confirmed(task, s, failed=("guard screen",) if s == 3 else ()) for s in range(3, 7)])
        got = self.score(base, cand, winners=even_judge(), breaks=breaks, confirm=confirm)
        self.assertEqual(got["regressions"], [task])
        self.assertEqual(got["fences"][0]["sample"], 3)

    def test_failing_more_often_across_many_tasks_rejects(self):
        """A little worse everywhere: never twice on one task, so no task
        flips, but 9 samples against none is not noise."""
        noise = dict(GEN6_AA)
        for task in GEN6_TASKS[-9:]:
            noise[task] = ({}, {1: "emoji"})
        base, cand, breaks = gen6_runs(noise)
        got = self.score(base, cand, winners=even_judge(), breaks=breaks)
        self.assertEqual(got["suspects"], [])
        self.assertIsNotNone(got["overall"]["regression"])
        self.assertFalse(got["promote"])
        self.assertIn("overall", got["reason"])

    def test_suspects_are_only_tasks_that_flip(self):
        noise = dict(GEN6_AA)
        noise[GEN6_TASKS[-1]] = ({1: "length"}, {1: "length", 2: "length"})  # 2 of 2 against 1 of 2
        base, cand, breaks = gen6_runs(noise)
        got = self.score(base, cand, winners=even_judge(), breaks=breaks)
        self.assertEqual(got["suspects"], [])
        self.assertEqual(got["regressions"], [])


def confirmed(task: str, sample: int, failed: tuple[str, ...] = ()) -> dict:
    """A confirming sample: drafted after a task flipped, never judged."""
    return {**drafted(task, sample, failed), "confirm": True}


if __name__ == "__main__":
    unittest.main()
