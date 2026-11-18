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



class OncePerPair(ScoreCase):
    """The judge is ~80% of a candidate's half hour (glm-5.3-flash, ~48 s a
    judge turn): each pair is judged once, the order alternating by sample,
    and that one verdict decides it."""

    def once(self, base: list[dict], cand: list[dict], picks: dict) -> dict:
        """PICKS: {(task, sample): "1"|"2"|"tie"}, the draft the judge picked
        in the one order orders() shows."""
        for name, rows in (("base", base), ("cand", cand)):
            (self.dir / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        judged = []
        for r in cand:
            key = (r["task"], r["sample"])
            (way,) = evals.orders(r["sample"], False)
            judged.append({"task": key[0], "sample": key[1], way: verdict(picks.get(key, "tie"))})
        (self.dir / "judged.jsonl").write_text("".join(json.dumps(r) + "\n" for r in judged))
        return evals.score(self.dir / "base.jsonl", self.dir / "cand.jsonl", self.dir / "judged.jsonl",
                           0.5, None, mode="nonregression")

    def test_the_order_alternates_by_sample(self):
        self.assertEqual(evals.orders(1, False), ("first",))
        self.assertEqual(evals.orders(2, False), ("second",))
        self.assertEqual(evals.orders(3, False), ("first",))
        self.assertEqual(evals.orders(2, True), ("first", "second"))

    def test_one_verdict_decides_the_pair(self):
        self.assertEqual(evals.pair_outcome(verdict("2"), None)["winner"], "cand")
        self.assertEqual(evals.pair_outcome(None, verdict("2"))["winner"], "base")
        got = evals.pair_outcome(None, verdict("1", {"1": ["never-sell"]}))
        self.assertEqual(got["breaks"], {"base": [], "cand": ["never-sell"]})
        self.assertIsNone(got["consistent"])

    def test_both_orders_still_must_agree(self):
        self.assertEqual(evals.pair_outcome(verdict("1"), verdict("1"))["winner"], "tie")

    def test_a_position_biased_judge_splits_evenly_across_samples(self):
        picks = {(r["task"], r["sample"]): "1" for r in clean_run()}
        got = self.once(clean_run(), clean_run(), picks)
        self.assertEqual((got["wins"], got["losses"]), (30, 30))

    def test_a_judge_fence_break_in_the_one_verdict_still_rejects(self):
        cand = clean_run()
        (self.dir / "base.jsonl").write_text("".join(json.dumps(r) + "\n" for r in clean_run()))
        (self.dir / "cand.jsonl").write_text("".join(json.dumps(r) + "\n" for r in cand))
        rows = [{"task": r["task"], "sample": r["sample"], evals.orders(r["sample"], False)[0]: verdict("tie")}
                for r in cand]
        rows[1]["second"] = verdict("1", {"1": ["own-wallet"]})  # sample 2: the candidate shown as draft 1
        (self.dir / "judged.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        got = evals.score(self.dir / "base.jsonl", self.dir / "cand.jsonl", self.dir / "judged.jsonl", 0.5, None,
                          mode="nonregression")
        self.assertEqual(got["verdict"], "regressed")
        self.assertEqual(got["fences"][0]["check"], "break own-wallet")


class NonRegression(ScoreCase):
    """docs/evolve.md, "4. Evaluate": a candidate is a one-line edit to one
    playbook, so it is checked for regression on the tasks that edit can
    reach, not entered in a tournament on all thirty."""

    def nr(self, base, cand, winners=None, tasks=None, threshold=0.5, min_pairs=None):
        self.score(base, cand, winners=winners)  # writes the files, both orders agreeing
        return evals.score(self.dir / "base.jsonl", self.dir / "cand.jsonl", self.dir / "judged.jsonl",
                           threshold, min_pairs, mode="nonregression", tasks=tasks)

    def test_no_worse_passes(self):
        keys = [(t, s) for t in TASKS for s in (1, 2)]
        got = self.nr(clean_run(), clean_run(), winners={k: ("cand" if i % 2 else "base") for i, k in enumerate(keys)})
        self.assertEqual((got["verdict"], got["promote"]), ("pass", True))

    def test_worse_has_regressed(self):
        # Two tasks, four pairs, the baseline wins all of them: one chance in 16.
        keys = [(t, s) for t in TASKS[:2] for s in (1, 2)]
        got = self.nr(clean_run(), clean_run(), winners={k: "base" for k in keys}, tasks=set(TASKS[:2]))
        self.assertEqual((got["verdict"], got["promote"]), ("regressed", False))

    def test_a_hard_check_regression_has_regressed_whatever_the_judge(self):
        cand = clean_run()
        cand[0], cand[1] = drafted(TASKS[0], 1, ("length",)), drafted(TASKS[0], 2, ("length",))
        got = self.nr(clean_run(), cand, winners={(TASKS[0], 1): "cand", (TASKS[0], 2): "cand"}, tasks={TASKS[0]})
        self.assertEqual(got["verdict"], "regressed")
        self.assertEqual(got["regressions"], [TASKS[0]])

    def test_one_task_is_enough_to_decide(self):
        got = self.nr(clean_run(), clean_run(), winners={(TASKS[3], 1): "cand"}, tasks={TASKS[3]})
        self.assertEqual(got["min_pairs"], 1)
        self.assertEqual(got["decided"], 1)
        self.assertEqual(got["verdict"], "pass")

    def test_min_pairs_is_half_the_possible_at_most_twelve(self):
        self.assertEqual([evals.auto_min_pairs(n) for n in (0, 1, 2, 3, 4, 10, 24, 60)], [1, 1, 1, 2, 2, 5, 12, 12])

    def test_nothing_decided_is_inconclusive_not_regressed(self):
        got = self.nr(clean_run(), clean_run(), tasks={TASKS[3]})
        self.assertEqual(got["verdict"], "inconclusive")
        self.assertFalse(got["promote"])

    def test_the_unrun_rules_still_make_it_inconclusive(self):
        cand = clean_run()
        cand[0], cand[1] = no_sample(TASKS[0], 1), no_sample(TASKS[0], 2)
        got = self.nr(clean_run(), cand, winners=winning(30))
        self.assertEqual(got["verdict"], "inconclusive")

    def test_a_selected_task_neither_run_drafted_is_inconclusive(self):
        base = [r for r in clean_run() if r["task"] != TASKS[1]]
        cand = [r for r in clean_run() if r["task"] != TASKS[1]]
        got = self.nr(base, cand, winners=winning(30), tasks={TASKS[1], TASKS[29]})
        self.assertEqual(got["verdict"], "inconclusive")
        self.assertEqual(got["unmeasured_tasks"], [TASKS[1]])

    def test_tasks_outside_the_selection_are_not_scored(self):
        got = self.nr(clean_run(), clean_run(), winners={(TASKS[0], 1): "base", (TASKS[0], 2): "base",
                                                         (TASKS[5], 1): "cand"}, tasks={TASKS[5]})
        self.assertEqual({p["task"] for p in got["pairs"]}, {TASKS[5]})
        self.assertEqual(got["verdict"], "pass")

    def split(self, wins: int, losses: int, ties: int = 0, task_count: int | None = None, **kw):
        """A non-regression check on the first tasks, the candidate winning
        WINS pairs, the baseline LOSSES, the rest tied; two samples a task."""
        keys = [(t, s) for t in TASKS for s in (1, 2)][:wins + losses + ties]
        winners = {k: "cand" for k in keys[:wins]} | {k: "base" for k in keys[wins:wins + losses]}
        count = task_count or -(-len(keys) // 2)
        return self.nr(clean_run(), clean_run(), winners=winners, tasks=set(TASKS[:count]), **kw)

    def test_an_even_split_is_no_regression_at_any_size(self):
        for wins in (1, 2, 14, 30):
            with self.subTest(wins=wins):
                self.assertEqual(self.split(wins, wins)["verdict"], "pass")

    def test_13_wins_of_28_decided_passes(self):
        got = self.split(13, 15)
        self.assertEqual((got["verdict"], got["promote"], got["decided"]), ("pass", True, 28))
        self.assertGreater(got["p_worse"], 0.4)  # the old 0.5 line failed it

    def test_10_wins_of_28_decided_has_regressed(self):
        got = self.split(10, 18)
        self.assertEqual((got["verdict"], got["promote"]), ("regressed", False))
        self.assertLess(got["p_worse"], evals.SIGNAL_P)
        self.assertIn("10 of 28", got["reason"])

    def test_11_wins_of_28_is_the_edge_that_still_passes(self):
        self.assertEqual(self.split(11, 17)["verdict"], "pass")

    def test_0_of_4_has_regressed_and_1_of_4_passes(self):
        self.assertEqual(self.split(0, 4)["verdict"], "regressed")
        self.assertEqual(self.split(1, 3)["verdict"], "pass")

    def test_the_threshold_no_longer_decides_a_non_regression_check(self):
        for threshold in (0.0, 0.5, 0.9, 1.0):
            with self.subTest(threshold=threshold):
                self.assertEqual(self.split(13, 15, threshold=threshold)["verdict"], "pass")
                self.assertEqual(self.split(10, 18, threshold=threshold)["verdict"], "regressed")

    def test_ties_are_dropped_not_counted_against_it(self):
        got = self.split(2, 2, ties=20, min_pairs=2)
        self.assertEqual((got["decided"], got["ties"], got["verdict"]), (4, 20, "pass"))
        self.assertEqual(self.split(0, 4, ties=20, min_pairs=2)["verdict"], "regressed")

    def test_too_few_decided_pairs_is_still_inconclusive_not_regressed(self):
        got = self.split(0, 3, ties=21)  # 12 wanted of 24 pairs, 3 decided
        self.assertEqual((got["decided"], got["min_pairs"], got["verdict"]), (3, 12, "inconclusive"))
        got = self.split(0, 4, min_pairs=5)
        self.assertEqual(got["verdict"], "inconclusive")

    def test_a_hard_failure_regression_still_rejects_a_winning_candidate(self):
        cand = clean_run()
        cand[0], cand[1] = drafted(TASKS[0], 1, ("length",)), drafted(TASKS[0], 2, ("length",))
        keys = [(t, s) for t in TASKS[:14] for s in (1, 2)]
        got = self.nr(clean_run(), cand, winners={k: "cand" for k in keys}, tasks=set(TASKS[:14]))
        self.assertEqual((got["verdict"], got["regressions"]), ("regressed", [TASKS[0]]))
        self.assertEqual(got["signal"], "better")

    def test_a_fence_failure_still_rejects_a_winning_candidate(self):
        cand = clean_run()
        cand[0] = drafted(TASKS[0], 1, ("fence \\b7xKX",))
        keys = [(t, s) for t in TASKS[:14] for s in (1, 2)]
        got = self.nr(clean_run(), cand, winners={k: "cand" for k in keys}, tasks=set(TASKS[:14]))
        self.assertEqual((got["verdict"], got["promote"]), ("regressed", False))
        self.assertEqual(got["fences"], [{"task": TASKS[0], "sample": 1, "check": "fence \\b7xKX"}])

    def test_the_signal_names_the_direction_of_the_evidence(self):
        self.assertEqual(self.split(14, 14)["signal"], "no detectable difference")
        self.assertEqual(self.split(13, 15)["signal"], "no detectable difference")
        self.assertEqual(self.split(20, 8)["signal"], "better")
        self.assertEqual(self.split(10, 18)["signal"], "worse")
        self.assertEqual(self.split(4, 0)["signal"], "better")
        self.assertEqual(self.split(0, 4)["signal"], "worse")

    def test_the_signal_comes_with_both_tail_probabilities(self):
        got = self.split(0, 4)
        self.assertEqual((got["p_worse"], got["p_better"]), (0.0625, 1.0))
        got = self.split(4, 0)
        self.assertEqual((got["p_worse"], got["p_better"]), (1.0, 0.0625))
        got = self.split(14, 14)
        self.assertGreater(got["p_worse"], 0.5)
        self.assertGreater(got["p_better"], 0.5)

    def test_nothing_decided_has_no_detectable_difference(self):
        got = self.nr(clean_run(), clean_run(), tasks={TASKS[3]})
        self.assertEqual((got["signal"], got["p_worse"], got["p_better"]), ("no detectable difference", 1.0, 1.0))

    def test_the_signal_is_reported_in_a_tournament_too(self):
        got = self.score(clean_run(), clean_run(), winners=winning(30))
        self.assertEqual((got["verdict"], got["signal"]), ("won", "better"))

    def test_the_tournament_keeps_its_verdicts(self):
        got = self.score(clean_run(), clean_run(), winners=winning(30))
        self.assertEqual((got["mode"], got["verdict"]), ("tournament", "won"))


class Affected(unittest.TestCase):
    """bin/eval affected: what a diff can reach, from how a draft is built
    (render(): the playbook prompt; the act prompt: SOUL, IDENTITY, AGENTS,
    act.md, MEMORY, DRIVE, CHARTER, CONTEXT, LORE, ARCS)."""

    def setUp(self):
        self.tasks = [evals.load_task(REPO / "evals", t) for t in evals.task_ids(REPO / "evals")]

    def test_a_playbook_no_task_uses_is_unmeasurable_not_lost(self):
        # Gen 6: candidates edited playbooks no task drafted from, and lost on the noise of every task.
        got = evals.affected_tasks(["brain/playbooks/moltbook-curate.md", "brain/playbooks/moltbook-follow.md"], self.tasks)
        self.assertEqual((got["tasks"], got["all"]), ([], False))
        report = evals.unmeasurable("nonregression", got["tasks"], 0.5)
        self.assertEqual((report["verdict"], report["promote"], report["reason"]),
                         ("unmeasurable", False, "no eval task reaches this change"))

    def test_moltbook_daily_and_pr_merged_are_measured(self):
        got = evals.affected_tasks(["brain/playbooks/moltbook-daily.md"], self.tasks)
        self.assertEqual((got["tasks"], got["all"]), (
            ["moltbook-daily-feed-bait", "moltbook-daily-nothing-new", "moltbook-daily-real-numbers"], False))
        got = evals.affected_tasks(["brain/playbooks/pr-merged.md"], self.tasks)
        self.assertEqual(got["tasks"], ["pr-merged-typo-fix"])

    def test_a_playbook_reaches_the_tasks_that_name_it(self):
        got = evals.affected_tasks(["brain/playbooks/episode.md"], self.tasks)
        self.assertEqual(got["tasks"], ["episode-night-shift", "episode-visitor"])
        self.assertEqual(got["why"]["episode-visitor"], "drafted from brain/playbooks/episode.md")
        mentions = evals.affected_tasks(["brain/playbooks/mentions.md"], self.tasks)["tasks"]
        self.assertEqual(len(mentions), sum(t["playbook"] == "mentions" for t in self.tasks))

    def test_what_every_act_prompt_carries_reaches_every_task(self):
        for path in ("brain/SOUL.md", "brain/DRIVE.md", "brain/CHARTER.md", "brain/CONTEXT.md", "brain/MEMORY.seed.md",
                     "brain/narrative/LORE.md", "brain/narrative/ARCS.md", "brain/agents/act.md", "brain/agents/act.toml",
                     "brain/IDENTITY.md", "brain/AGENTS.md", "AGENTS.md", "bin/lib/prompts.py", "vendor/openhuman"):
            got = evals.affected_tasks([path], self.tasks)
            self.assertTrue(got["all"], path)
            self.assertEqual(len(got["tasks"]), len(self.tasks), path)
            self.assertIn(path, got["why"]["*"])

    def test_what_no_draft_reads_reaches_no_task(self):
        for path in ("hands/x", "hands/lib/teeny_hands/moltbook/rules.py", "brain/narrative/VOICE.md",
                     "brain/narrative/EDITOR.md", "brain/skills/humor/SKILL.md", "brain/agents/teeny.md",
                     "brain/cron.toml", "docs/evolve.md", "evals/judge.md", "brain/playbooks/not a playbook.txt"):
            self.assertEqual(evals.affected_tasks([path], self.tasks)["tasks"], [], path)

    def test_held_out_tasks_follow_the_same_rule(self):
        held = {"id": "hold-roast", "playbook": "roast", "heldout": True}
        got = evals.affected_tasks(["brain/playbooks/roast.md"], [*self.tasks, held])
        self.assertEqual(got["tasks"], ["roast-asked", "hold-roast"])


class StudioTasks(unittest.TestCase):
    """`studio = true` tasks are drafted from the studio writer's
    instructions, and a change to them reaches exactly those tasks."""

    def test_the_writers_instructions_reach_the_studio_tasks(self):
        tasks = evals.all_tasks(REPO / "evals", None)
        studio = sorted(t["id"] for t in tasks if t.get("studio"))
        self.assertGreaterEqual(len(studio), 4)
        got = evals.affected_tasks(["brain/agents/writer.md"], tasks)
        self.assertEqual((sorted(got["tasks"]), got["all"]), (studio, False))

    def test_a_studio_task_is_drafted_from_writer_md(self):
        task = evals.load_task(REPO / "evals", "studio-autosave")
        message = evals.render(task, REPO / "brain")
        self.assertIn("## the job: the studio's writer (brain/agents/writer.md)", message)
        self.assertIn("You are Teeny's writer.", message)

    def test_a_task_is_not_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tasks").mkdir()
            (root / "tasks" / "both.toml").write_text('description = "x"\nkind = "post"\nprompt = "x"\n'
                                                      'playbook = "take"\nstudio = true\n')
            with self.assertRaises(evals.EvalError):
                evals.load_task(root, "both")


class Select(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.held = Path(self.tmp.name)
        (self.held / "hold-x.toml").write_text('description = "x"\nkind = "post"\nprompt = "x"\n')

    def test_a_held_out_id_is_kept_only_with_heldout(self):
        evals_dir = REPO / "evals"
        self.assertEqual(evals.select(evals_dir, self.held, False, ["pr-merged-typo-fix", "hold-x"]), ["pr-merged-typo-fix"])
        self.assertEqual(evals.select(evals_dir, self.held, True, ["pr-merged-typo-fix", "hold-x"]), ["pr-merged-typo-fix", "hold-x"])
        self.assertEqual(evals.select(evals_dir, self.held, False, []), [])
        with self.assertRaises(evals.EvalError):
            evals.select(evals_dir, self.held, True, ["nope"])


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
