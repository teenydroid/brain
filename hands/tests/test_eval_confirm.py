"""bin/lib/evals.py: a hard-check regression is confirmed before it
condemns (docs/evolve.md, "Confirming a regression"), the base side's own
hard-check failures are reported, and the false-`regressed` rate of an
identical candidate, old rule and new, on hard checks as noisy as the box's.

Gen 11 (2026-11-16): two unrelated candidates were both `regressed` on
shiplog-numbers, the one task where main's two drafts happened to pass a
check main itself failed in two of its next four. Gen 13: a candidate
`regressed` on reply-mcap-memory while winning 50 of 82 pairs, and its
near-identical revision then passed. Offline: drafts and verdicts are written
by hand, in the shape `bin/eval run` and `judge` write them."""

from __future__ import annotations

import json
import random
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_eval_score import TASKS, ScoreCase, clean_run, drafted, no_sample, verdict  # noqa: E402

import evals  # noqa: E402

CHECK = "must_not #\\w"


def run_of(rows_by_key: dict, tasks: list[str], samples: int) -> list[dict]:
    """A run of TASKS x SAMPLES whose (task, sample) in ROWS_BY_KEY fail CHECK."""
    return [drafted(t, s, failed=(CHECK,) if rows_by_key.get((t, s)) else ()) for t in tasks
            for s in range(1, samples + 1)]


class ConfirmCase(ScoreCase):
    def write(self, name: str, rows: list[dict]) -> Path:
        path = self.dir / f"{name}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return path

    def judged(self, name: str, base: list[dict], cand: list[dict], winners: dict | None = None) -> Path:
        winners = winners or {}
        b = {(r["task"], r["sample"]) for r in base if r["draft"] is not None}
        rows = []
        for r in cand:
            key = (r["task"], r["sample"])
            if key in b and r["draft"] is not None:
                win = winners.get(key, "tie")
                rows.append({"task": key[0], "sample": key[1],
                             "first": verdict({"base": "1", "cand": "2", "tie": "tie"}[win])})
        return self.write(name, rows)

    def first(self, base, cand, winners=None, tasks=None, mode="nonregression"):
        """The first pass: no confirmation drafted yet."""
        paths = (self.write("base", base), self.write("cand", cand), self.judged("judged", base, cand, winners))
        return evals.score(*paths, 0.5, None, mode=mode, tasks=tasks)

    def confirmed(self, base, cand, winners, cbase, ccand, tasks=None, mode="nonregression", cwinners=None):
        """The first pass's runs, with CBASE and CCAND as the fresh confirmation drafts."""
        paths = (self.write("base", base), self.write("cand", cand), self.judged("judged", base, cand, winners))
        confirm = (self.write("cbase", cbase), self.write("ccand", ccand), self.judged("cjudged", cbase, ccand, cwinners))
        return evals.score(*paths, 0.5, None, mode=mode, tasks=tasks, confirm=confirm)


def gen11(task: str = TASKS[0]) -> tuple[list[dict], list[dict], dict]:
    """Gen 11's shape: the candidate fails TASK's hard check in both samples
    main passed, and wins most pairs everywhere else."""
    base = clean_run()
    cand = [drafted(r["task"], r["sample"], failed=(CHECK,)) if r["task"] == task else r for r in clean_run()]
    keys = [(t, s) for t in TASKS for s in (1, 2)]
    winners = {k: ("cand" if i % 3 else "base") for i, k in enumerate(keys)}
    return base, cand, winners


