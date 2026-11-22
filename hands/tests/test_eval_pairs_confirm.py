"""bin/lib/evals.py: a non-regression check the judge's pairs call worse is
confirmed on a fresh, independent sample before it condemns (docs/evolve.md,
"Confirming a regression"), pairs are shown in an order balanced within each
task, and the report says which path (fence, hard, pairs) decided.

The first `bin/evolve aa` on the box (2026-11-17, main against itself, 46
tasks at two samples) came back `regressed`: main won 48 of 82 decided pairs
against itself, which two alike sides do 7.5% of the time, under the 10% the
sign test allowed. By design that path alone marked about one identical
candidate in ten `regressed`, and `regressed` settles a learning."""

from __future__ import annotations

import json
import random
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_eval_confirm import CHECK, ConfirmCase, failures_of, new_rule, rates, run_of  # noqa: E402
from test_eval_score import TASKS, clean_run, drafted, verdict  # noqa: E402

import evals  # noqa: E402


def losing(share_cand: float, tasks=TASKS, samples=2) -> dict:
    """Winners for every (task, sample): the candidate wins SHARE_CAND of them, spread evenly."""
    keys = [(t, s) for t in tasks for s in range(1, samples + 1)]
    wins = round(share_cand * len(keys))
    step = len(keys) / wins if wins else 0
    picked = {int(i * step) for i in range(wins)}
    return {k: ("cand" if i in picked else "base") for i, k in enumerate(keys)}


class PairsCase(ConfirmCase):
    def pairs_confirmed(self, winners, fresh_winners, mode="nonregression", base=None, cand=None):
        base, cand = base or clean_run(), cand or clean_run()
        paths = (self.write("base", base), self.write("cand", cand), self.judged("judged", base, cand, winners))
        fresh = (self.write("fbase", clean_run()), self.write("fcand", clean_run()),
                 self.judged("fjudged", clean_run(), clean_run(), fresh_winners))
        return evals.score(*paths, 0.5, None, mode=mode, confirm_pairs=fresh)


class FirstPass(PairsCase):
    def test_pairs_that_say_worse_are_regressed_and_ask_to_be_confirmed(self):
        got = self.first(clean_run(), clean_run(), losing(0.3))
        self.assertEqual(got["verdict"], "regressed")
        self.assertTrue(got["confirm_pairs"])
        self.assertEqual(got["path"], "pairs")
        self.assertIsNone(got["pairs_confirmation"])
        self.assertIn("not yet confirmed", got["reason"])

    def test_pairs_that_say_nothing_ask_for_nothing(self):
        got = self.first(clean_run(), clean_run(), losing(0.5))
        self.assertEqual((got["verdict"], got["confirm_pairs"], got["path"]), ("pass", False, None))

    def test_a_hard_check_regression_is_the_hard_path_and_confirmed_first(self):
        cand = [drafted(r["task"], r["sample"], failed=(CHECK,)) if r["task"] == TASKS[0] else r for r in clean_run()]
        got = self.first(clean_run(), cand, losing(0.3))
        self.assertEqual((got["path"], got["confirm"], got["confirm_pairs"]), ("hard", [TASKS[0]], False))

    def test_a_fence_is_the_fence_path_and_nothing_is_confirmed(self):
        cand = clean_run()
        cand[-1] = drafted(TASKS[-1], 2, failed=("fence \\b7xKX",))
        got = self.first(clean_run(), cand, losing(0.3))
        self.assertEqual((got["path"], got["confirm"], got["confirm_pairs"]), ("fence", [], False))

    def test_a_tournament_is_not_confirmed(self):
        got = self.first(clean_run(), clean_run(), losing(0.3), mode="tournament")
        self.assertEqual((got["verdict"], got["confirm_pairs"], got["path"]), ("lost", False, "pairs"))


class Confirmation(PairsCase):
    def test_a_fresh_sample_that_is_worse_too_is_regressed(self):
        got = self.pairs_confirmed(losing(0.3), losing(0.3))
        self.assertEqual((got["verdict"], got["path"]), ("regressed", "pairs"))
        c = got["pairs_confirmation"]
        self.assertEqual(c["state"], "confirmed")
        self.assertLess(c["p_worse"], evals.PAIRS_CONFIRM_P)
        self.assertFalse(got["confirm_pairs"])
        self.assertIn("confirmed on a fresh sample", got["reason"])

    def test_a_fresh_sample_that_is_not_worse_falls_back_to_pass(self):
        """The A/A run on the box, replayed: worse by chance the first time
        (here 23 of 60), an even split the second."""
        got = self.pairs_confirmed(losing(23 / 60), losing(0.5))
        self.assertEqual((got["verdict"], got["path"], got["promote"]), ("pass", None, True), got["reason"])
        self.assertEqual(got["pairs_confirmation"]["state"], "refuted")
        self.assertIn("did not hold up", got["reason"])

    def test_a_fresh_sample_that_decides_nothing_is_inconclusive(self):
        got = self.pairs_confirmed(losing(0.3), {})
        self.assertEqual(got["pairs_confirmation"]["state"], "unmeasured")
        self.assertEqual(got["verdict"], "inconclusive")

    def test_the_report_carries_both_confirmations_forward(self):
        """A hard-check suspect refuted, then the pairs: rescore keeps the first."""
        cand = [drafted(r["task"], r["sample"], failed=(CHECK,)) if r["task"] == TASKS[0] else r for r in clean_run()]
        winners = losing(0.3)
        paths = (self.write("base", clean_run()), self.write("cand", cand), self.judged("judged", clean_run(), cand, winners))
        k = evals.CONFIRM_SAMPLES
        same = run_of({(TASKS[0], s): s % 2 == 0 for s in range(1, k + 1)}, [TASKS[0]], k)
        hard = (self.write("hb", same), self.write("hc", same), self.judged("hj", same, same))
        report = self.dir / "report.json"
        report.write_text(json.dumps(evals.score(*paths, 0.5, None, mode="nonregression")))
        after_hard = evals.rescore(report, confirm=hard)
        self.assertEqual(after_hard["confirmation"][TASKS[0]]["state"], "refuted")
        self.assertTrue(after_hard["confirm_pairs"])
        report.write_text(json.dumps(after_hard))
        fresh = (self.write("fb", clean_run()), self.write("fc", clean_run()),
                 self.judged("fj", clean_run(), clean_run(), losing(0.5)))
        final = evals.rescore(report, confirm_pairs=fresh)
        self.assertEqual(final["confirmation"][TASKS[0]]["state"], "refuted")
        self.assertEqual(final["pairs_confirmation"]["state"], "refuted")
        self.assertEqual(final["verdict"], "pass")


