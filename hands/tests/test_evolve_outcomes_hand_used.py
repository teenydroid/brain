"""bin/lib/evolve_outcomes.py, the `hand_used` kind: runs of scheduled
playbooks whose input or output calls a hand, so a hand built to be used
scores worked when it is and no-effect when it sits idle."""
from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "bin" / "lib"))

import evolve_outcomes as eo  # noqa: E402
from test_evolve_outcomes import DAY, Ledger  # noqa: E402

NOW = 1_800_000_000.0
HANDLED = [{"action": "hand", "status": "done", "result": {"ok": True}}]
SKIPPED = [{"action": "skip", "reason": "nothing worth it"}]
CHECK = {"kind": "hand_used", "hand": "bounty", "direction": "rise"}


def playbook(head: str) -> str:
    return f"+++\ndescription = \"a playbook\"\n{head}\n+++\nSay something.\n"


class HandUsed(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.tree = self.root / "tree"
        (self.tree / "hands" / "bounty").mkdir(parents=True)
        (self.tree / "hands" / "other").mkdir(parents=True)
        (self.tree / "brain" / "playbooks").mkdir(parents=True)
        self.ledger = Ledger(self.root / "state")

    def book(self, name: str, head: str):
        (self.tree / "brain" / "playbooks" / f"{name}.md").write_text(playbook(head))

    def src(self):
        return dataclasses.replace(self.ledger.sources(), tree=self.tree)

    def count(self, since=NOW - DAY, until=NOW):
        return eo.count(CHECK, self.src(), since, until)

    def test_counts_runs_of_playbooks_whose_input_calls_the_hand(self):
        self.book("announce", 'output = "post"\n\n[[inputs]]\nname = "open"\nrun = "hands/bounty announcements"')
        self.ledger.act("announce", NOW - 100)
        self.ledger.act("announce", NOW - 200, actions=SKIPPED)
        self.assertEqual(self.count(), 2)

    def test_counts_runs_of_a_playbook_whose_output_is_the_hand(self):
        self.book("pick", 'output = "hand:bounty pick"')
        self.ledger.act("pick", NOW - 100, actions=HANDLED)
        self.assertEqual(self.count(), 1)

    def test_a_failed_run_is_not_a_use(self):
        self.book("announce", 'output = "post"\n\n[[inputs]]\nname = "open"\nrun = "hands/bounty announcements"')
        self.book("pick", 'output = "hand:bounty pick"')
        self.ledger.act("announce", NOW - 100, ok=False, error="an input failed")
        self.ledger.act("pick", NOW - 100, ok=False, error="the draft turn failed", actions=HANDLED)
        self.assertEqual(self.count(), 0)

    def test_an_output_only_run_that_took_no_hand_action_is_not_a_use(self):
        self.book("pick", 'output = "hand:bounty pick"')
        self.ledger.act("pick", NOW - 100)
        self.ledger.act("pick", NOW - 200, actions=SKIPPED)
        self.ledger.act("pick", NOW - 300, actions=[{"action": "hand", "status": "refused", "result": {}}])
        self.ledger.act("pick", NOW - 400, actions=[{"action": "hand", "status": "error", "result": {}}])
        self.assertEqual(self.count(), 0)
        self.ledger.act("pick", NOW - 500, actions=SKIPPED + HANDLED)
        self.assertEqual(self.count(), 1)

    def test_an_input_callers_ok_run_is_a_use_without_an_action(self):
        self.book("announce", 'output = "none"\n\n[[inputs]]\nname = "open"\nrun = "hands/bounty announcements"')
        self.ledger.act("announce", NOW - 100)
        self.assertEqual(self.count(), 1)

    def test_budget_and_studio_skips_are_not_uses(self):
        self.book("announce", 'output = "post"\n\n[[inputs]]\nname = "open"\nrun = "hands/bounty announcements"')
        self.ledger.act("announce", NOW - 100)
        path = self.ledger.state / "act" / "announce" / "runs.jsonl"
        with open(path, "a") as fh:
            for why in ("daily budget spent", "the studio owns this slot (STUDIO=1)"):
                fh.write('{"at": "%s", "ok": true, "skipped": "%s"}\n' % (eo._iso(NOW - 50), why))
        self.assertEqual(self.count(), 1)

    def test_a_playbook_naming_another_hand_is_not_counted(self):
        self.book("other", 'output = "hand:other pick"\n\n[[inputs]]\nname = "x"\nrun = "hands/other list"')
        self.book("near", 'output = "post"\n\n[[inputs]]\nname = "x"\nrun = "hands/bounty-extra list"')
        self.ledger.act("other", NOW - 100)
        self.ledger.act("near", NOW - 100)
        self.assertEqual(self.count(), 0)

    def test_only_inside_the_window(self):
        self.book("pick", 'output = "hand:bounty pick"')
        self.ledger.act("pick", NOW - 2 * DAY, actions=HANDLED)
        self.ledger.act("pick", NOW - 100, actions=HANDLED)
        self.ledger.act("pick", NOW + 100, actions=HANDLED)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.count(NOW - 3 * DAY, NOW - DAY), 1)

    def test_validate_needs_a_hand_that_exists_in_the_tree(self):
        self.assertEqual(eo.validate(CHECK, self.tree), CHECK)
        for hand in ("nothere", "../etc", ""):
            with self.assertRaises(eo.BadCheck):
                eo.validate({**CHECK, "hand": hand}, self.tree)
        with self.assertRaises(eo.BadCheck):
            eo.validate({"kind": "hand_used", "direction": "rise"}, self.tree)
        with self.assertRaises(eo.BadCheck):
            eo.validate({**CHECK, "direction": None}, self.tree)

    def test_describe_and_brief_name_the_kind(self):
        self.assertEqual(eo.describe(CHECK), "hand_used: runs of playbooks that call hands/bounty, should rise")
        self.assertIn("`hand_used` {hand, direction}", eo.brief_text((2, 7)))

    def test_a_new_hand_used_five_times_scores_worked(self):
        self.book("pick", 'output = "hand:bounty pick"')
        for n in range(5):
            self.ledger.act("pick", NOW - 100 - n, actions=HANDLED)
        before, after = self.count(NOW - 2 * DAY, NOW - DAY), self.count()
        self.assertEqual((before, after), (0, 5))
        self.assertEqual(eo.verdict("rise", before, after, 4, 0.1)[0], "worked")

    def test_sources_without_a_tree_reads_this_checkout(self):
        src = self.ledger.sources()
        self.assertIsNone(src.tree)
        self.ledger.act("bounty-open", NOW - 100)
        self.assertEqual(eo.count(CHECK, src, NOW - DAY, NOW), 1)


if __name__ == "__main__":
    unittest.main()