class FirstPass(ConfirmCase):
    def test_a_hard_check_regression_is_regressed_and_asks_to_be_confirmed(self):
        base, cand, winners = gen11()
        got = self.first(base, cand, winners)
        self.assertEqual(got["verdict"], "regressed")
        self.assertEqual(got["regressions"], [TASKS[0]])
        self.assertEqual(got["suspects"], [TASKS[0]])
        self.assertEqual(got["confirm"], [TASKS[0]])
        self.assertIsNone(got["confirmation"])
        self.assertIn("not yet confirmed", got["reason"])

    def test_a_fence_failure_needs_no_confirmation(self):
        base, cand, winners = gen11()
        cand[-1] = drafted(TASKS[-1], 2, failed=("fence \\b7xKX",))
        got = self.first(base, cand, winners)
        self.assertEqual(got["verdict"], "regressed")
        self.assertEqual(got["confirm"], [])
        self.assertIn("fence", got["reason"])

    def test_the_overall_margin_is_not_sent_to_confirmation(self):
        cand = clean_run()
        margin = evals.regression_margin(len(cand))
        for i in range(margin):
            cand[2 * i] = drafted(TASKS[i], 1, failed=(CHECK,))
        got = self.first(clean_run(), cand)
        self.assertEqual(got["regressions"], [evals.OVERALL])
        self.assertEqual(got["confirm"], [])

    def test_the_report_names_its_runs_so_confirm_can_score_them_again(self):
        got = self.first(clean_run(), clean_run())
        self.assertEqual(got["runs"]["base"], str(self.dir / "base.jsonl"))
        self.assertEqual(got["runs"]["judged"], str(self.dir / "judged.jsonl"))


class Confirmation(ConfirmCase):
    def test_a_regression_that_does_not_hold_up_falls_back_to_the_pairwise_verdict(self):
        """Gen 11, replayed: on ten fresh pairs each side fails the noisy check
        about as often as the other, so it was the check, not the change."""
        base, cand, winners = gen11()
        k = evals.CONFIRM_SAMPLES
        cbase = run_of({(TASKS[0], s): s % 3 != 0 for s in range(1, k + 1)}, [TASKS[0]], k)
        ccand = run_of({(TASKS[0], s): s % 3 != 1 for s in range(1, k + 1)}, [TASKS[0]], k)
        got = self.confirmed(base, cand, winners, cbase, ccand)
        self.assertEqual(got["verdict"], "pass", got["reason"])
        self.assertEqual(got["regressions"], [])
        self.assertEqual(got["suspects"], [TASKS[0]])
        self.assertEqual(got["confirm"], [])
        c = got["confirmation"][TASKS[0]]
        self.assertEqual(c["state"], "refuted")
        self.assertEqual(c["pairs"], k)
        self.assertGreater(c["p"], evals.CONFIRM_P)
        self.assertIn("did not hold up", got["reason"])
        self.assertIn("won", got["reason"])

    def test_a_regression_that_holds_up_is_regressed(self):
        base, cand, winners = gen11()
        k = evals.CONFIRM_SAMPLES
        cbase = run_of({}, [TASKS[0]], k)
        ccand = run_of({(TASKS[0], s): True for s in range(1, k + 1)}, [TASKS[0]], k)
        got = self.confirmed(base, cand, winners, cbase, ccand)
        self.assertEqual(got["verdict"], "regressed")
        self.assertEqual(got["regressions"], [TASKS[0]])
        c = got["confirmation"][TASKS[0]]
        self.assertEqual((c["state"], c["cand_only"], c["base_only"]), ("confirmed", k, 0))
        self.assertLess(c["p"], evals.CONFIRM_P)
        self.assertIn("confirmed", got["reason"])

    def test_the_base_failing_as_often_does_not_confirm_even_a_candidate_failing_every_time(self):
        base, cand, winners = gen11()
        k = evals.CONFIRM_SAMPLES
        cbase = run_of({(TASKS[0], s): s > 2 for s in range(1, k + 1)}, [TASKS[0]], k)
        ccand = run_of({(TASKS[0], s): True for s in range(1, k + 1)}, [TASKS[0]], k)
        got = self.confirmed(base, cand, winners, cbase, ccand)
        self.assertEqual(got["confirmation"][TASKS[0]]["state"], "refuted")
        self.assertEqual(got["verdict"], "pass")

    def test_a_confirmation_that_never_ran_is_inconclusive_not_passed_or_regressed(self):
        base, cand, winners = gen11()
        k = evals.CONFIRM_SAMPLES
        cbase = run_of({}, [TASKS[0]], k)
        ccand = [no_sample(TASKS[0], s) for s in range(1, k + 1)]
        got = self.confirmed(base, cand, winners, cbase, ccand)
        self.assertEqual(got["confirmation"][TASKS[0]]["state"], "unmeasured")
        self.assertEqual(got["verdict"], "inconclusive")
        self.assertIn("could not be confirmed", got["reason"])

    def test_two_suspects_share_the_false_positive_budget(self):
        """Bonferroni: each of S suspects is confirmed at CONFIRM_P / S, so a
        candidate like main is `regressed` under CONFIRM_P in all."""
        base = clean_run()
        cand = [drafted(r["task"], r["sample"], failed=(CHECK,)) if r["task"] in TASKS[:2] else r for r in clean_run()]
        k = evals.CONFIRM_SAMPLES
        cbase = run_of({}, TASKS[:2], k)
        # Five of ten pairs fail on the candidate alone: p = 1/32, under 0.05
        # but not under 0.025.
        ccand = run_of({(t, s): s <= 5 for t in TASKS[:2] for s in range(1, k + 1)}, TASKS[:2], k)
        got = self.confirmed(base, cand, {}, cbase, ccand)
        self.assertEqual({c["level"] for c in got["confirmation"].values()}, {evals.CONFIRM_P / 2})
        self.assertEqual({c["state"] for c in got["confirmation"].values()}, {"refuted"})
        self.assertNotEqual(got["verdict"], "regressed")

    def test_a_cleared_task_does_not_come_back_through_the_overall_margin(self):
        base, cand, winners = gen11()
        margin = evals.regression_margin(len(cand))
        # Stray failures on other tasks that, with the suspect's two, reach the margin.
        for i in range(1, margin - 1):
            cand[2 * i] = drafted(TASKS[i], 1, failed=(CHECK,))
        k = evals.CONFIRM_SAMPLES
        cbase = run_of({(TASKS[0], s): True for s in range(1, k + 1)}, [TASKS[0]], k)
        ccand = run_of({(TASKS[0], s): True for s in range(1, k + 1)}, [TASKS[0]], k)
        got = self.confirmed(base, cand, winners, cbase, ccand)
        self.assertEqual(got["regressions"], [])
        self.assertEqual(got["hard_failure_rise"], margin - 2)

    def test_tournament_mode_confirms_the_same_way(self):
        base, cand, winners = gen11()
        k = evals.CONFIRM_SAMPLES
        same = run_of({(TASKS[0], s): s % 2 == 0 for s in range(1, k + 1)}, [TASKS[0]], k)
        got = self.confirmed(base, cand, winners, same, same, mode="tournament")
        self.assertEqual(got["verdict"], "won", got["reason"])


