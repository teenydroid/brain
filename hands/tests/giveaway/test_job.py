"""The pinned giveaway-settle job (bin/giveaway-run, evals/cron.pinned.toml):
`hands/giveaway due` every hour, then, only with the switch on, the round
threads' playbooks when `announcements` says a post is owed. A fake
hands/giveaway and a fake bin/act stand in, so nothing here runs a model."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "bin" / "lib"))
import cron_policy  # noqa: E402

FAKE_HAND = """#!/usr/bin/env bash
dir="$(dirname "$0")"
echo "$1" >>"$dir/calls"
cat "$dir/$1.json"
"""

FAKE_ACT = """#!/usr/bin/env bash
dir="$(dirname "$0")"
echo "$*" >>"$dir/act-calls"
if [ "$1" = --check ]; then
  [ -e "$dir/has-$2" ] || exit 1
  echo '{"ok": true}'
  exit 0
fi
[ -e "$dir/fails-$1" ] && exit 1
exit 0
"""

NOTHING_OWED = {"open": [], "countdown": [], "to_judge": [], "winners": [], "claim_open": [], "closing_soon": []}


class PinnedTest(unittest.TestCase):
    def test_the_job_is_pinned_hourly_and_never_catches_up(self):
        jobs = tomllib.loads((REPO / "evals" / "cron.pinned.toml").read_text())["jobs"]
        job = jobs["giveaway-settle"]
        self.assertEqual((job["command"], job["schedule"]), ("bin/giveaway-run", "33 * * * *"))
        self.assertNotIn("catch_up", job)
        self.assertIn("bin/giveaway-run", cron_policy.NO_CATCH_UP_COMMANDS)
        self.assertTrue(os.access(REPO / "bin" / "giveaway-run", os.X_OK))
        flat = cron_policy.load(REPO / "brain" / "cron.toml", REPO / "evals" / "cron.pinned.toml")
        self.assertIn("teeny.giveaway-settle", [j["name"] for j in flat])


class DispatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        for name, body in (("giveaway", FAKE_HAND), ("act", FAKE_ACT)):
            (self.dir / name).write_text(body)
            (self.dir / name).chmod(0o755)
        self.answer("due", {"ok": True, "entries": {"rounds": []}, "draw": {"rounds": []},
                            "settle": {"rounds": []}, "clawback": {"rounds": []}})
        self.on(True)
        self.answer("announcements", NOTHING_OWED)
        for playbook in ("giveaway-judge", "giveaway-post"):
            (self.dir / f"has-{playbook}").touch()

    def answer(self, verb: str, body: dict) -> None:
        (self.dir / f"{verb}.json").write_text(json.dumps(body))

    def on(self, switch: bool) -> None:
        self.answer("status", {"ok": True, "season": {"on": switch}, "rounds": []})

    def owe(self, **items: list) -> None:
        self.answer("announcements", {**NOTHING_OWED, **items})

    def run_job(self) -> tuple[int, dict, list[str]]:
        env = {**os.environ, "TEENY_HANDS": str(self.dir), "TEENY_ACT": str(self.dir / "act"), "TEENY_ROOT": str(REPO)}
        done = subprocess.run([str(REPO / "bin" / "giveaway-run")], capture_output=True, text=True, env=env, timeout=60,
                              stdin=subprocess.DEVNULL)
        calls = (self.dir / "act-calls").read_text().splitlines() if (self.dir / "act-calls").exists() else []
        return done.returncode, json.loads(done.stdout), calls

    def runs(self, calls: list[str]) -> list[str]:
        return [c for c in calls if not c.startswith("--check")]

    def test_nothing_owed_runs_no_playbook(self):
        code, out, calls = self.run_job()
        self.assertEqual(code, 0)
        self.assertEqual((out["ok"], out["dispatched"], out["due"]["ok"]), (True, [], True))
        self.assertEqual(calls, [])
        self.assertEqual((self.dir / "calls").read_text().split(), ["due", "status", "announcements"])

    def test_a_pick_owed_runs_the_judge_and_a_post_owed_runs_the_poster_once(self):
        self.owe(to_judge=[{"id": 1}], winners=[{"id": 1}], countdown=[{"id": 2}], closing_soon=[{"id": 3}])
        code, out, calls = self.run_job()
        self.assertEqual(code, 0)
        self.assertEqual(out["dispatched"], ["giveaway-judge", "giveaway-post"])
        self.assertEqual(self.runs(calls), ["giveaway-judge", "giveaway-post"])

    def test_each_kind_of_post_owed_runs_the_poster(self):
        for kind in ("countdown", "winners", "claim_open", "closing_soon"):
            (self.dir / "act-calls").unlink(missing_ok=True)
            self.owe(**{kind: [{"id": 1}]})
            _, out, calls = self.run_job()
            self.assertEqual((out["dispatched"], self.runs(calls)), (["giveaway-post"], ["giveaway-post"]), kind)

    def test_an_open_slot_alone_is_left_to_the_weekly_plan(self):
        self.owe(open=[{"on": True}])
        _, out, calls = self.run_job()
        self.assertEqual((out["dispatched"], calls), ([], []))

    def test_switched_off_only_due_runs(self):
        self.on(False)
        self.owe(to_judge=[{"id": 1}], winners=[{"id": 1}])
        code, out, calls = self.run_job()
        self.assertEqual((code, out["dispatched"], calls), (0, [], []))
        self.assertEqual((self.dir / "calls").read_text().split(), ["due", "status"])

    def test_a_playbook_that_does_not_exist_yet_is_skipped_quietly(self):
        (self.dir / "has-giveaway-post").unlink()
        self.owe(to_judge=[{"id": 1}], winners=[{"id": 1}])
        code, out, calls = self.run_job()
        self.assertEqual(code, 0)
        self.assertEqual((out["dispatched"], out["skipped"]), (["giveaway-judge"], ["giveaway-post"]))
        self.assertEqual(self.runs(calls), ["giveaway-judge"])

    def test_a_failed_playbook_or_due_fails_the_job_and_still_reports(self):
        (self.dir / "fails-giveaway-judge").touch()
        self.owe(to_judge=[{"id": 1}], winners=[{"id": 1}])
        code, out, _ = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual((out["ok"], out["failed"], out["dispatched"]), (False, ["giveaway-judge"], ["giveaway-post"]))
        (self.dir / "due.json").write_text("not json")
        code, out, _ = self.run_job()
        self.assertEqual(code, 1)
        self.assertFalse(out["due"]["ok"])


if __name__ == "__main__":
    unittest.main()
