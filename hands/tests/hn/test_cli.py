"""hands/hn on the command line: the shim, one JSON line, the exit codes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .support import ME, T0, Scratch, comment, story
from teeny_hands.hn import watch

HN = Path(__file__).resolve().parents[2] / "hn"


class CliTest(Scratch):
    def run_hand(self, *argv):
        done = subprocess.run(
            [sys.executable, "-I", "-B", str(HN), *argv],
            input="",
            capture_output=True,
            text=True,
            env=os.environ,
            timeout=60,
        )
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, f"one JSON line, got {done.stdout!r} / {done.stderr!r}")
        return done.returncode, json.loads(lines[0])

    def test_is_an_executable_python_shim(self):
        self.assertTrue(os.access(HN, os.X_OK))
        self.assertTrue(HN.read_text().startswith("#!/usr/bin/env -S python3 -I\n"))

    def test_feeds_item_and_thread(self):
        self.feed("best", [1, 2])
        self.items(story(1, "one", kids=[3]), story(2, "two"), comment(3, 1, "hi &amp; bye"))
        code, out = self.run_hand("best", "--max", "1")
        self.assertEqual((code, out["ok"], [s["title"] for s in out["stories"]]), (0, True, ["one"]))
        code, out = self.run_hand("item", "1")
        self.assertEqual((code, out["item"]["kids"]), (0, [3]))
        code, out = self.run_hand("thread", "1", "--depth", "1", "--max", "5")
        self.assertEqual((code, out["comments"][0]["text"], out["truncated"]), (0, "hi & bye", False))

    def test_user_and_search(self):
        self.user("pg", [5])
        self.hn.route("GET", "/api/v1/search", {"hits": [{"objectID": "9", "_tags": ["story"], "title": "x"}]})
        self.assertEqual(self.run_hand("user", "pg")[1]["user"]["submitted"], 1)
        code, out = self.run_hand("search", "openhuman", "--tags", "story", "--max", "3")
        self.assertEqual((code, out["results"][0]["id"]), (0, 9))
        self.assertEqual(self.hn.requests("GET", "/api/v1/search")[0].query["hitsPerPage"], "3")

    def test_watch_and_inbox(self):
        self.items(story(50, "followed", kids=[51]), comment(51, 50, "new", by="ana", at=T0 + 60))
        code, out = self.run_hand("watch", "add", "50", "--note", "for the crabs")
        self.assertEqual((code, out["watch"]["note"]), (0, "for the crabs"))
        self.assertEqual(self.run_hand("watch", "list")[1]["watches"][0]["item_id"], 50)
        self.at(T0 + 120)
        code, out = self.run_hand("inbox", "--days", "3", "--max", "5")
        self.assertEqual((code, out["added"]), (0, 1))
        self.assertEqual(self.run_hand("watch", "remove", "50")[1]["removed"], 50)

    def test_a_refusal_exits_2_and_errors_exit_1(self):
        for n in range(watch.MAX_WATCHES):
            self.items(story(100 + n))
            watch.add(100 + n)
        self.items(story(200))
        code, out = self.run_hand("watch", "add", "200")
        self.assertEqual((code, out["ok"], out["rule"]), (2, False, "watches"))
        self.assertEqual(self.run_hand()[0], 1)
        self.assertEqual(self.run_hand("top", "--max", "500")[0], 1)
        self.assertEqual(self.run_hand("item", "not-an-id")[0], 1)
        self.assertEqual(self.run_hand("watch", "add")[0], 1)
        self.configure(HN_USERNAME=None)
        watch.db().execute("DELETE FROM hn_watch")
        code, out = self.run_hand("inbox")
        self.assertEqual(code, 1)
        self.assertIn("HN_USERNAME", out["error"])

    def test_an_answer_hacker_news_does_not_give_is_an_error(self):
        self.hn.route("GET", "/v0/item/7.json", {"error": "Permission denied"}, status=401)
        code, out = self.run_hand("item", "7")
        self.assertEqual((code, out["error"]), (1, "Hacker News answered 401: Permission denied"))
        self.assertEqual(ME, "teenydroid")