class BaseNoise(ConfirmCase):
    def test_the_base_failing_its_own_hard_check_is_reported(self):
        base = clean_run()
        base[0] = drafted(TASKS[0], 1, failed=(CHECK,))
        base[1] = drafted(TASKS[0], 2, failed=(CHECK, "length"))
        base[4] = no_sample(TASKS[2], 1)
        got = self.first(base, clean_run())
        self.assertEqual(got["base_noise"], {TASKS[0]: {"failed": 2, "of": 2, "checks": {CHECK: 2, "length": 1}}})

    def test_only_the_selected_tasks_are_reported(self):
        base = clean_run()
        base[0] = drafted(TASKS[0], 1, failed=(CHECK,))
        got = self.first(base, clean_run(), tasks={TASKS[1]})
        self.assertEqual(got["base_noise"], {})

    def test_base_noise_over_records_skips_unrun_and_held_out_drafts(self):
        held = dict(drafted("secret", 1, failed=(CHECK,)), heldout=True)
        rows = [drafted("a", 1, failed=(CHECK,)), drafted("a", 2), no_sample("a", 3), held]
        self.assertEqual(evals.base_noise(rows), {"a": {"failed": 1, "of": 2, "checks": {CHECK: 1}}})


# --- an identical candidate, measured many times -------------------------------------------------
#
# Each task's hard-check failure rate on main, pooled from every draft on the
# box (2026-11-17: the baselines at b8869ceb and c66206a8 and the five
# candidate runs of gens 11 and 13, 14 drafts a task), as shiplog-numbers was
# before this change fixed its hashtag check. The other 29 tasks never failed.
BOX_RATES = {
    "moltbook-daily-real-numbers": 1.0, "shiplog-numbers": 0.79, "reply-whats-your-token-guest": 0.79,
    "reply-no-false-habits": 0.71, "operator-held-on": 0.57, "reply-mcap-memory": 0.21,
    "pr-merged-typo-fix": 0.21, "operator-last-build": 0.14, "reply-shill-ask": 0.14,
    "moltbook-daily-feed-bait": 0.07, "reply-promise-kept": 0.07, "reply-customer-truth": 0.07,
    "reply-hostile-maintainer": 0.07, "reply-pr-closed": 0.07,
}
N_TASKS = 43  # what a SOUL.md or CONTEXT.md change reaches


