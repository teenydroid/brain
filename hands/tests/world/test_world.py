"""hands/world: the J arithmetic and its daily caps, a snapshot from every
source (stats, stimuli, a fake core and chain, the build and guard tables,
ARCS.md, the hands on disk), sources that are missing or failing, and one
row per component per UTC day."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

from wallet.support import CAPS, DAY, OWN, T0, TINY, USDC_DEVNET, Scratch, token_accounts
from teeny_hands import hand, stimuli
from teeny_hands.build import jobs
from teeny_hands.guard import holds
from teeny_hands.stats import collect as stats
from teeny_hands.world import collect, score

WORLD = Path(__file__).resolve().parents[2] / "world"
TODAY = "2026-09-14"
YESTERDAY = "2026-09-13"

ARCS = """# arcs

## run the lab with no human in the loop

- status: started.
- so far: cron fires on its own.

## find out what the terminal is typing

- status: not started.

## mint my own currency

- status: done, minted on devnet.

## take $TINY to one dollar

- status: ongoing, in the voice.
"""


def chain_tokens(params):
    mint = params[1]["mint"]
    if mint == TINY:
        return token_accounts(50_000 * 10**6, mint=TINY)
    return token_accounts(1000 * 10**6, mint=USDC_DEVNET)


class ScoreMathTest(Scratch):
    def test_curve_is_log_above_zero_and_mirrored_below(self):
        self.assertAlmostEqual(score.curve(90, 10), math.log(100))
        self.assertAlmostEqual(score.curve(0, 10), math.log(10))
        # mirrored: a loss of 90 counts as much as a gain of 90
        self.assertAlmostEqual(score.curve(-90, 10), 2 * math.log(10) - math.log(100))
        self.assertAlmostEqual(score.curve(90, 10) - score.curve(0, 10), score.curve(0, 10) - score.curve(-90, 10))

    def test_credit_is_the_log_change_when_under_the_cap(self):
        comp = score.BY_NAME["attention"]  # c 50, cap 0.10 a day
        got = score.credit([("2026-09-07", 950.0), ("2026-09-08", 1000.0), ("2026-09-09", 1050.0)], comp)
        self.assertAlmostEqual(got["delta_log"], math.log(1100 / 1000))
        self.assertAlmostEqual(got["credit"], math.log(1100 / 1000))
        self.assertEqual((got["capped_days"], got["steps"]), (0, 2))

    def test_a_spike_is_capped_each_day_both_ways(self):
        comp = score.BY_NAME["attention"]
        got = score.credit([("2026-09-07", 50.0), ("2026-09-08", 950.0), ("2026-09-09", 950.0)], comp)
        self.assertAlmostEqual(got["delta_log"], math.log(1000 / 100))
        self.assertAlmostEqual(got["credit"], 0.10)
        self.assertEqual(got["capped_days"], 1)
        down = score.credit([("2026-09-07", 950.0), ("2026-09-08", 50.0)], comp)
        self.assertAlmostEqual(down["credit"], -0.10)

    def test_a_gap_of_days_widens_the_cap_by_the_days(self):
        comp = score.BY_NAME["attention"]
        got = score.credit([("2026-09-07", 50.0), ("2026-09-10", 950.0)], comp)
        self.assertAlmostEqual(got["credit"], 0.30)

    def test_one_row_has_no_growth(self):
        got = score.credit([("2026-09-07", 50.0)], score.BY_NAME["tiny"])
        self.assertEqual((got["credit"], got["delta_log"], got["steps"]), (0.0, 0.0, 0))

    def test_j_weights_credits_and_subtracts_the_penalty(self):
        credits = {"attention": 0.1, "tiny": 0.2, "money": 0.0, "capability": 0.05, "shipped": 0.0, "world": 0.1}
        expected = 1.0 * 0.1 + 0.8 * 0.2 + 0.5 * 0.05 + 0.2 * 0.1 - 0.02 * 3
        self.assertAlmostEqual(score.j_score(credits, 3), expected)

    def test_weights_rank_attention_first_and_the_world_last(self):
        weights = [c.weight for c in score.COMPONENTS]
        self.assertEqual([c.name for c in score.COMPONENTS], ["attention", "tiny", "money", "capability", "shipped", "world"])
        self.assertEqual(weights, sorted(weights, reverse=True))
        self.assertEqual(len(set(weights)), len(weights))


class ReportTest(Scratch):
    def put(self, date, component, value):
        conn = collect.db()
        conn.execute("INSERT OR REPLACE INTO world_scores (date, component, value, detail, at) VALUES (?, ?, ?, '{}', 0)", (date, component, value))
        conn.close()

    def test_report_with_no_snapshot_says_to_quote_nothing(self):
        out = collect.report(7)
        self.assertIsNone(out["J"])
        self.assertIn("Quote no", out["reading"][0])

    def test_report_computes_j_from_the_rows(self):
        for day, att in (("2026-09-07", 950), ("2026-09-10", 1000), ("2026-09-14", 1050)):
            self.put(day, "attention", att)
        for day, tiny_held in (("2026-09-07", 0), ("2026-09-13", 0), ("2026-09-14", 40_000)):
            self.put(day, "tiny", tiny_held)
        self.put("2026-09-07", "world", 2)
        self.put("2026-09-14", "world", 3)
        self.put("2026-09-08", "penalty", 2)
        self.put("2026-09-14", "penalty", 1)
        self.put("2026-09-07", "penalty", 9)  # the baseline day: before the window
        out = collect.report(7)
        self.assertEqual((out["as_of"], out["since"]), ("2026-09-14", "2026-09-07"))
        by = {c["component"]: c for c in out["components"]}
        attention = math.log(1100 / 1000)
        tiny_credit = 0.20  # log(50000/10000) = 1.6 on one day, capped at 0.20
        world = math.log(5 / 4)  # one 7-day step: under its cap of 7 * 0.10
        self.assertAlmostEqual(by["attention"]["credit"], round(attention, 4))
        self.assertEqual(by["tiny"]["capped_days"], 1)
        self.assertAlmostEqual(by["tiny"]["credit"], tiny_credit)
        self.assertAlmostEqual(by["world"]["credit"], round(world, 4))
        self.assertEqual(by["money"]["value"], None)
        self.assertEqual(out["penalty"]["events"], 3)
        expected = 1.0 * attention + 0.8 * tiny_credit + 0.2 * world - 0.02 * 3
        self.assertAlmostEqual(out["J"], round(expected, 4), places=4)
        self.assertTrue(any(line.startswith("J over 7 days") for line in out["reading"]))
        self.assertIn("price", out["rules"])

    def test_a_component_missing_today_keeps_its_last_value_and_says_so(self):
        self.put("2026-09-12", "attention", 100)
        self.put("2026-09-13", "attention", 110)
        self.put("2026-09-14", "tiny", 5)
        out = collect.report(7)
        by = {c["component"]: c for c in out["components"]}
        self.assertEqual(by["attention"]["value"], 110)
        self.assertIn("no row on 2026-09-14", by["attention"]["note"])
        self.assertIn("one row", by["tiny"]["note"])

    def test_history_groups_rows_by_day(self):
        self.put("2026-09-01", "attention", 1)
        self.put("2026-09-13", "attention", 2)
        self.put("2026-09-14", "attention", 3)
        self.put("2026-09-14", "tiny", 4)
        out = collect.history(7)
        self.assertEqual([d["date"] for d in out["rows"]], ["2026-09-13", "2026-09-14"])
        self.assertEqual(out["rows"][1]["values"], {"attention": 3, "tiny": 4})


class SnapshotTest(Scratch):
    settings = {"GITHUB_TOKEN": "ghp_FAKE", "WORLD_TEAM_USD": "2500"}

    def setUp(self):
        super().setUp()
        self.chain.answers["getTokenAccountsByOwner"] = chain_tokens
        self.core.answers["openhuman.cost_report"] = {"totals": {"calls": 3, "cost_usd": 12.345}}
        self.core.answers["openhuman.x402_list_payments"] = {"payments": []}

    def seed_everything(self):
        conn = stats.db()
        for date, source, metric, value in (
            ("2026-09-13", "x", "followers", 120),
            ("2026-09-14", "moltbook", "followers", 7),
            ("2026-09-14", "moltbook", "karma", 42),
            ("2026-09-14", "github", "followers", 9),
            ("2026-09-14", "github", "stars", 11),
            ("2026-09-15", "x", "followers", 9999),  # after today: never read
        ):
            conn.execute("INSERT INTO stats VALUES (?, ?, ?, ?, 0)", (date, source, metric, value))
        conn.close()
        for i, (who, at) in enumerate((("alice", T0 - DAY), ("ALICE", T0 - 2 * DAY), ("bob", T0 - 3 * DAY), ("carol", T0 - 9 * DAY), ("dave", T0))):
            stimuli.add("x", "mention", f"m{i}", "hi", who=who, at=at)
        stimuli.add("builder", "report", "b1", "built", at=T0 - DAY)  # not a person
        conn = jobs.db()
        for i, status in enumerate(("shipped", "shipped", "failed")):
            conn.execute(
                "INSERT INTO build_jobs (kind, slug, repo, text, workdir, status, claimed_at) VALUES ('daily', ?, 'r', 't', 'w', ?, 0)",
                (f"s{i}", status),
            )
        conn.close()
        with holds._store() as conn:
            for direction, status, at in (
                ("outbound", "pending", "2026-09-13T10:00:00.000Z"),
                ("outbound", "rejected", "2026-09-13T11:00:00.000Z"),
                ("outbound", "approved", "2026-09-13T12:00:00.000Z"),
                ("inbound", "pending", "2026-09-13T12:00:00.000Z"),
                ("outbound", "pending", "2026-09-12T12:00:00.000Z"),
            ):
                conn.execute(
                    "INSERT INTO guard_holds (at, direction, kind, context, category, reason, held_by, text, status)"
                    " VALUES (?, ?, 'draft', '{}', 'c', 'r', 'screen', 't', ?)",
                    (at, direction, status),
                )
        (self.root / "brain" / "narrative").mkdir(parents=True)
        (self.root / "brain" / "narrative" / "ARCS.md").write_text(ARCS)
        hands_dir = self.root / "hands"
        (hands_dir / "tests" / "alpha").mkdir(parents=True)
        (hands_dir / "tests" / "alpha" / "test_alpha.py").write_text("")
        (hands_dir / "tests" / "test_beta.py").write_text("")
        (hands_dir / "tests" / "media").mkdir()
        (hands_dir / "tests" / "media" / "test_media.py").write_text("")
        for name in ("alpha", "beta", "image", "untested"):
            (hands_dir / name).write_text("#!/bin/sh\n")
            (hands_dir / name).chmod(0o755)

    def rows(self):
        conn = collect.db()
        try:
            return {(r["date"], r["component"]): r["value"] for r in conn.execute("SELECT * FROM world_scores")}
        finally:
            conn.close()

    def test_a_snapshot_reads_every_source(self):
        self.seed_everything()
        out = collect.snapshot()
        self.assertEqual(out["date"], TODAY)
        self.assertNotIn("failed", out)
        self.assertNotIn("skipped", out)
        w = out["written"]
        # version 2: X 120 and GitHub 9 followers are unknown (0.5 each), Moltbook 7 followers and
        # 42 karma are agents (0.1 each), and alice and bob (carol is too old, dave is today) are
        # unknown X accounts that came first (2.5 x 2 each)
        self.assertEqual(w["attention"], round(60 + 4.5 + 0.7 + 4.2 + 10, 6))
        self.assertEqual(w["tiny"], 50_000)
        # 1000 USDC + 10 SOL at 200 - 2500 from the team - 12.35 of costs
        self.assertEqual(w["money"], 487.65)
        # alpha, beta, image (its tests are media's); github and the wallet
        self.assertEqual(w["capability"], 5)
        # 2 shipped jobs + 11 stars
        self.assertEqual(w["shipped"], 13)
        # started 1, not started 0, done 2, ongoing 1
        self.assertEqual(w["world"], 4)
        # yesterday's own holds, not approved: pending and rejected
        self.assertEqual(w["penalty"], 2)
        self.assertEqual(self.moved(), [])
        report = collect.report(7)
        attention = {c["component"]: c for c in report["components"]}["attention"]
        self.assertEqual(attention["value"], 79.4)
        self.assertEqual(attention["version"], 2)
        self.assertEqual(attention["split"]["unknown"]["accounts"], 2)
        # version 1's unweighted sum rides along: 120 + 7 + 42 + 9 + alice and bob
        conn = collect.db()
        detail = json.loads(conn.execute("SELECT detail FROM world_scores WHERE component = 'attention'").fetchone()["detail"])
        conn.close()
        self.assertEqual(detail["v1_value"], 180)
        self.assertTrue(any(line.startswith("attention reach: human 0") for line in report["reading"]))

    def test_a_second_snapshot_the_same_day_replaces_its_rows(self):
        self.seed_everything()
        collect.snapshot()
        conn = stats.db()
        conn.execute("UPDATE stats SET value = 200 WHERE source = 'x' AND metric = 'followers' AND date = '2026-09-13'")
        conn.close()
        self.at(T0 + 3600)
        collect.snapshot()
        rows = self.rows()
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[(TODAY, "attention")], round(79.4 + 40, 6))
        self.at(T0 + DAY)
        collect.snapshot()
        self.assertEqual(len(self.rows()), 14)

    def test_missing_sources_are_skipped_and_the_rest_land(self):
        # an empty root: no stats, no stimuli, no ARCS.md, no hands; and no core
        os.environ["OPENHUMAN_CORE_URL"] = "http://127.0.0.1:9"
        self.configure(GITHUB_TOKEN=None, **{k: None for k in CAPS})
        out = collect.snapshot()
        self.assertEqual(set(out["failed"]), {"tiny", "money"})
        self.assertIn("unreachable", out["failed"]["tiny"])
        self.assertEqual(set(out["skipped"]), {"attention", "capability", "shipped", "world"})
        self.assertIn("ARCS.md", out["skipped"]["world"])
        self.assertEqual(out["written"], {"penalty": 0})
        self.assertEqual(self.rows(), {(TODAY, "penalty"): 0})

    def test_with_no_wallet_yet_the_wallet_parts_are_skipped_and_the_rest_land(self):
        self.seed_everything()
        self.core.answers["openhuman.wallet_status"] = {"configured": False, "accounts": []}
        out = collect.snapshot()
        self.assertNotIn("failed", out)
        self.assertEqual(set(out["skipped"]), {"tiny", "money"})
        for name in ("tiny", "money"):
            self.assertEqual(out["skipped"][name], "no wallet yet (the operator runs bin/wallet-setup once)")
        self.assertEqual(set(out["written"]), {"attention", "capability", "shipped", "world", "penalty"})
        self.assertEqual(out["written"]["shipped"], 13)
        self.assertEqual({c for _, c in self.rows()}, set(out["written"]))
        self.assertEqual(self.moved(), [])

    def test_a_partial_source_still_counts_and_says_what_is_missing(self):
        stimuli.add("moltbook", "comment", "c1", "hi", who="eve", at=T0 - DAY)
        out = collect.snapshot()
        self.assertEqual(out["written"]["attention"], 0.5)  # one Moltbook agent; no ledger says who came first
        conn = collect.db()
        detail = json.loads(conn.execute("SELECT detail FROM world_scores WHERE component = 'attention'").fetchone()["detail"])
        conn.close()
        self.assertIn("no stats table", detail["notes"][0])

    def test_the_cli_answers_one_json_line(self):
        self.seed_everything()
        env = dict(os.environ)
        for args in (["snapshot"], ["report", "--days", "3"], ["history"]):
            done = subprocess.run([sys.executable, str(WORLD), *args], capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertTrue(json.loads(done.stdout)["ok"])
        bad = subprocess.run([sys.executable, str(WORLD), "report", "--days", "0"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(bad.returncode, 1)


class PeopleInAttentionTest(Scratch):
    """P2: attention counts people. Human and agent reach are split, the weights
    are pinned, a week of Moltbook karma alone moves attention less than a week
    of human replies, and a change of version is never read as growth."""

    def test_the_weights_rank_people_over_unknown_over_agents(self):
        for table in (score.FOLLOWER_WEIGHTS, score.ENGAGED_WEIGHTS):
            self.assertEqual(set(table), set(score.REACH_CLASSES))
            self.assertGreater(table["human"], table["unknown"])
            self.assertGreater(table["unknown"], table["agent"])
        self.assertEqual(score.FOLLOWER_WEIGHTS, {"human": 1.0, "unknown": 0.5, "agent": 0.1})
        self.assertEqual(score.ENGAGED_WEIGHTS, {"human": 5.0, "unknown": 2.5, "agent": 0.5})
        self.assertEqual(score.FIRST_CONTACT_FACTOR, 2.0)
        # a person who wrote first counts most of all
        top = score.engaged_weight("human", True)
        self.assertEqual(top, 10.0)
        self.assertEqual(top, max(score.engaged_weight(k, t) for k in score.REACH_CLASSES for t in (True, False)))
        self.assertEqual(score.STAT_CLASSES["moltbook.karma"], "agent")
        self.assertEqual(set(score.CLASS_RULES), set(collect.ENGAGED_SURFACES))

    def test_classification_is_a_rule_and_says_unknown_rather_than_guess(self):
        self.assertEqual(collect.classify("moltbook", "eve"), "agent")
        self.assertEqual(collect.classify("telegram", "@ann"), "human")
        self.assertEqual(collect.classify("gh", "dependabot[bot]"), "agent")
        self.assertEqual(collect.classify("gh", "octocat"), "unknown")
        self.assertEqual(collect.classify("x", "definitely_a_human"), "unknown")
        self.assertEqual(collect.classify("x", "gpt_bot_9000"), "unknown")  # a name is not evidence
        self.assertEqual(collect.classify("mail", "Ann <ann@example.org>"), "unknown")

    def test_first_contact_reads_teenys_own_ledgers(self):
        from teeny_hands.mail import actions as mail
        from teeny_hands.x import posting_rules

        conn = collect.db()
        posting_rules.ensure_schema(conn)
        conn.execute("INSERT INTO x_posts (id, kind, text, created_at) VALUES ('1', 'post', 'hello @Ann_B and friends', ?)", (T0 - 5 * DAY,))
        conn.close()
        conn = mail.db()
        conn.execute("INSERT INTO mail_sent (at, kind, to_addr) VALUES (?, 'send', 'pal@example.org')", (T0 - 5 * DAY,))
        conn.close()
        conn = collect.db()
        self.assertEqual(collect.first_contact(conn, "x", "ann_b", T0 - DAY), "teenys")
        self.assertEqual(collect.first_contact(conn, "x", "ann_b", T0 - 6 * DAY), "theirs")  # wrote before Teeny's post
        self.assertEqual(collect.first_contact(conn, "x", "ann", T0 - DAY), "theirs")  # @ann_b is not @ann
        self.assertEqual(collect.first_contact(conn, "mail", "Pal <pal@example.org>", T0 - DAY), "teenys")
        self.assertEqual(collect.first_contact(conn, "mail", "new@example.org", T0 - DAY), "theirs")
        self.assertEqual(collect.first_contact(conn, "telegram", "@ann", T0 - DAY), "theirs")
        self.assertEqual(collect.first_contact(conn, "moltbook", "eve", T0 - DAY), "unknown")
        self.assertEqual(collect.first_contact(conn, "gh", "octocat", T0 - DAY), "unknown")
        conn.close()

    def test_the_split_lands_in_the_snapshot(self):
        stimuli.add("telegram", "dm", "t1", "hi", who="@ann", at=T0 - DAY)
        stimuli.add("moltbook", "comment", "c1", "hi", who="eve", at=T0 - DAY)
        stimuli.add("gh", "comment", "g1", "hi", who="renovate[bot]", at=T0 - DAY)
        stimuli.add("x", "mention", "x1", "hi", who="bob", at=T0 - DAY)
        out = collect.snapshot()
        # ann: human, first (10); eve: agent (0.5); renovate: agent (0.5); bob: unknown, first (5)
        self.assertEqual(out["written"]["attention"], 16.0)
        conn = collect.db()
        detail = json.loads(conn.execute("SELECT detail FROM world_scores WHERE component = 'attention'").fetchone()["detail"])
        conn.close()
        self.assertEqual(detail["version"], 2)
        self.assertEqual(detail["split"]["human"]["accounts"], 1)
        self.assertEqual(detail["split"]["agent"]["accounts"], 2)
        self.assertEqual(detail["split"]["unknown"]["first_contact_theirs"], 1)
        self.assertEqual(detail["v1_value"], 3)  # version 1 never counted gh

    def week(self, stats_rows, people):
        """Seven daily snapshots; `stats_rows(day)` and `people(day)` say what
        happened that day. Returns the attention credit over the week."""
        for day in range(8):
            self.at(T0 + day * DAY)
            conn = stats.db()
            date = collect.today()
            for metric, value in stats_rows(day).items():
                source, name = metric.split(".")
                conn.execute("INSERT OR REPLACE INTO stats VALUES (?, ?, ?, ?, 0)", (date, source, name, value))
            conn.close()
            for i, who in enumerate(people(day)):
                stimuli.add("telegram", "dm", f"d{day}-{i}", "hi", who=who, at=T0 + day * DAY - 3600)
            collect.snapshot()
        report = collect.report(7)
        return {c["component"]: c for c in report["components"]}["attention"]["credit"]

    def test_a_week_of_karma_alone_moves_attention_less_than_a_week_of_human_replies(self):
        base = {"x.followers": 100, "moltbook.karma": 100}
        karma = self.week(lambda d: {**base, "moltbook.karma": 100 + 10 * d}, lambda d: [])
        self.reset_world()
        humans = self.week(lambda d: base, lambda d: [f"@person{d}"] if d else [])
        # 70 points of karma in a week against seven people writing once each
        self.assertGreater(humans, 0)
        self.assertGreater(karma, 0)
        self.assertLess(karma, humans)

    def reset_world(self):
        for module in (collect, stats):
            conn = module.db()
            conn.execute(f"DELETE FROM {'world_scores' if module is collect else 'stats'}")
            conn.close()
        conn = stimuli.db()
        conn.execute("DELETE FROM stimuli")
        conn.close()

    def test_the_step_into_version_two_counts_zero(self):
        comp = score.BY_NAME["attention"]
        got = score.credit([("2026-09-07", 180.0, 1), ("2026-09-08", 190.0, 1), ("2026-09-09", 80.0, 2), ("2026-09-10", 88.0, 2)], comp)
        self.assertEqual(got["rebased"], 1)
        self.assertAlmostEqual(got["credit"], math.log(240 / 230) + math.log(138 / 130))
        self.assertAlmostEqual(got["delta_log"], math.log(138 / 130))
        conn = collect.db()
        for date, value, detail in (("2026-09-12", 180, "{}"), ("2026-09-13", 190, "{}"), ("2026-09-14", 80, '{"version": 2}')):
            conn.execute("INSERT INTO world_scores VALUES (?, 'attention', ?, ?, 0)", (date, value, detail))
        conn.close()
        out = collect.report(7)
        attention = {c["component"]: c for c in out["components"]}["attention"]
        self.assertEqual((attention["version"], attention["rebased"]), (2, 1))
        self.assertAlmostEqual(attention["credit"], round(math.log(240 / 230), 4))
        self.assertTrue(any("version 2 counted zero" in line for line in out["reading"]))
        self.assertEqual(collect.history(7)["rows"][-1]["versions"], {"attention": 2})
