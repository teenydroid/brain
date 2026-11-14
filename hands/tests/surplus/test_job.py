"""The pinned surplus-sweep job (bin/surplus-sweep, evals/cron.pinned.toml),
the SURPLUS_* settings bin/doctor checks (teeny_hands/surplus/settings.py),
and the fence that keeps the sweep out of every playbook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

from wallet.support import Scratch
from teeny_hands.surplus import settings  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "bin" / "lib"))
import cron_policy  # noqa: E402

SURPLUS = "0xb8750da63a6f9e65062e33604356f3dfe620e563"


class PinnedJobTest(Scratch):
    def test_the_job_runs_every_two_hours_and_is_pinned(self):
        jobs = tomllib.loads((REPO / "evals" / "cron.pinned.toml").read_text())["jobs"]
        self.assertEqual(jobs["surplus-sweep"]["command"], "bin/surplus-sweep")
        self.assertEqual(jobs["surplus-sweep"]["schedule"], "41 */2 * * *")
        self.assertTrue(os.access(REPO / "bin" / "surplus-sweep", os.X_OK))
        flat = cron_policy.load(REPO / "brain" / "cron.toml", REPO / "evals" / "cron.pinned.toml")
        self.assertIn("teeny.surplus-sweep", [j["name"] for j in flat])

    def test_switched_off_the_job_is_skipped_not_failed(self):
        done = subprocess.run([str(REPO / "bin" / "surplus-sweep")], capture_output=True, text=True, timeout=60,
                              env={**os.environ, "TEENY_HANDS": str(REPO / "hands")}, stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        out = json.loads(done.stdout)
        self.assertTrue(out["ok"])
        self.assertIn("off", out["skipped"])
        self.assertEqual(self.core.methods(), [])


class SettingsTest(unittest.TestCase):
    def bad(self, needle, **env):
        with self.assertRaises(settings.SettingsError) as caught:
            settings.check(env)
        self.assertIn(needle, str(caught.exception))

    def test_nothing_set_is_off(self):
        self.assertEqual(settings.check({}), "surplus sweep off")

    def test_on_says_where_the_money_goes(self):
        line = settings.check({"SURPLUS_SWEEP": "1", "SURPLUS_ADDRESS": SURPLUS})
        self.assertIn(SURPLUS, line)
        self.assertIn("above $10", line)

    def test_on_without_an_address_or_with_a_bad_one_fails(self):
        self.bad("SURPLUS_ADDRESS is not set", SURPLUS_SWEEP="1")
        self.bad("0x and 40 hex", SURPLUS_SWEEP="1", SURPLUS_ADDRESS="DJuTSGxVrD9mHwmiWCFcEtnWhFF6ssNopxtBMT4kiVKZ")

    def test_the_base_reserve_never_undercuts_the_x402_rail(self):
        rail = {"X402_FUND": "1", "X402_FUND_FLOOR_USD": "2", "X402_FUND_TARGET_USD": "10", "X402_FUND_MAX_PER_DAY_USD": "10"}
        self.bad("X402_FUND_TARGET_USD", SURPLUS_SWEEP="1", SURPLUS_ADDRESS=SURPLUS, SURPLUS_KEEP_BASE_USD="5", **rail)
        settings.check({"SURPLUS_SWEEP": "1", "SURPLUS_ADDRESS": SURPLUS, "SURPLUS_KEEP_BASE_USD": "10", **rail})

    def test_set_but_off_is_still_checked(self):
        self.bad("ceiling", SURPLUS_KEEP_SOL="50")
        self.bad("above zero", SURPLUS_MIN_PAYOUT_USD="0")
        self.bad("is 1 or 0", SURPLUS_SWEEP="sure")


class PlaybookTest(Scratch):
    """The sweep moves money: no playbook outputs it or runs it as an input;
    an input runs only the read verbs."""

    def load(self, front):
        import playbook

        brain = self.root / "brain"
        (brain / "playbooks").mkdir(parents=True, exist_ok=True)
        (brain / "playbooks" / "p.md").write_text(f'+++\ndescription = "x"\n{front}\n+++\ngo')
        return playbook.load(brain, "p")

    def test_the_sweep_is_never_a_playbook_output_or_input(self):
        import playbook

        for verb in ("sweep", "status"):
            with self.assertRaises(playbook.PlaybookError, msg=verb):
                self.load(f'output = "hand:surplus {verb}"')
        with self.assertRaises(playbook.PlaybookError):
            self.load('output = "none"\n[[inputs]]\nrun = "hands/surplus sweep"')
        self.assertEqual(self.load('output = "none"\n[[inputs]]\nrun = "hands/surplus status"')["inputs"][0]["argv"][:2],
                         ["hands/surplus", "status"])
