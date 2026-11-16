"""hands/reddit on the command line: the shim, one JSON line, the exit
codes, flags and a JSON body on stdin alike."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .support import COMMENT, THREAD, Scratch

REDDIT = Path(__file__).resolve().parents[2] / "reddit"


class CliTest(Scratch):
    def run_hand(self, *argv, body=None):
        done = subprocess.run(
            [sys.executable, "-I", "-B", str(REDDIT), *argv],
            input="" if body is None else json.dumps(body),
            capture_output=True, text=True, env=os.environ, timeout=60,
        )
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, f"one JSON line, got {done.stdout!r} / {done.stderr!r}")
        return done.returncode, json.loads(lines[0])

    def test_is_an_executable_python_shim(self):
        self.assertTrue(os.access(REDDIT, os.X_OK))
        self.assertTrue(REDDIT.read_text().startswith("#!/usr/bin/env -S python3 -I\n"))
        self.assertEqual(len(REDDIT.read_text().splitlines()), 6)

    def test_reads_and_a_refusal(self):
        code, out = self.run_hand("status")
        self.assertEqual((code, out["ok"], out["account"]), (0, True, None))
        code, out = self.run_hand("comment", "--thread", THREAD, body={"body": "hi"})
        self.assertEqual((code, out["ok"], out["rule"]), (2, False, "no_account"))
        code, out = self.run_hand("comment", body={"thread": THREAD})
        self.assertEqual((code, out["ok"]), (1, False))
        self.assertEqual(self.run_hand()[0], 1)

    def test_flags_and_a_body_both_queue(self):
        self.ready()
        self.seen(THREAD)
        code, out = self.run_hand("comment", "--thread", THREAD, body={"body": "a helpful comment"})
        self.assertEqual((code, out["kind"]), (0, "reddit_comment"))
        code, out = self.run_hand("reply", body={"comment": COMMENT, "body": "an answer"})
        self.assertEqual((code, out["kind"]), (0, "reddit_reply"))
        code, out = self.run_hand("upvote", THREAD)
        self.assertEqual((code, out["kind"]), (0, "reddit_vote"))
        code, out = self.run_hand("subscribe", "LLMDevs")
        self.assertEqual((code, out["kind"]), (0, "reddit_subscribe"))
        code, out = self.run_hand("browse", "--subreddit", "LLMDevs", "--sort", "new")
        self.assertEqual((code, out["start_url"]), (0, "https://old.reddit.com/r/LLMDevs/new/"))
        code, out = self.run_hand("due")
        self.assertEqual((code, out["due"]), (0, {"subreddit": "AI_Agents"}))
        self.assertEqual(len(self.tasks()), 5)

    def test_under_dry_run_the_playbook_body_says_what_would_be_queued(self):
        self.configure(DRY_RUN="1")
        code, out = self.run_hand("browse", body={"subreddit": "AI_Agents"})
        self.assertEqual((code, out["dry_run"], out["would_queue"]["kind"]), (0, True, "reddit_browse"))
        self.assertEqual(self.tasks(), [])
