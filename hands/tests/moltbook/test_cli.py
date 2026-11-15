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

    def test_unanswered_and_decline_on_the_command_line(self):
        code, out = self.run_hand("unanswered")
        self.assertEqual((code, out["unanswered"], out["comments"]), (0, 0, []))
        code, out = self.run_hand("decline", body={"comment_id": "c1", "reason": "spam"})
        self.assertEqual((code, out["rule"]), (2, "not_owed"))
        self.assertEqual(self.run_hand("decline", body={"comment_id": "c1", "reason": "busy"})[0], 1)

    def test_post_slot_is_a_read_that_says_open_or_why(self):
        self.configure(DRY_RUN="1")
        code, out = self.run_hand("post-slot", "--wait", "0")
        self.assertEqual((code, out["open"]), (0, True))
        self.assertEqual(self.run_hand("post-slot", "--wait", "9999")[0], 1)

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
        code, out = self.run_hand("metrics", "daily")
        self.assertEqual((code, out["metrics"]["live_posts_24h"], out["metrics"]["own_thread_unanswered"]), (0, 1.0, 0.0))

    def test_study(self):
        self.mb.route("GET", "/posts", {"success": True, "posts": [
            {"id": "p1", "title": "x is a y", "score": 5, "created_at": "2026-09-14T06:00:00Z", "content": "c"}]})
        for name in ("agents", "memory", "builds", "tooling", "infrastructure", "security"):
            self.mb.route("GET", f"/submolts/{name}/feed", {"success": True, "posts": []})
        code, out = self.run_hand("study")
        self.assertEqual((code, [p["title"] for p in out["top"]], out["own"]), (0, ["x is a y"], []))

    def test_a_filtered_feed_and_the_peers(self):
        young, old = "2026-09-14T06:30:00Z", "2026-09-12T06:30:00Z"  # T0 is 07:00Z on the 14th
        self.mb.route("GET", "/submolts/general/feed", {"success": True, "posts": [
            {"id": "g1", "author": {"name": "crabby", "karma": 40}, "comment_count": 1, "created_at": young},
            {"id": "g2", "author": {"name": "whale", "karma": 90000}, "comment_count": 1, "created_at": young},
            {"id": "g3", "author": {"name": "crabby", "karma": 40}, "comment_count": 1, "created_at": old}]})
        self.mb.route("GET", "/submolts/memory/feed", {"success": True, "posts": [
            {"id": "m1", "author": {"name": "shelly", "karma": 9}, "comment_count": 0, "created_at": young}]})
        code, out = self.run_hand("feed", "--submolt", "general", "--submolt", "memory", "--sort", "new", "--brief",
                                  "--max-age-hours", "3", "--max-author-karma", "10000", "--max-comments", "5", "--skip-mine")
        self.assertEqual((code, sorted(p["id"] for p in out["posts"])), (0, ["g1", "m1"]))
        self.assertEqual(self.run_hand("feed", "--max-age-hours", "0")[0], 1)  # a usage error
        (self.root / "brain").mkdir()
        (self.root / "brain" / "moltbook-peers.txt").write_text("lilt\n")
        self.mb.route("GET", "/posts", {"success": True, "posts": [
            {"id": "l1", "author": {"name": "lilt", "karma": 11183}, "created_at": young},
            {"id": "l2", "author": {"name": "lilt", "karma": 11183}, "created_at": old}]})
        code, out = self.run_hand("peers", "--max-age-hours", "3", "--skip-mine")
        self.assertEqual((code, out["peers"], [p["id"] for p in out["posts"]]), (0, ["lilt"], ["l1"]))

    def test_register_prints_the_key_on_stdout_only(self):
        self.configure(MOLTBOOK_API_KEY=None)
        self.mb.route("POST", "/agents/register", {"agent": {"api_key": KEY, "claim_url": "c", "verification_code": "v"}})
        code, out = self.run_hand("register", "--name", "teenydroid", "--description", "codes in public", key_may_print=True)
        self.assertEqual((code, out["api_key"]), (0, KEY))
        self.assertEqual(self.leaked(), [])


class PruneJobTest(Scratch):
    """bin/moltbook-prune, the weekly pinned job, calls `hands/moltbook prune`."""

    def test_the_weekly_job_unfollows_who_does_not_follow_back(self):
        import tomllib
        from teeny_hands import hand
        from teeny_hands.moltbook import rules

        repo = MOLTBOOK.parents[1]
        pinned = tomllib.loads((repo / "evals" / "cron.pinned.toml").read_text())["jobs"]["moltbook-prune"]
        self.assertEqual((pinned["command"], pinned["schedule"]), ("bin/moltbook-prune", "30 17 * * 6"))
        conn = rules.db()
        conn.execute("INSERT INTO moltbook_actions (at, verb, target) VALUES (?, 'follow', 'quiet')", (T0 - 9 * 86400,))
        self.mb.route("GET", "/agents/profile", {"success": True, "agent": {"name": "quiet", "follows_you": False}})
        self.mb.route("DELETE", "/agents/quiet/follow", {"success": True})
        env = {**os.environ, "TEENY_HANDS": str(MOLTBOOK.parent)}
        done = subprocess.run([str(repo / "bin" / "moltbook-prune")], capture_output=True, text=True, env=env,
                              timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout.splitlines()[-1])["unfollowed"], ["quiet"])
        self.assertEqual(len(self.mb.requests("DELETE", "/agents/quiet/follow")), 1)
