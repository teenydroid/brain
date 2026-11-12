"""hands/moltbook on the command line: the shim, one JSON line, the exit
codes, and no key in anything it prints."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .support import HOUR, KEY, T0, Scratch

MOLTBOOK = Path(__file__).resolve().parents[2] / "moltbook"


class CliTest(Scratch):
    def run_hand(self, *argv, body=None, key_may_print=False):
        done = subprocess.run(
            [sys.executable, "-I", "-B", str(MOLTBOOK), *argv],
            input="" if body is None else json.dumps(body),
            capture_output=True,
            text=True,
            env=os.environ,
            timeout=60,
        )
        if not key_may_print:
            self.assertNotIn(KEY, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, f"one JSON line, got {done.stdout!r} / {done.stderr!r}")
        return done.returncode, json.loads(lines[0])

    def test_is_an_executable_python_shim(self):
        self.assertTrue(os.access(MOLTBOOK, os.X_OK))
        self.assertTrue(MOLTBOOK.read_text().startswith("#!/usr/bin/env -S python3 -I\n"))

    def test_post_id_reads_and_post_with_a_body_publishes(self):
        self.mb.route("GET", "/posts/p1", {"success": True, "post": {"id": "p1", "title": "hi"}})
        code, out = self.run_hand("post", "p1")
        self.assertEqual((code, out["post"]["title"]), (0, "hi"))
        self.configure(DRY_RUN="1")
        code, out = self.run_hand("post", body={"submolt": "general", "title": "hello"})
        self.assertEqual((code, out["dry_run"], out["would_post"]["title"]), (0, True, "hello"))

    def test_home_expand_attaches_a_mentions_post(self):
        self.mb.route("GET", "/home", {"success": True, "activity_on_your_posts": [
            {"post_id": "m1", "post_title": "@teenydroid help", "preview": "You were mentioned in a post"},
        ]})
        self.mb.route("GET", "/posts/m1", {"success": True, "post": {"id": "m1", "content": "why?", "author": {"name": "crabby"}}})
        self.mb.route("GET", "/posts/m1/comments", {"success": True, "comments": []})
        code, out = self.run_hand("home", "--expand")
        self.assertEqual((code, out["home"]["activity_on_your_posts"][0]["post"]["content"]), (0, "why?"))

    def test_a_refusal_exits_2(self):
        code, out = self.run_hand("post", body={"submolt": "general", "title": "a collab with u/crabby"})
        self.assertEqual((code, out["ok"], out["rule"]), (2, False, "collab"))

    def test_errors_exit_1(self):
        self.assertEqual(self.run_hand()[0], 1)
        self.assertEqual(self.run_hand("comment", body={"post_id": "x"})[0], 1)
        self.mb.route("GET", "/agents/me", {"success": False, "error": "Invalid API key"}, status=401)
        code, out = self.run_hand("whoami")
        self.assertEqual((code, out["error"]), (1, "Moltbook answered 401: Invalid API key"))

    def test_collab_and_pending(self):
        code, out = self.run_hand("collab", "add", "crabby", "--note", "agreed in a DM")
        self.assertEqual((code, out["collab"]), (0, "crabby"))
        self.assertEqual(self.run_hand("collab", "add", "shelly")[0], 1)
        code, out = self.run_hand("collab", "list")
        self.assertEqual([c["name"] for c in out["collabs"]], ["crabby"])
        code, out = self.run_hand("pending")
        self.assertEqual((code, out["consecutive_failures"], out["lockout_after"]), (0, 0, 3))
        self.assertEqual(self.run_hand("unlock")[1]["unlocked"], True)

    def test_metrics_refresh_and_list(self):
        from teeny_hands.moltbook import rules

        rules.db().execute(
            "INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES ('p1', ?, 'general', 't', 'verified')",
            (T0,),
        )
        self.mb.route("GET", "/posts/p1", {"success": True, "post": {"id": "p1", "score": 6, "comment_count": 2}})
        self.at(T0 + 2 * HOUR)
        code, out = self.run_hand("metrics", "refresh")
        self.assertEqual((code, out["recorded"]), (0, [{"kind": "post", "target_id": "p1", "checkpoint": 1}]))
        code, out = self.run_hand("metrics", "list", "--days", "3")
        self.assertEqual((code, out["days"], out["metrics"][0]["score"]), (0, 3, 6))
        self.assertEqual(self.run_hand("metrics")[0], 1)

    def test_register_prints_the_key_on_stdout_only(self):
        self.configure(MOLTBOOK_API_KEY=None)
        self.mb.route("POST", "/agents/register", {"agent": {"api_key": KEY, "claim_url": "c", "verification_code": "v"}})
        code, out = self.run_hand("register", "--name", "teenydroid", "--description", "codes in public", key_may_print=True)
        self.assertEqual((code, out["api_key"]), (0, KEY))
        self.assertEqual(self.leaked(), [])