class BalancedOrder(unittest.TestCase):
    """The judge picks draft 2 in 55.7% of the 483 decided pairs on the box
    (gens 11, 13 and the A/A run; p = 0.014). Shown each way equally often
    within a task, that favours neither side; by the pair's place in the
    list, one dropped pair turned every later task's order round."""

    def shown(self, keys):
        pairs = [{"task": t, "sample": s} for t, s in keys]
        return {(p["task"], p["sample"]): way for p, way in zip(pairs, evals.pair_orders(pairs, False))}

    def test_each_task_is_shown_each_way_equally_whatever_was_dropped(self):
        keys = [(t, s) for t in TASKS[:6] for s in (1, 2)]
        del keys[3]  # one side of task-01's sample 2 never drafted
        shown = self.shown(keys)
        for t in TASKS[:6]:
            if t == TASKS[1]:
                continue
            self.assertEqual(sorted(shown[(t, s)] for s in (1, 2)), [("first",), ("second",)], t)

    def test_an_odd_sample_count_alternates_across_tasks(self):
        shown = self.shown([(t, 1) for t in TASKS[:10]])
        self.assertEqual(sum(v == ("first",) for v in shown.values()), 5)

    def test_both_orders_is_both(self):
        self.assertEqual(evals.pair_orders([{"task": "a", "sample": 1}], True), [("first", "second")])


# --- an identical candidate, measured many times, both paths -------------------------------------

PAIRS = 2 * 46  # every task at two samples: what the A/A run judged
TIE = 0.11  # 10 ties in 92 pairs there


def judged_pairs(rng: random.Random, n: int, q: float) -> tuple[int, int]:
    """Wins and decided of N pairs the candidate wins with chance Q when decided."""
    wins = decided = 0
    for _ in range(n):
        if rng.random() < TIE:
            continue
        decided += 1
        wins += rng.random() < q
    return wins, decided


def pairs_old(wins: int, decided: int) -> bool:
    return evals.binomial_tails(wins, decided)[0] < evals.SIGNAL_P


def pairs_new(rng: random.Random, wins: int, decided: int, q: float) -> bool:
    if not pairs_old(wins, decided):
        return False
    return evals.confirm_worse(*judged_pairs(rng, PAIRS, q))["state"] == "confirmed"


class FalseRegressRate(unittest.TestCase):
    TRIALS = 4000

    def rates_of(self, q: float, seed: int = 17) -> tuple[float, float]:
        """(old, new): how often a candidate winning a decided pair with
        chance Q is `regressed`, hard checks (the box's rates, the candidate
        no different) and pairs together."""
        rng = random.Random(seed)
        base = rates()
        old = new = 0
        for _ in range(self.TRIALS):
            failures = failures_of(rng, base, base, 2)
            wins, decided = judged_pairs(rng, PAIRS, q)
            pairs = [{"winner": "tie"}] * sum(n["pairs"] for n in failures.values())
            old += bool(evals._tally(pairs, failures)["regressions"]) or pairs_old(wins, decided)
            new += new_rule(rng, failures, base, base) or pairs_new(rng, wins, decided, q)
        return old / self.TRIALS, new / self.TRIALS

    def test_an_identical_candidate_is_regressed_under_five_percent_counting_both_paths(self):
        old, new = self.rates_of(0.5)
        self.assertGreater(old, 0.25)  # the hard screen's ~23% and the pairs' ~8%
        self.assertLess(new, 0.03)
        self.assertLess(self.rates_of(0.5, seed=19)[1], 0.03)

    def test_a_candidate_losing_60_40_is_still_caught_more_often_than_not(self):
        self.assertGreater(self.rates_of(0.4)[1], 0.5)

    def test_a_candidate_losing_70_30_is_caught_almost_always(self):
        self.assertGreater(self.rates_of(0.3)[1], 0.97)

    def test_the_designed_bound_is_the_screen_times_the_confirmation(self):
        self.assertLessEqual(round(evals.SIGNAL_P * evals.PAIRS_CONFIRM_P, 6), 0.02)


if __name__ == "__main__":
    unittest.main()
