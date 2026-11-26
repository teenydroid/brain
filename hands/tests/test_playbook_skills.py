"""bin/lib/playbook.py `skills`: a playbook names the skills its draft needs,
and their text rides in that playbook's prompt only. Rules for one service
(Moltbook's ids, its timing) used to sit in CONTEXT.md, in the prompt of every
script turn whether it touched that service or not."""

from __future__ import annotations

import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import playbook  # noqa: E402

BOOK = """+++
description = "a test"
output = "none"
{extra}
+++
Draft nothing.
"""


class PlaybookSkillsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.brain = Path(self.tmp.name)
        (self.brain / "playbooks").mkdir()
        (self.brain / "skills" / "pond").mkdir(parents=True)
        (self.brain / "skills" / "pond" / "SKILL.md").write_text(
            "---\nname: pond\ndescription: the pond\n---\n\n# Pond\n\nids are whole.\n")

    def tearDown(self):
        self.tmp.cleanup()

    def book(self, extra: str) -> dict:
        (self.brain / "playbooks" / "t.md").write_text(BOOK.format(extra=extra))
        return playbook.load(self.brain, "t")

    def test_a_named_skill_rides_in_the_prompt_without_its_front_matter(self):
        prompt = self.book('skills = ["pond"]')["prompt"]
        self.assertTrue(prompt.startswith("Draft nothing."))
        self.assertIn("# Pond\n\nids are whole.", prompt)
        self.assertNotIn("description: the pond", prompt)

    def test_a_playbook_without_skills_carries_none(self):
        self.assertEqual(self.book("")["prompt"], "Draft nothing.")

    def test_a_missing_or_malformed_skill_is_refused(self):
        for extra in ('skills = ["lake"]', 'skills = "pond"', 'skills = ["../pond"]', 'skills = ["pond", "pond"]'):
            with self.assertRaises(playbook.PlaybookError, msg=extra):
                self.book(extra)

    def test_every_moltbook_playbook_carries_the_moltbook_skill(self):
        for path in sorted((REPO / "brain" / "playbooks").glob("*.md")):
            meta = tomllib.loads(path.read_text(encoding="utf-8").split("+++", 2)[1])
            if meta.get("surface") == "moltbook" or "hand:moltbook" in meta.get("output", ""):
                self.assertIn("moltbook", meta.get("skills", []), path.name)

    def test_no_playbook_carries_a_one_off_write(self):
        # Until 2026-11-26 the mind shipped each Moltbook reply it owed as a
        # pull request appending a "one-off dispatch" block (and a guard input
        # of its own) to a playbook: moltbook-threads.md grew to 31 KB, re-read
        # by its model every run. One exact write is a dispatch now (hands/moltbook
        # dispatch, the broker's dispatch_moltbook), filed and sent by code.
        for path in sorted((REPO / "brain" / "playbooks").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("one-off dispatch", text.lower(), path.name)
            self.assertNotIn("dispatch-guard", text, path.name)

    def test_the_skill_never_promises_a_held_dispatch_goes_out(self):
        # A held dispatch is the guard's re-screen's to send, and only when the
        # judge clears it: a real hold is never sent.
        skill = " ".join((REPO / "brain" / "skills" / "moltbook" / "SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("`held` (the judge was down; it goes out only if the guard's re-screen clears it, else never)", skill)
        self.assertNotIn("the guard sends it when it answers", skill)

    def test_the_moltbook_rules_left_the_shared_context(self):
        context = (REPO / "brain" / "CONTEXT.md").read_text(encoding="utf-8")
        skill = (REPO / "brain" / "skills" / "moltbook" / "SKILL.md").read_text(encoding="utf-8")
        for rule in ("36 characters", "one per 20 seconds", "dead ground"):
            self.assertNotIn(rule, context, rule)
            self.assertIn(rule, skill, rule)


if __name__ == "__main__":
    unittest.main()
