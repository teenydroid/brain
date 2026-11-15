"""Runway as a sense (docs/roadmap.md, S2): the days the wallet's USDC lasts
at this week's burn, as one `health · runway` stimulus a day and a line in
the mind's beat header, and the tier from the frozen evals/runway.toml that
cuts optional spend (pictures and clips, evolve candidates) when it is low.
Off unless RUNWAY=1. The core and the chain are faked; nothing is signed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from wallet.support import DAY, MOVING, T0, Scratch, token_accounts
from teeny_hands import hand, runway, stimuli
from teeny_hands.media import assets

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import rhythm  # noqa: E402


class RunwayTest(Scratch):
    settings = {"RUNWAY": "1"}

    def setUp(self) -> None:
        super().setUp()
        self.costs(70.0)
        self.core.answers["openhuman.x402_list_payments"] = {"payments": []}

    def costs(self, week_usd: float) -> None:
        self.core.answers["openhuman.cost_report"] = {"totals": {"cost_usd": week_usd, "calls": 10}}

    def hold(self, usdc: float) -> None:
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(int(usdc * 1e6))

    def inbox(self) -> list[dict]:
        return [s for s in stimuli.recent()["stimuli"] if (s["surface"], s["kind"]) == ("health", "runway")]

    # --- the tiers ------------------------------------------------------------------------------

    def test_the_frozen_tiers_parse_and_the_lowest_one_under_applies(self):
        table = runway.tiers()
        self.assertEqual([t["name"] for t in table], ["critical", "low"])
        self.assertEqual(runway.tier_for(3, table)["name"], "critical")
        self.assertEqual(runway.tier_for(10, table)["name"], "low")
        self.assertIsNone(runway.tier_for(100, table))
        self.assertIsNone(runway.tier_for(None, table))

    def test_a_bad_tier_file_fails_closed(self):
        bad = self.root / "runway.toml"
        bad.write_text("[tiers.low]\nbelow_days = 0\n")
        with self.assertRaises(hand.HandError):
            runway.tiers(bad)

    # --- the daily sense --------------------------------------------------------------------------

    def test_a_low_runway_files_one_stimulus_and_cuts_optional_spend(self):
        self.hold(100)  # 100 USDC at 10 a day: 10 days, the low tier
        out = runway.daily()
        self.assertEqual((out["days"], out["tier"], out["filed"]), (10.0, "low", True))
        (got,) = self.inbox()
        self.assertIn("100.00 USDC", got["text"])
        self.assertIn("10 days at this rate", got["text"])
        self.assertIn("no pictures or clips", got["text"])
        self.assertFalse(runway.daily()["filed"])  # once a day
        lim = runway.limits()
        self.assertEqual((lim["media"], lim["evolve_candidates"]), (False, 2))
        self.assertEqual(runway.cap_candidates(4), (2, "low tier (runway 10 days, under 30): 2 candidate(s), not 4"))
        self.assertEqual(runway.cap_candidates(1), (1, None))
        self.assertEqual(self.moved(), [])

    def test_below_the_critical_tier_one_candidate(self):
        self.hold(30)
        runway.daily()
        self.assertEqual(runway.cap_candidates(4)[0], 1)

    def test_a_long_runway_cuts_nothing(self):
        self.hold(1000)
        out = runway.daily()
        self.assertIsNone(out["tier"])
        self.assertEqual(runway.limits()["media"], True)
        self.assertEqual(runway.cap_candidates(4), (4, None))
        self.assertIn("nothing is cut", self.inbox()[0]["text"])

    def test_an_unread_cost_is_not_measured_and_unmeasured_is_the_low_tier(self):
        self.hold(1000)
        del self.core.answers["openhuman.cost_report"]
        out = runway.daily()
        self.assertFalse(out["measured"])
        self.assertEqual(runway.limits()["tier"], "low")
        self.assertIn("could not be measured", self.inbox()[0]["text"])

    def test_a_stale_measure_counts_as_the_low_tier(self):
        self.hold(1000)
        runway.daily()
        self.assertIsNone(runway.limits()["tier"])
        self.at(T0 + (runway.STALE_DAYS + 1) * DAY)
        self.assertEqual(runway.limits()["tier"], "low")

    def test_off_by_default_nothing_is_measured_or_cut(self):
        self.hold(1)
        runway.daily()
        self.configure(RUNWAY=None)
        self.assertIn("RUNWAY", runway.daily()["skipped"])
        self.assertEqual(runway.limits()["media"], True)
        self.assertEqual(runway.cap_candidates(4), (4, None))
        self.assertIsNone(runway.header_line())

    # --- what the tier reaches --------------------------------------------------------------------

    def test_no_picture_is_made_on_a_low_tier(self):
        self.hold(100)
        runway.daily()
        with self.assertRaises(hand.Refused) as no:
            assets.generate_image({"prompt": "teeny at a desk"}, api=object())
        self.assertEqual(no.exception.detail["rule"], "runway")

    def test_the_mind_is_told_why(self):
        self.hold(100)
        runway.daily()
        lines = rhythm.header()
        self.assertIn("Runway: runway 10 days, under 30. You are on the low tier: no pictures or clips, "
                      "at most 2 evolve candidate(s) a generation.", lines)

    def test_the_pinned_job_runs_it(self):
        self.hold(100)
        done = subprocess.run([str(REPO / "bin" / "health"), "runway"], capture_output=True, text=True,
                              env=os.environ, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout)["tier"], "low")

    def test_the_pinned_job_switched_off_is_skipped_not_failed(self):
        # The scheduler counts a non-zero exit as a failed job.
        self.configure(RUNWAY=None)
        done = subprocess.run([str(REPO / "bin" / "health"), "runway"], capture_output=True, text=True,
                              env=os.environ, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        out = json.loads(done.stdout)
        self.assertTrue(out["ok"])
        self.assertIn("RUNWAY", out["skipped"])
        self.assertEqual(self.inbox(), [])
