"""hands/x through the command line: the verbs, the contract's JSON and exit
codes, and no secret on stdout or stderr. Every case runs the real
executable as a subprocess against the fake X.

The guard is the other hand's. Verbs that need it run a copy of the shim over
a scratch lib/ holding the real teeny_hands.x and a stand-in guard, so these
tests pin hands/x whatever state hands/guard is in. The repo's own
teeny_hands/guard/ is never touched."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .support import LIB, SECRETS, T0, Scratch, me

SHIM = LIB.parent / "x"

FAKE_GATE = '''
from dataclasses import dataclass

@dataclass
class Verdict:
    kind: str
    text: str
    reason: str
    category: str | None = None
    hold_id: int | None = None
    judge_failed: bool = False

def check_outbound(text, *, surface="x", kind="post", replying_to=None, max_emoji=None, context=None):
    if "$FAKE" in text:
        return Verdict("refuse", text, "names a ticker that is not ours")
    if "hold me" in text:
        return Verdict("hold", text, "judge", category="politics", hold_id=7)
    return Verdict("allow", text.replace(" -- ", ", "), "")

def inbound(text, *, surface="x", context=None):
    return Verdict("allow", text, "")
'''


class Cli(Scratch):
    def setUp(self):
        super().setUp()
        self.at(T0)
        me(self.x, "1000")

    def guarded_shim(self) -> Path:
        """hands/x over a scratch lib/ with the stand-in guard."""
        hands = self.root / "scratch-hands"
        shim = hands / "x"
        if shim.exists():
            return shim
        lib = hands / "lib" / "teeny_hands"
        (lib / "guard").mkdir(parents=True)
        shutil.copy(LIB / "teeny_hands" / "__init__.py", lib / "__init__.py")
        (lib / "hand.py").symlink_to(LIB / "teeny_hands" / "hand.py")
        (lib / "tags.py").symlink_to(LIB / "teeny_hands" / "tags.py")
        (lib / "x").symlink_to(LIB / "teeny_hands" / "x", target_is_directory=True)
        (lib / "guard" / "__init__.py").write_text("")
        (lib / "guard" / "gate.py").write_text(FAKE_GATE)
        # The gate is the stand-in; the repeat measure the posting rules use is the real one.
        (lib / "guard" / "repeats.py").symlink_to(LIB / "teeny_hands" / "guard" / "repeats.py")
        shutil.copy(SHIM, shim)
        return shim

    def run_x(self, *argv: str, stdin: str | None = None, guarded: bool = False) -> tuple[int, dict, str]:
        shim = self.guarded_shim() if guarded else SHIM
        done = subprocess.run(
            [str(shim), *argv], input=stdin, capture_output=True, text=True, env=os.environ, timeout=60
        )
        for secret in SECRETS.values():
            self.assertNotIn(secret, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, done.stdout + done.stderr)
        return done.returncode, json.loads(lines[0]), done.stderr


class ShimTest(Cli):
    def test_the_shim_is_executable_and_runs_python_isolated(self):
        self.assertTrue(os.access(SHIM, os.X_OK))
        self.assertTrue(SHIM.read_text().startswith("#!/usr/bin/env -S python3 -I\n"))

    def test_no_verb_is_an_error(self):
        code, out, _ = self.run_x()
        self.assertEqual(code, 1)
        self.assertFalse(out["ok"])


class ReadVerbsTest(Cli):
    def test_whoami(self):
        code, out, _ = self.run_x("whoami")
        self.assertEqual((code, out), (0, {"ok": True, "id": "1000", "username": "teenydroid"}))

    def test_get(self):
        self.x.route("GET", "/2/tweets/77", {"data": {"id": "77", "text": "hello", "author_id": "42"}})
        code, out, _ = self.run_x("get", "77")
        self.assertEqual((code, out), (0, {"ok": True, "post": {"id": "77", "text": "hello", "authorId": "42"}}))
        self.run_x("get", "77")
        self.assertEqual(len(self.x.requests("GET", "/2/tweets/77")), 1, "the second read is the cache's")

    def test_get_a_post_x_does_not_have(self):
        self.x.route("GET", "/2/tweets/404", {"errors": [{"title": "Not Found Error"}]})
        self.assertEqual(self.run_x("get", "404")[:2], (0, {"ok": True, "post": None}))

    def test_thread_reads_the_post_s_conversation(self):
        self.x.route("GET", "/2/tweets/901", {"data": {"id": "901", "text": "r", "conversation_id": "900"}})
        self.x.route("GET", "/2/tweets/search/recent", {"data": [{"id": "901", "text": "r"}, {"id": "902", "text": "s"}]})
        code, out, _ = self.run_x("thread", "901", "--max", "50")
        self.assertEqual(code, 0)
        self.assertEqual(out["conversationId"], "900")
        self.assertEqual(out["post"]["id"], "901")
        self.assertEqual([p["id"] for p in out["posts"]], ["901", "902"])
        q = self.x.requests("GET", "/2/tweets/search/recent")[0].query
        self.assertEqual((q["query"], q["max_results"]), ("conversation_id:900", "50"))

    def test_user(self):
        self.x.route("GET", "/2/users/by/username/stonks", {"data": {"id": "9", "username": "stonks"}})
        self.assertEqual(self.run_x("user", "@stonks")[:2], (0, {"ok": True, "user": {"id": "9", "username": "stonks"}}))

    def test_search_uses_the_configured_max_unless_told(self):
        self.configure(X_SEARCH_MAX_RESULTS="25")
        self.x.route("GET", "/2/tweets/search/recent", {"data": [{"id": "1", "text": "a"}]})
        code, out, _ = self.run_x("search", "$TINY -is:retweet")
        self.assertEqual((code, [p["id"] for p in out["posts"]]), (0, ["1"]))
        self.run_x("search", "other", "--max", "40")
        maxes = [s.query["max_results"] for s in self.x.requests("GET", "/2/tweets/search/recent")]
        self.assertEqual(maxes, ["25", "40"])

    def test_an_x_error_is_exit_1_with_the_status_and_no_url(self):
        self.x.route("GET", "/2/tweets/5", {"title": "Unauthorized"}, status=401)
        code, out, stderr = self.run_x("get", "5")
        self.assertEqual(code, 1)
        self.assertEqual(out, {"ok": False, "error": "X answered 401: Unauthorized"})
        self.assertNotIn("127.0.0.1", json.dumps(out) + stderr)


class MentionVerbsTest(Cli):
    def test_ingest_queues_and_mentions_lists_and_mark_records(self):
        self.x.route("GET", "/2/users/1000/mentions", {"data": [{"id": "11", "text": "gm", "author_id": "8"}]})
        code, out, _ = self.run_x("ingest", "--no-offer", guarded=True)
        self.assertEqual((code, out["read"], out["offered"]), (0, 1, 0))
        code, out, _ = self.run_x("mentions", "--status", "pending")
        self.assertEqual((code, [m["id"] for m in out["mentions"]]), (0, ["11"]))
        self.assertEqual(self.run_x("mark", "11", "--status", "drafted")[:2], (0, {"ok": True, "id": "11", "status": "drafted"}))
        self.assertEqual(self.run_x("mark", "12", "--status", "drafted")[0], 2)


class WriteVerbsTest(Cli):
    def test_post_under_dry_run_says_what_would_go_out_and_asks_x_nothing(self):
        self.configure(DRY_RUN="1")
        self.x.seen.clear()
        code, out, _ = self.run_x("post", stdin='{"text":"hi"}', guarded=True)
        self.assertEqual((code, out), (0, {"ok": True, "dry_run": True, "would_post": "hi", "kind": "post"}))
        self.assertEqual(self.x.requests(), [])

    def test_post_live_publishes_the_guard_s_text(self):
        self.x.route("POST", "/2/tweets", {"data": {"id": "100"}}, status=201)
        code, out, _ = self.run_x("post", stdin='{"text":"gm -- frens"}', guarded=True)
        self.assertEqual((code, out), (0, {"ok": True, "id": "100", "kind": "post", "text": "gm, frens"}))
        self.assertEqual(self.x.requests("POST", "/2/tweets")[0].json(), {"text": "gm, frens"})

    def test_a_refusal_is_exit_2_with_the_reason(self):
        code, out, _ = self.run_x("post", stdin='{"text":"buy $FAKE"}', guarded=True)
        self.assertEqual((code, out), (2, {"ok": False, "refused": "names a ticker that is not ours", "rewrite": True}))
        code, out, _ = self.run_x("post", stdin='{"text":"hold me"}', guarded=True)
        self.assertEqual((code, out), (2, {"ok": False, "refused": "held", "category": "politics", "hold_id": 7}))
        self.assertEqual(self.x.writes(), [])

    def test_through_the_repo_s_own_lib_without_a_guard_nothing_is_published(self):
        # Whatever hands/guard holds right now: absent, the hand fails closed;
        # present, it is the real guard, which this test does not fake.
        self.configure(DRY_RUN="1")
        code, out, _ = self.run_x("post", stdin='{"text":"hi"}')
        if not (LIB / "teeny_hands" / "guard" / "gate.py").exists():
            self.assertEqual(code, 1)
            self.assertIn("guard", out["error"])
        self.assertEqual(self.x.writes(), [])

    def test_reply(self):
        self.x.route("GET", "/2/tweets/5", {"data": {"id": "5", "text": "gm", "author_id": "42"}})
        self.x.route("POST", "/2/tweets", {"data": {"id": "99"}}, status=201)
        code, out, _ = self.run_x("reply", "--to", "5", stdin='{"text":"hi"}', guarded=True)
        self.assertEqual((code, out), (0, {"ok": True, "id": "99", "in_reply_to": "5", "kind": "reply", "text": "hi"}))
        code, out, _ = self.run_x("reply", "--to", "5", stdin='{"text":"again"}', guarded=True)
        self.assertEqual((code, out["refused"].startswith("already replied")), (2, True))

    def test_like(self):
        self.x.route("POST", "/2/users/1000/likes", {"data": {"liked": True}})
        code, out, _ = self.run_x("like", "77")
        self.assertEqual((code, out["rule"]), (2, "likes_off"))
        self.configure(X_LIKES="1")
        self.assertEqual(self.run_x("like", "77")[:2], (0, {"ok": True, "liked": "77"}))

    def test_unsay_previews_then_deletes(self):
        self.x.route("GET", "/2/tweets/55", {"data": {"id": "55", "text": "oops", "author_id": "1000"}})
        self.x.route("DELETE", "/2/tweets/55", {"data": {"deleted": True}})
        code, out, _ = self.run_x("unsay", "55")
        self.assertEqual((code, out["preview"]), (0, True))
        self.assertEqual(self.x.writes(), [])
        self.assertEqual(self.run_x("unsay", "55", "--confirm")[:2], (0, {"ok": True, "id": "55", "deleted": True}))

    def test_upload(self):
        (self.root / "state" / "media").mkdir(parents=True)
        (self.root / "state" / "media" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        self.x.route("POST", "/2/media/upload/initialize", {"data": {"id": "m1"}})
        self.x.route("POST", "/2/media/upload/m1/append", {})
        self.x.route("POST", "/2/media/upload/m1/finalize", {"data": {"id": "m1"}})
        code, out, _ = self.run_x("upload", "--file", "state/media/a.png")
        self.assertEqual((code, out), (0, {"ok": True, "media_id": "m1", "mime": "image/png", "bytes": 16}))
        code, out, _ = self.run_x("upload", "--file", "secrets/teeny.env")
        self.assertEqual((code, out["ok"]), (2, False))


if __name__ == "__main__":
    import unittest

    unittest.main()
