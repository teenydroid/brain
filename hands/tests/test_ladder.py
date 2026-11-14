"""hands/ladder: the autonomy ladder Teeny reads, and the fence proposal it
may file (docs/roadmap.md, G3; docs/governance.md, "How a fence changes").

A dry run produces an issue body that names the fence, the evidence and the
risk; a proposal for a fence the charter doesn't name is refused; one a week
at most; live, it opens one issue on Teeny's own repository and labels it
`fence-proposal`. GitHub is a stand-in."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "hands" / "lib"))
sys.path.insert(0, str(REPO / "bin" / "lib"))

import playbook  # noqa: E402
from teeny_hands import hand  # noqa: E402
from teeny_hands.ladder import cli  # noqa: E402

PROPOSAL = {
    "fence": "browser-deny",
    "title": "let the browser read one forum the deny list blocks",
    "ask": "take the forum's host off the browser's deny list",
    "evidence": "three browse tasks this week stopped at a link to it (L3.browser_week 4/7)",
    "would_do": "read the threads where agent builders compare tools",
    "risk": "a docs page could carry injected instructions; the operator task stays read-only and its notes are data",
}


class FakeGitHub:
    def __init__(self):
        self.opened: list[tuple] = []
        self.calls: list[tuple] = []

    def open_issue(self, owner, name, title, body):
        self.opened.append((owner, name, title, body))
        return {"number": 41, "html_url": f"https://github.com/{owner}/{name}/issues/41"}

    def call(self, method, path, query=(), body=None):
        self.calls.append((method, path, body))
        return [{"name": "fence-proposal"}]


class LadderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for part in ("evals", "docs", "secrets", "state"):
            (self.root / part).mkdir()
        shutil.copy(REPO / "evals" / "autonomy.toml", self.root / "evals")
        shutil.copy(REPO / "docs" / "governance.md", self.root / "docs")
        self.env = mock.patch.dict(os.environ, {"TEENY_ROOT": str(self.root)})
        self.env.start()
        os.environ.pop("DRY_RUN", None)
        self.secrets("SAFETY_JUDGE=0\n")

    def tearDown(self):
        self.env.stop()
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def secrets(self, text: str) -> None:
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    def test_the_report_is_what_frozen_code_counts(self):
        caps = {c["capability"]: c for c in cli.report()["capabilities"]}
        self.assertEqual(caps["merge"]["level"], 4)
        self.assertTrue(any("L5.human_merged 0/5" in s for s in caps["merge"]["still_needs"]))

    def test_fences_are_the_charters(self):
        out = cli.fences()
        ids = {f["id"] for f in out["fences"]}
        self.assertTrue({"guard", "wallet-caps", "browser-deny", "ladder", "reviewer"} <= ids)
        self.assertIn("ladder:merge", out["capabilities"])

    def test_a_dry_run_names_the_fence_the_evidence_and_the_risk(self):
        out = cli.propose(None, dict(PROPOSAL))
        self.assertTrue(out["dry_run"])
        self.assertEqual((out["would_open_issue"], out["label"]), ("teenydroid/brain", "fence-proposal"))
        self.assertTrue(out["title"].startswith("fence-proposal: "))
        body = out["body"]
        self.assertIn("**Fence:** `browser-deny`: The browser opens public https pages only, never a host on", body)
        self.assertIn("## My evidence\n\n" + PROPOSAL["evidence"], body)
        self.assertIn("## What could go wrong\n\n" + PROPOSAL["risk"], body)
        self.assertIn("## What I would do with it", body)
        # A rehearsal opens nothing and counts nothing.
        self.assertEqual(cli.propose(None, dict(PROPOSAL))["dry_run"], True)

    def test_a_level_proposal_carries_the_ladders_own_count(self):
        out = cli.propose(None, {**PROPOSAL, "fence": "ladder:merge"})
        self.assertIn("`merge`, at L4", out["body"])
        self.assertIn("L5.human_merged 0/5", out["body"])

    def test_an_unknown_fence_or_capability_is_refused(self):
        for fence in ("the-off-switch", "ladder:everything"):
            with self.assertRaises(hand.Refused):
                cli.propose(None, {**PROPOSAL, "fence": fence})

    def test_every_field_is_needed_and_nothing_else_is_taken(self):
        for missing in ("evidence", "risk", "fence"):
            body = dict(PROPOSAL)
            body.pop(missing)
            with self.assertRaises(hand.Refused):
                cli.propose(None, body)
        with self.assertRaises(hand.Refused):
            cli.propose(None, {**PROPOSAL, "labels": ["approved"]})
        with self.assertRaises(hand.Refused):
            cli.propose(None, {**PROPOSAL, "risk": "x" * 2000})

    def test_live_it_opens_one_labelled_issue_a_week(self):
        self.secrets("SAFETY_JUDGE=0\nDRY_RUN=0\nGITHUB_TOKEN=t\n")
        gh = FakeGitHub()
        out = cli.propose(None, dict(PROPOSAL), gh)
        self.assertEqual((out["number"], out["label"]), (41, True))
        self.assertEqual(gh.opened[0][:2], ("teenydroid", "brain"))
        self.assertEqual(gh.calls, [("POST", "/repos/teenydroid/brain/issues/41/labels", {"labels": ["fence-proposal"]})])
        with self.assertRaises(hand.Refused) as refused:
            cli.propose(None, dict(PROPOSAL), gh)
        self.assertEqual(refused.exception.detail.get("rule"), "weekly")
        self.assertEqual(len(gh.opened), 1)
        os.environ["TEENY_NOW"] = str(hand.now() + 8 * 86400)
        try:
            cli.propose(None, dict(PROPOSAL), gh)
        finally:
            os.environ.pop("TEENY_NOW")
        self.assertEqual(len(gh.opened), 2)

    def test_the_playbook_parses_and_can_only_propose(self):
        got = playbook.load(REPO / "brain", "fence-proposal", REPO / "hands")
        self.assertEqual(got["output"], "hand:ladder propose")
        self.assertEqual(playbook.VERB_OUTPUTS["ladder"], ("propose",))

    def test_the_hand_is_frozen(self):
        import frozen

        patterns = frozen.parse_patterns((REPO / "evals" / "frozen.txt").read_text())
        for path in ("hands/ladder", "hands/lib/teeny_hands/ladder/cli.py"):
            self.assertIsNotNone(frozen.frozen_pattern(path, patterns), path)
        self.assertIsNone(frozen.frozen_pattern("brain/playbooks/fence-proposal.md", patterns))


if __name__ == "__main__":
    unittest.main()
