"""hands/reddit on the command line: the shim, one JSON line, the exit
codes, and no secret in anything it prints."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .support import ME, PASSWORD, SECRET, TOKEN, Scratch, listing, t3

REDDIT = Path(__file__).resolve().parents[2] / "reddit"


class CliTest(Scratch):
    def run_hand(self, *argv, body=None):
        done = subprocess.run(
            [sys.executable, "-I", "-B", str(REDDIT), *argv],
            input="" if body is None else json.dumps(body),
            capture_output=True, text=True, env=os.environ, timeout=60,
        )
        for secret in (SECRET, PASSWORD, TOKEN):
            self.assertNotIn(secret, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, f"one JSON line, got {done.stdout!r} / {done.stderr!r}")
        return done.returncode, json.loads(lines[0])

    def test_is_an_executable_python_shim(self):
        self.assertTrue(os.access(REDDIT, os.X_OK))
        self.assertTrue(REDDIT.read_text().startswith("#!/usr/bin/env -S python3 -I\n"))

    def test_feed_reads(self):
        self.rd.route("GET", "/r/learnpython/hot", listing(t3("p1")))
        code, out = self.run_hand("feed", "learnpython", "--max", "3")
        self.assertEqual((code, out["posts"][0]["id"]), (0, "p1"))

    def test_a_refusal_exits_2(self):
        code, out = self.run_hand("post", body={"subreddit": "AskReddit", "title": "hi", "body": "there", "anchor": "PR #1"})
        self.assertEqual((code, out["refused"][:6], out["rule"]), (2, "r/AskR", "subreddit"))

    def test_check_says_who_the_token_is_for(self):
        self.rd.route("GET", "/api/v1/me", {"name": ME, "total_karma": 5})
        code, out = self.run_hand("check")
        self.assertEqual((code, out["me"]["name"], out["rules"]["subreddits"]), (0, ME, ["learnpython", "sideproject"]))

    def test_check_refuses_a_token_for_another_account(self):
        self.rd.route("GET", "/api/v1/me", {"name": "somebody_else"})
        code, out = self.run_hand("check")
        self.assertEqual(code, 1)
        self.assertIn("different account", out["error"])

    def test_slot_answers_without_a_body(self):
        code, out = self.run_hand("slot", "--kind", "post")
        self.assertEqual((code, out["open"], out["why"].split(":")[0]), (0, False, "daily_cap"))
        code, out = self.run_hand("slot", "--kind", "comment", "--wait", "5")
        self.assertEqual((code, out["open"]), (0, True))

    def test_rules_shows_the_backoffs_and_clear_backoff_lifts_one(self):
        from teeny_hands.reddit import rules

        rules.start_backoff(rules.db(), "learnpython", "forbidden", "Reddit answered a write with 403")
        code, out = self.run_hand("rules")
        self.assertEqual((code, [b["scope"] for b in out["backoffs"]]), (0, ["learnpython"]))
        self.assertEqual(out["rules"]["backoff_days"], 7)
        code, out = self.run_hand("clear-backoff", "r/learnpython")
        self.assertEqual((code, out["cleared"]), (0, 1))
        self.assertEqual(self.run_hand("rules")[1]["backoffs"], [])

    def test_metrics_and_study_verbs(self):
        code, out = self.run_hand("metrics", "refresh")
        self.assertEqual((code, out["recorded"]), (0, []))
        code, out = self.run_hand("metrics", "list", "--days", "3")
        self.assertEqual((code, out["metrics"]), (0, []))
        self.rd.route("GET", "/r/learnpython/top", listing(t3("a")))
        self.rd.route("GET", "/r/SideProject/top", listing())
        code, out = self.run_hand("study")
        self.assertEqual((code, out["subreddits"]["learnpython"]["top_week"][0]["id"]), (0, "a"))
