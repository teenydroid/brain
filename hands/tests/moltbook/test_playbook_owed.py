"""The moltbook-threads playbook owes answers on Teeny's own posts first.

Live, 2026-11-23 to 11-24: a comment on one of Teeny's own posts (forgeloop
f79804f2 on the attest post 6217f8d8) waited about 26 hours with no reply.
The owed ledger and the `unanswered` list existed, no playbook carried them,
and every beat that could have answered filtered own-thread comments out
(`--skip-mine`) or declined them (helpdesk). This file pins the fix: the
playbook carries the owed list before its other inputs, answers go out with
the comment's id as `parent_id`, and the hand's own-thread reserve is spent
by exactly that path.
"""

from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import cron_policy  # noqa: E402
import playbook  # noqa: E402
from .support import Scratch  # noqa: E402


class OwedInputTest(Scratch):
    def test_the_playbook_parses_and_carries_the_owed_list_first(self):
        got = playbook.load(REPO / "brain", "moltbook-threads", REPO / "hands")
        self.assertEqual((got["output"], got["surface"]), ("hand:moltbook comment", "moltbook"))
        names = [i["name"] for i in got["inputs"]]
        self.assertIn("owed", names)
        self.assertEqual(names[-1], "owed")
        owed = got["inputs"][-1]
        self.assertFalse(owed["required"])
        self.assertEqual(owed["argv"], ["hands/moltbook", "unanswered", "--max", "20"])

    def test_the_prompt_says_the_owed_come_first_and_how_to_answer(self):
        got = playbook.load(REPO / "brain", "moltbook-threads", REPO / "hands")
        prompt = got["prompt"]
        self.assertIn("comes before all of these", prompt)
        self.assertIn('"parent_id"', prompt)
        self.assertIn("never the post's", prompt)

    def test_the_prompt_does_not_promise_words_the_list_does_not_carry(self):
        # greptile on PR 554: unanswered() returns ids and metadata only, so a
        # playbook that says the text is already there makes the beat guess.
        got = playbook.load(REPO / "brain", "moltbook-threads", REPO / "hands")
        prompt = got["prompt"]
        self.assertNotIn("its `text` already carries", prompt)
        self.assertIn("never its words", prompt)
        self.assertIn("offers it again", prompt)

    def test_the_prompt_keeps_held_rows_unanswered_and_exempts_owed_replies(self):
        # coderabbit on PR 554: a held row must not be answered, and the
        # once-per-post rule must not make an owed reply look forbidden.
        got = playbook.load(REPO / "brain", "moltbook-threads", REPO / "hands")
        prompt = got["prompt"]
        self.assertIn("Never answer an entry whose `status` is `held`", prompt)
        self.assertIn("except a reply on your own post", prompt)
        raw = (REPO / "brain" / "playbooks" / "moltbook-threads.md").read_text()
        self.assertNotIn("each with its text", raw)

    def test_the_verb_the_input_runs_exists(self):
        from teeny_hands.moltbook import cli  # noqa: F401  (registers the verbs)

        from teeny_hands.moltbook.owed import unanswered

        got = unanswered(max_items=1)
        self.assertIn("comments", got)
        self.assertIn("last_24h", got)

    def test_the_job_stays_within_the_brains_job_cap(self):
        jobs = tomllib.loads((REPO / "brain" / "cron.toml").read_text())["jobs"]
        self.assertEqual(jobs["moltbook-threads"]["command"], "bin/act moltbook-threads")
        self.assertLessEqual(len(jobs), cron_policy.MAX_JOBS)


if __name__ == "__main__":
    unittest.main()
