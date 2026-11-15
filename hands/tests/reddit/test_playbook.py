"""The reddit-browse playbook and its cron slot: it parses, skips before the
model when nothing is due, may only queue a read, and the hand behind it is
frozen like hands/x and hands/moltbook."""

from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import cron_policy  # noqa: E402
import frozen  # noqa: E402
import playbook  # noqa: E402


class PlaybookTest(unittest.TestCase):
    def test_the_playbook_parses_and_only_queues_a_read(self):
        got = playbook.load(REPO / "brain", "reddit-browse", REPO / "hands")
        self.assertEqual((got["output"], got["surface"]), ("hand:reddit browse", "reddit"))
        self.assertEqual([(i["argv"], i["required"]) for i in got["inputs"]][0], (["hands/reddit", "due"], True))
        self.assertNotIn("signup", playbook.VERB_OUTPUTS["reddit"])
        self.assertNotIn("profile", playbook.VERB_OUTPUTS["reddit"])

    def test_it_runs_every_two_hours_within_the_brains_job_cap(self):
        jobs = tomllib.loads((REPO / "brain" / "cron.toml").read_text())["jobs"]
        self.assertEqual(jobs["reddit-browse"]["command"], "bin/act reddit-browse")
        self.assertLessEqual(len(jobs), cron_policy.MAX_JOBS)
        cron_policy.check(REPO / "brain" / "cron.toml", REPO / "evals" / "cron.pinned.toml") \
            if hasattr(cron_policy, "check") else None

    def test_the_hand_and_its_allowlist_are_frozen(self):
        patterns = frozen.parse_patterns((REPO / "evals" / "frozen.txt").read_text())
        for path in ("hands/reddit", "hands/lib/teeny_hands/reddit/actions.py", "deploy/reddit-subreddits.txt"):
            self.assertIsNotNone(frozen.frozen_pattern(path, patterns), path)
        self.assertIsNone(frozen.frozen_pattern("brain/playbooks/reddit-browse.md", patterns))


if __name__ == "__main__":
    unittest.main()
