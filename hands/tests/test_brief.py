"""hands/brief: the team's told-to post. A brief is posted once, only when X
took the post; a dry run, a refusal or an error leaves it open."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))

from teeny_hands import briefs  # noqa: E402
from teeny_hands import hand  # noqa: E402

HAND = HERE.parent / "brief"
T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z

FAKE_X = """#!/usr/bin/env python3
import json, os, sys
body = json.load(sys.stdin)
with open(os.environ["FAKE_X_LOG"], "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "body": body}) + "\\n")
answer = json.loads(os.environ["FAKE_X_ANSWER"])
print(json.dumps(answer))
sys.exit(int(os.environ.get("FAKE_X_EXIT", "0")))
"""


class BriefTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        hands = self.root / "fake-hands"
        hands.mkdir()
        (hands / "x").write_text(FAKE_X)
        (hands / "x").chmod(0o755)
        self.log = self.root / "x.log"
        self._env = dict(os.environ)
        os.environ.update(
            {
                "TEENY_ROOT": str(self.root),
                "TEENY_NOW": str(T0),
                "TEENY_HANDS": str(hands),
                "FAKE_X_LOG": str(self.log),
            }
        )
        os.environ.pop("DRY_RUN", None)
        hand._ENV_CACHE = None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def x_answers(self, answer, code=0):
        os.environ["FAKE_X_ANSWER"] = json.dumps(answer)
        os.environ["FAKE_X_EXIT"] = str(code)

    def status(self, brief_id):
        return hand.db().execute("SELECT status FROM briefs WHERE id = ?", (brief_id,)).fetchone()["status"]

    def test_next_is_null_with_nothing_queued(self):
        self.assertEqual(briefs.next_open(), {"brief": None})

    def test_next_is_the_oldest_open_brief(self):
        first = briefs.add("talk about the openhuman release")["brief"]["id"]
        briefs.add("second thing")
        self.assertEqual(briefs.next_open(), {"brief": {"id": first, "text": "talk about the openhuman release"}})

    def test_an_empty_brief_is_refused(self):
        with self.assertRaises(hand.Refused):
            briefs.add("   ")

    def test_a_posted_brief_is_marked_and_never_posted_again(self):
        brief_id = briefs.add("say hi to the new holders")["brief"]["id"]
        self.x_answers({"id": "1900", "kind": "post", "text": "hi new holders"})
        out = briefs.post({"id": brief_id, "text": "hi new holders"})
        self.assertTrue(out["marked"])
        self.assertEqual(self.status(brief_id), "posted")
        self.assertEqual(briefs.next_open(), {"brief": None})
        with self.assertRaises(hand.Refused):
            briefs.post({"id": brief_id, "text": "again"})
        sent = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(sent, [{"argv": ["post"], "body": {"text": "hi new holders"}}])

    def test_a_dry_run_leaves_the_brief_open(self):
        brief_id = briefs.add("rehearse this")["brief"]["id"]
        self.x_answers({"ok": True, "dry_run": True, "would_post": "rehearsed", "kind": "post"})
        out = briefs.post({"id": brief_id, "text": "rehearsed"})
        self.assertFalse(out["marked"])
        self.assertEqual(self.status(brief_id), "open")

    def test_a_refusal_from_x_leaves_the_brief_open_and_is_a_refusal(self):
        brief_id = briefs.add("too soon after gm")["brief"]["id"]
        self.x_answers({"ok": False, "refused": "posted 1h ago; the gap is 3h", "rule": "pacing"}, code=2)
        with self.assertRaises(hand.Refused) as caught:
            briefs.post({"id": brief_id, "text": "hello"})
        self.assertIn("gap", caught.exception.reason)
        self.assertEqual(self.status(brief_id), "open")

    def test_an_error_from_x_leaves_the_brief_open(self):
        brief_id = briefs.add("x is down")["brief"]["id"]
        self.x_answers({"ok": False, "error": "X answered 503"}, code=1)
        with self.assertRaises(hand.HandError):
            briefs.post({"id": brief_id, "text": "hello"})
        self.assertEqual(self.status(brief_id), "open")

    def test_a_cancelled_brief_is_never_offered(self):
        brief_id = briefs.add("never mind")["brief"]["id"]
        briefs.cancel(brief_id)
        self.assertEqual(briefs.next_open(), {"brief": None})
        with self.assertRaises(hand.Refused):
            briefs.cancel(brief_id)

    def test_post_needs_an_id_and_text(self):
        with self.assertRaises(hand.Refused):
            briefs.post({"text": "no id"})
        with self.assertRaises(hand.Refused):
            briefs.post({"id": 1, "text": " "})

    def test_the_hand_takes_add_from_the_command_line(self):
        env = dict(os.environ)
        done = subprocess.run([str(HAND), "add", "post", "about", "the", "lab"], capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout)["brief"]["text"], "post about the lab")
        done = subprocess.run([str(HAND), "next"], capture_output=True, text=True, env=env)
        self.assertEqual(json.loads(done.stdout)["brief"]["text"], "post about the lab")

    def test_add_from_a_playbook_draft_cannot_queue_a_brief(self):
        # bin/act pipes a hand: draft to `hands/brief <verb>` with no argument;
        # `add` needs its text as arguments, so a draft cannot queue one.
        done = subprocess.run([str(HAND), "add"], input='{"text": "x"}', capture_output=True, text=True, env=dict(os.environ))
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(briefs.next_open(), {"brief": None})


if __name__ == "__main__":
    unittest.main()
