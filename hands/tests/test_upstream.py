"""hands/upstream: a new OpenHuman release that closes a gap becomes one
pin-bump issue, once (docs/roadmap.md, G9). GitHub is a stand-in; the pin is
a gitlink in a scratch repository; the release notes are made up."""

from __future__ import annotations

import os
import shutil
import subprocess
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
from teeny_hands.upstream import cli  # noqa: E402

PIN = "a" * 40
NOTES = """## What's new
- Cron jobs can now be declared in a file (closes #8119)
- Faster startup
- Langfuse export without the proxy (tinyhumansai/openhuman/pull/9001, see https://github.com/tinyhumansai/openhuman/issues/8122)
- kill yourself #8120
"""


class FakeGitHub:
    def __init__(self, releases):
        self.releases = releases
        self.opened: list[tuple] = []

    def call(self, method, path, query=(), body=None):
        if path.endswith(f"/commits/{PIN}"):
            return {"commit": {"committer": {"date": "2026-11-01T00:00:00Z"}}}
        if path.endswith("/releases"):
            return self.releases
        raise AssertionError(path)

    def open_issue(self, owner, name, title, body):
        self.opened.append((owner, name, title, body))
        return {"number": 52, "html_url": f"https://github.com/{owner}/{name}/issues/52"}


def release(tag, published, body=NOTES, **kw):
    return {"tag_name": tag, "published_at": published, "body": body, "draft": False, "prerelease": False, **kw}


class UpstreamTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs").mkdir()
        (self.root / "secrets").mkdir()
        shutil.copy(REPO / "docs" / "oh-gaps.md", self.root / "docs")
        env = {**os.environ, "GIT_AUTHOR_NAME": "o", "GIT_AUTHOR_EMAIL": "o@e", "GIT_COMMITTER_NAME": "o",
               "GIT_COMMITTER_EMAIL": "o@e"}
        for args in (["init", "-q", "-b", "main"], ["update-index", "--add", "--cacheinfo", f"160000,{PIN},vendor/openhuman"],
                     ["commit", "-q", "-m", "pin"]):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, env=env, capture_output=True)
        self.env = mock.patch.dict(os.environ, {"TEENY_ROOT": str(self.root)})
        self.env.start()
        os.environ.pop("DRY_RUN", None)
        self.secrets("SAFETY_JUDGE=0\n")
        self.gh = FakeGitHub([release("v0.9.0", "2026-11-10T00:00:00Z"), release("v0.8.0", "2026-10-01T00:00:00Z"),
                              release("v0.9.1", "2026-11-11T00:00:00Z", body="- docs only")])

    def tearDown(self):
        self.env.stop()
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def secrets(self, text):
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    def test_open_gaps_are_read_with_their_numbers(self):
        got = cli.gaps()
        self.assertEqual(got["G2"], {"8119"})
        self.assertNotIn("G1", got)  # solved
        self.assertNotIn("G11", got)  # resolved

    def test_releases_after_the_pin_with_the_gaps_they_close(self):
        out = cli.releases(gh=self.gh)
        self.assertEqual(out["pin"], PIN)
        self.assertEqual([r["tag"] for r in out["releases"]], ["v0.9.0", "v0.9.1"])
        closes = {c["gap"]: c["lines"] for c in out["releases"][0]["closes"]}
        self.assertEqual(set(closes), {"G2", "G5"})
        self.assertIn("closes #8119", closes["G2"][0])
        # A line the guard's lexicon holds never reaches the playbook.
        self.assertNotIn("G3", closes)
        self.assertEqual(out["releases"][1]["closes"], [])

    def test_a_dry_run_says_what_it_would_open(self):
        out = cli.propose(None, {"tag": "v0.9.0", "why": "G2 would let me drop bin/seed-cron's reconciliation."}, self.gh)
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["title"], "pin bump: openhuman v0.9.0 (closes G2, G5)")
        self.assertIn("- **G2**", out["body"])
        self.assertIn("## Why I want it", out["body"])
        self.assertEqual(self.gh.opened, [])

    def test_a_fixture_release_opens_one_issue_once(self):
        self.secrets("SAFETY_JUDGE=0\nDRY_RUN=0\nGITHUB_TOKEN=t\n")
        out = cli.propose(None, {"tag": "v0.9.0", "why": "fewer workarounds"}, self.gh)
        self.assertEqual(out["number"], 52)
        self.assertEqual(len(self.gh.opened), 1)
        self.assertEqual(self.gh.opened[0][:2], ("teenydroid", "brain"))
        with self.assertRaises(hand.Refused) as again:
            cli.propose(None, {"tag": "v0.9.0", "why": "fewer workarounds"}, self.gh)
        self.assertEqual(again.exception.detail.get("rule"), "once")
        self.assertEqual(len(self.gh.opened), 1)

    def test_a_release_that_closes_nothing_or_is_old_is_refused(self):
        for tag, rule in (("v0.9.1", "gap"), ("v0.8.0", "release"), ("v9", "release")):
            with self.assertRaises(hand.Refused) as refused:
                cli.propose(None, {"tag": tag, "why": "x"}, self.gh)
            self.assertEqual(refused.exception.detail.get("rule"), rule, tag)

    def test_the_body_is_only_tag_and_why(self):
        with self.assertRaises(hand.Refused):
            cli.propose(None, {"tag": "v0.9.0", "why": "x", "title": "merge it now"}, self.gh)

    def test_the_playbook_parses_and_can_only_propose(self):
        got = playbook.load(REPO / "brain", "pin-bump", REPO / "hands")
        self.assertEqual(got["output"], "hand:upstream propose")
        self.assertEqual(playbook.VERB_OUTPUTS["upstream"], ("propose",))


if __name__ == "__main__":
    unittest.main()
