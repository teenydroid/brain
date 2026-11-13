"""The pinned x402-fund job (bin/x402-fund, evals/cron.pinned.toml) and the
X402_* settings bin/doctor checks (teeny_hands/x402/settings.py)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

from wallet.support import Scratch
from teeny_hands.x402 import settings  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "bin" / "lib"))
import cron_policy  # noqa: E402


class PinnedJobTest(Scratch):
    def test_the_job_runs_every_half_hour_and_is_pinned(self):
        jobs = tomllib.loads((REPO / "evals" / "cron.pinned.toml").read_text())["jobs"]
        self.assertEqual(jobs["x402-fund"]["command"], "bin/x402-fund")
        self.assertEqual(jobs["x402-fund"]["schedule"], "13-59/30 * * * *")
        self.assertNotEqual(jobs["x402-fund"].get("enabled"), False)
        self.assertTrue(os.access(REPO / "bin" / "x402-fund", os.X_OK))
        flat = cron_policy.load(REPO / "brain" / "cron.toml", REPO / "evals" / "cron.pinned.toml")
        self.assertIn("x402-fund", [j["name"] for j in flat])

    def test_switched_off_the_job_is_skipped_not_failed(self):
        done = subprocess.run([str(REPO / "bin" / "x402-fund")], capture_output=True, text=True, timeout=60,
                              env={**os.environ, "TEENY_HANDS": str(REPO / "hands")}, stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        out = json.loads(done.stdout)
        self.assertTrue(out["ok"])
        self.assertIn("off", out["skipped"])
        self.assertEqual(self.core.methods(), [])


class SettingsTest(unittest.TestCase):
    def ok(self, **env):
        return settings.check(env)

    def bad(self, needle, **env):
        with self.assertRaises(settings.SettingsError) as caught:
            settings.check(env)
        self.assertIn(needle, str(caught.exception))

    def test_nothing_set_is_fine_and_off(self):
        line = self.ok()
        self.assertIn("top-up off", line)
        self.assertIn("Firecrawl off", line)

    def test_a_sane_top_up_says_what_it_does(self):
        line = self.ok(X402_FUND="1", X402_FUND_FLOOR_USD="1", X402_FUND_TARGET_USD="5", X402_FUND_MAX_PER_DAY_USD="10")
        self.assertIn("below $1 on Base, up to $5", line)

    def test_on_without_its_keys_or_with_them_wrong_fails(self):
        self.bad("X402_FUND_FLOOR_USD is not set", X402_FUND="1")
        self.bad("below X402_FUND_TARGET_USD", X402_FUND="1", X402_FUND_FLOOR_USD="5", X402_FUND_TARGET_USD="5",
                 X402_FUND_MAX_PER_DAY_USD="10")
        self.bad("ceiling", X402_FUND="1", X402_FUND_FLOOR_USD="1", X402_FUND_TARGET_USD="5", X402_FUND_MAX_PER_DAY_USD="5000")
        self.bad("is 1 or 0", X402_FUND="yes please")

    def test_set_but_off_is_still_checked(self):
        self.bad("above zero", X402_FUND_FLOOR_USD="0")
        self.bad("X402_FUND_COOLDOWN_MIN", X402_FUND_COOLDOWN_MIN="1")
        self.bad("X402_FUND_KEEP_SOL", X402_FUND_KEEP_SOL="50")

    def test_the_search_limits_are_checked(self):
        self.bad("X402_EXA_MAX_USD", X402_EXA_MAX_USD="abc")
        self.bad("ceiling", X402_FIRECRAWL_MAX_USD="2")
        self.bad("X402_TEXT_CHARS", X402_TEXT_CHARS="100")
        self.bad("is 1 or 0", X402_FIRECRAWL="maybe")