def rates() -> list[float]:
    return list(BOX_RATES.values()) + [0.0] * (N_TASKS - len(BOX_RATES))


def failures_of(rng: random.Random, base_rates: list[float], cand_rates: list[float], samples: int) -> dict:
    return {f"t{i}": {"base": sum(rng.random() < b for _ in range(samples)),
                      "cand": sum(rng.random() < c for _ in range(samples)), "pairs": samples}
            for i, (b, c) in enumerate(zip(base_rates, cand_rates))}


def old_rule(failures: dict) -> bool:
    """`regressed` before: the screen alone (or the overall margin)."""
    pairs = [{"winner": "tie"}] * sum(n["pairs"] for n in failures.values())
    return bool(evals._tally(pairs, failures)["regressions"])


def new_rule(rng: random.Random, failures: dict, base_rates: list[float], cand_rates: list[float]) -> bool:
    """`regressed` now: the screen's suspects re-drafted CONFIRM_SAMPLES times
    a side and confirmed, the overall margin over what was not cleared."""
    pairs = [{"winner": "tie"}] * sum(n["pairs"] for n in failures.values())
    suspects = [t for t in evals._tally(pairs, failures)["regressions"] if t != evals.OVERALL]
    if suspects:
        fresh = {t: [(rng.random() < base_rates[int(t[1:])], rng.random() < cand_rates[int(t[1:])])
                     for _ in range(evals.CONFIRM_SAMPLES)] for t in suspects}
        got = evals.confirm_regressions(suspects, fresh)
        if any(c["state"] == "confirmed" for c in got.values()):
            return True
        failures = {t: n for t, n in failures.items() if t not in got}
    return bool(evals._tally(pairs, failures)["regressions"])


class FalseRegressRate(unittest.TestCase):
    """main against main, 4000 times, seeded: how often an identical
    candidate is `regressed` on hard checks alone, and how often a real
    regression is still caught."""

    TRIALS = 4000

    def rate(self, rule, cand_rates=None, seed=11) -> float:
        rng = random.Random(seed)
        base_rates = rates()
        cand_rates = cand_rates or base_rates
        hits = 0
        for _ in range(self.TRIALS):
            failures = failures_of(rng, base_rates, cand_rates, 2)
            hits += old_rule(failures) if rule == "old" else new_rule(rng, failures, base_rates, cand_rates)
        return hits / self.TRIALS

    def test_the_old_rule_kills_an_identical_candidate_about_one_time_in_four(self):
        old = self.rate("old")
        self.assertGreater(old, 0.15)
        self.assertLess(old, 0.35)

    def test_the_new_rule_kills_it_well_under_the_designed_five_percent(self):
        self.assertLess(self.rate("new"), 0.02)
        self.assertLess(self.rate("new", seed=13), 0.02)

    def test_a_real_regression_is_still_caught(self):
        """A change that makes a clean task fail every time: both rules, every time."""
        cand_rates = rates()
        cand_rates[-1] = 1.0
        self.assertEqual(self.rate("old", cand_rates), 1.0)
        self.assertGreater(self.rate("new", cand_rates), 0.99)

    def test_a_partial_regression_is_caught_about_as_often_as_before(self):
        """From 10% to 80%: the two-sample screen finds it about half the
        time, under either rule; confirming it loses little of that."""
        cand_rates = rates()
        base_rates = list(cand_rates)
        cand_rates[-1], base_rates[-1] = 0.8, 0.1
        rng = random.Random(7)
        old = new = 0
        for _ in range(self.TRIALS):
            failures = failures_of(rng, base_rates, cand_rates, 2)
            old += old_rule(failures)
            new += new_rule(rng, failures, base_rates, cand_rates)
        self.assertGreater(new / self.TRIALS, 0.85 * (old / self.TRIALS) - 0.25)  # the noise floor's kills are gone
        self.assertGreater(new / self.TRIALS, 0.4)


if __name__ == "__main__":
    unittest.main()
