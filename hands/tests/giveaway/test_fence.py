"""The giveaway-pool fence (docs/governance.md): Tiny Drops are paid by code.

A playbook, which Teeny writes, reaches hands/giveaway only to open a round
(checked against every GIVEAWAY_* cap by the hand) or to pick the best reply
in a tool-less turn. Who entered, the draw, the payout and the clawback are
never a draft's output, and an input runs only the hand's read verbs. The
switch that turns rounds on sits on the autonomy ladder like every other
spend.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import autonomy  # noqa: E402
import playbook  # noqa: E402


class PlaybookFenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.brain = Path(self.tmp.name) / "brain"
        (self.brain / "playbooks").mkdir(parents=True)

    def load(self, front: str) -> dict:
        (self.brain / "playbooks" / "drop.md").write_text(f'+++\ndescription = "x"\n{front}\n+++\ngo')
        return playbook.load(self.brain, "drop")

    def test_the_draw_settle_and_payout_are_never_a_playbook_output(self):
        for verb in ("entries", "draw", "settle", "clawback"):
            with self.assertRaisesRegex(playbook.PlaybookError, "moves money", msg=verb):
                self.load(f'output = "hand:giveaway {verb}"')

    def test_only_open_and_judge_are_outputs(self):
        for verb in ("open", "judge"):
            self.assertEqual(self.load(f'output = "hand:giveaway {verb}"')["output"], f"hand:giveaway {verb}")
        for verb in ("status", "announcements", "pay", "add"):
            with self.assertRaises(playbook.PlaybookError, msg=verb):
                self.load(f'output = "hand:giveaway {verb}"')

    def test_an_input_runs_only_the_read_verbs(self):
        for verb in ("entries", "draw", "settle", "clawback", "open", "judge"):
            with self.assertRaises(playbook.PlaybookError, msg=verb):
                self.load(f'output = "none"\n[[inputs]]\nname = "g"\nrun = "hands/giveaway {verb}"')
        for verb in ("status", "announcements"):
            got = self.load(f'output = "none"\n[[inputs]]\nname = "g"\nrun = "hands/giveaway {verb}"')
            self.assertEqual(got["inputs"][0]["argv"][:2], ["hands/giveaway", verb])

    def test_the_wallet_still_takes_no_output(self):
        # Index 0 is untouched: rounds never pay from Teeny's own wallet.
        for verb in ("pay", "address"):
            with self.assertRaises(playbook.PlaybookError, msg=verb):
                self.load(f'output = "hand:wallet {verb}"')


class SwitchTest(unittest.TestCase):
    def test_giveaways_is_a_spend_switch_and_off_by_default(self):
        caps = autonomy.load(REPO / "evals")
        switch = [s for s in caps["spend"]["switch"] if s["name"] == "GIVEAWAYS"]
        self.assertEqual(len(switch), 1)
        self.assertEqual((switch[0]["file"], switch[0]["when"]), ("teeny.env", "truthy"))
        self.assertGreaterEqual(switch[0]["needs"], 3)
        self.assertFalse(autonomy.is_on(None, "truthy"))
        example = (REPO / "deploy" / "teeny.env.example").read_text()
        self.assertIn("# GIVEAWAYS=0\n", example)
        for cap in ("GIVEAWAY_POOL_MAX_TINY", "GIVEAWAY_SEASON_MAX_TINY", "GIVEAWAY_ROUNDS_PER_WEEK",
                    "GIVEAWAY_MAX_PER_WINNER_TINY", "GIVEAWAY_CLAWBACK_DAYS", "GIVEAWAY_X_LOOKUPS_PER_ROUND"):
            self.assertIn(f"# {cap}=\n", example, cap)
        self.assertIn("# GIVEAWAY_CLUSTER=devnet\n", example)


if __name__ == "__main__":
    unittest.main()
