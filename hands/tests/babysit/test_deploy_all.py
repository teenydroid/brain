"""Deploys under BABYSIT_SELF_MERGE_ALL (deploy.py): with every self-change
merging itself, at most one spine restart per BABYSIT_DEPLOY_MIN_GAP_MINUTES
(a sooner one waits, never lost), last-good on every deploy, never while the
mind's beat runs, and nothing while the spine is on last-good. With the
switch off each of these is as it was (test_deploy.py)."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys

from .support import T0
from .test_deploy import DeployScratch
from teeny_hands import hand
from teeny_hands.babysit import deploy


class AllDeployScratch(DeployScratch):
    def on(self, **more: str) -> None:
        self.configure(BABYSIT_SELF_MERGE="1", BABYSIT_SELF_MERGE_ALL="1", **more)

    def at(self, minutes: float) -> None:
        os.environ["TEENY_NOW"] = str(T0 + minutes * 60)

    def new_spine(self) -> None:
        self.spine = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        hand.state_dir().joinpath("spine.pid").write_text(f"{self.spine.pid}\n")


class GapTest(AllDeployScratch):
    def test_a_second_restart_inside_the_gap_waits_and_is_not_lost(self):
        self.on()
        self.merged({"brain/SOUL.md": "one\n"})
        self.assertTrue(deploy.run()["restart"])
        self.assertTrue(self.spine_termed())
        self.new_spine()
        first = self.head()
        self.at(30)
        sha = self.merged({"brain/SOUL.md": "two\n"})
        out = deploy.run()
        self.assertEqual(out["waiting"], ["gap"])
        deploy.run()  # the next sweep, still inside the gap: no second row
        self.assertEqual(self.head(), first)
        self.assertFalse(self.spine_termed())
        self.assertEqual(self.pending()["sha"], sha)
        self.assertEqual(self.verbs(), ["deploy_ff", "deploy_restart", "deploy_wait"])
        self.at(61)
        out = deploy.run()
        self.assertEqual((out["deployed"], out["restart"]), (sha, True))
        self.assertTrue(self.spine_termed())
        self.assertIsNone(self.pending())

    def test_a_deploy_that_restarts_nothing_is_not_held_by_the_gap(self):
        self.on()
        self.merged({"brain/SOUL.md": "one\n"})
        deploy.run()
        self.at(5)
        sha = self.merged({"brain/playbooks/morning.md": "later\n"})
        self.assertEqual(deploy.run()["deployed"], sha)

    def test_the_gap_is_a_setting(self):
        self.on(BABYSIT_DEPLOY_MIN_GAP_MINUTES="10")
        self.merged({"brain/SOUL.md": "one\n"})
        deploy.run()
        self.new_spine()
        self.at(11)
        sha = self.merged({"brain/SOUL.md": "two\n"})
        self.assertEqual(deploy.run()["deployed"], sha)

    def test_switch_off_restarts_back_to_back_as_before(self):
        self.merged({"brain/SOUL.md": "one\n"})
        deploy.run()
        self.new_spine()
        sha = self.merged({"brain/SOUL.md": "two\n"})
        self.assertEqual(deploy.run()["deployed"], sha)
        self.assertTrue(self.spine_termed())


class LastGoodTest(AllDeployScratch):
    def test_switch_on_a_deploy_that_restarts_nothing_records_last_good(self):
        self.on()
        self.merged({"brain/playbooks/morning.md": "say gm later\n"})
        out = deploy.run()
        self.assertFalse(out["restart"])
        good = json.loads((hand.state_dir() / "last-good").read_text())
        self.assertEqual((good["commit"], good["gen"]), (self.start, "pr-7"))

    def test_switch_off_a_deploy_that_restarts_nothing_records_none(self):
        self.merged({"brain/playbooks/morning.md": "say gm later\n"})
        deploy.run()
        self.assertFalse((hand.state_dir() / "last-good").exists())


class BusyTest(AllDeployScratch):
    def hold(self, name: str):
        locks = hand.state_dir() / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        fh = open(locks / f"{name}.lock", "a+")
        self.addCleanup(fh.close)
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh

    def test_switch_on_waits_while_the_minds_beat_runs(self):
        self.on()
        self.merged({"brain/SOUL.md": "x\n"})
        fh = self.hold("tick")
        self.assertEqual(deploy.run()["waiting"], ["tick"])
        self.assertEqual(self.head(), self.start)
        fcntl.flock(fh, fcntl.LOCK_UN)
        self.assertIn("deployed", deploy.run())

    def test_switch_off_does_not_wait_on_the_beat(self):
        self.merged({"brain/playbooks/morning.md": "x\n"})
        self.hold("tick")
        self.assertIn("deployed", deploy.run())


class FallbackTest(AllDeployScratch):
    def fell_back(self) -> None:
        (hand.state_dir() / "spine-fallback.json").write_text(json.dumps(
            {"from": "c" * 40, "to": self.start, "gen": "pr-6", "reason": "bin/doctor failed",
             "at": "2026-09-14T06:00:00Z"}))

    def test_switch_on_nothing_deploys_while_the_spine_is_on_last_good(self):
        self.on()
        sha = self.merged({"brain/playbooks/morning.md": "x\n"})
        self.fell_back()
        out = deploy.run()
        self.assertEqual(out["waiting"], ["fallback"])
        self.assertEqual(self.head(), self.start)
        self.assertEqual(self.pending()["sha"], sha)
        (hand.state_dir() / "spine-fallback.json").unlink()  # the operator clears it
        self.assertEqual(deploy.run()["deployed"], sha)

    def test_switch_off_deploys_as_before(self):
        self.merged({"brain/playbooks/morning.md": "x\n"})
        self.fell_back()
        self.assertIn("deployed", deploy.run())
