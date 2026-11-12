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

    def test_a_picture_bin_act_attached_goes_on_the_post(self):
        brief_id = briefs.add("show the new home")["brief"]["id"]
        self.x_answers({"id": "1901", "kind": "post", "text": "new home"})
        briefs.post({"id": brief_id, "text": "new home", "media_ids": ["123456"]})
        sent = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(sent[-1]["body"], {"text": "new home", "media_ids": ["123456"]})

    def test_media_ids_must_be_a_list_of_ids(self):
        brief_id = briefs.add("bad media")["brief"]["id"]
        with self.assertRaises(hand.HandError):
            briefs.post({"id": brief_id, "text": "x", "media_ids": "https://example.com/cat.png"})

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

    # --- Moltbook leads: threads and topics the operator scouted -------------------------

    def test_a_moltbook_lead_is_never_the_next_x_brief(self):
        briefs.add("https://www.moltbook.com/post/abc agents arguing about memory compaction", surface="moltbook")
        self.assertEqual(briefs.next_open(), {"brief": None})
        x_brief = briefs.add("the x topic")["brief"]["id"]
        self.assertEqual(briefs.next_open()["brief"]["id"], x_brief)

    def test_leads_are_the_open_moltbook_ones_from_the_last_two_days_newest_first(self):
        old = briefs.add("old thread", surface="moltbook")["brief"]["id"]
        os.environ["TEENY_NOW"] = str(T0 + 47 * 3600)
        first = briefs.add("a reddit thread on agent rate limits", surface="moltbook")["brief"]["id"]
        dropped = briefs.add("never mind", surface="moltbook")["brief"]["id"]
        briefs.cancel(dropped)
        second = briefs.add("https://www.moltbook.com/post/xyz", surface="moltbook")["brief"]["id"]
        briefs.add("an x topic")
        self.assertEqual([lead["id"] for lead in briefs.leads()["leads"]], [second, first, old])
        os.environ["TEENY_NOW"] = str(T0 + 49 * 3600)
        self.assertEqual([lead["id"] for lead in briefs.leads()["leads"]], [second, first])

    def test_a_lead_names_its_surface_in_the_listing(self):
        briefs.add("a thread", surface="moltbook")
        briefs.add("a post topic")
        self.assertEqual([b["surface"] for b in briefs.listing(10)["briefs"]], ["x", "moltbook"])

    def test_an_unknown_surface_is_refused(self):
        with self.assertRaises(hand.Refused):
            briefs.add("hello", surface="reddit")

    def test_a_ledger_from_before_surfaces_is_read_as_x(self):
        conn = hand.db()
        conn.executescript(
            "CREATE TABLE briefs (id INTEGER PRIMARY KEY, text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',"
            " created_at REAL NOT NULL, post_id TEXT, post_text TEXT, done_at REAL);"
            f"INSERT INTO briefs (text, created_at) VALUES ('from before', {T0});"
        )
        conn.close()
        self.assertEqual(briefs.next_open()["brief"]["text"], "from before")
        self.assertEqual(briefs.leads(), {"leads": []})

    def test_the_hand_takes_a_moltbook_lead_from_the_command_line(self):
        env = dict(os.environ)
        done = subprocess.run([str(HAND), "add", "--surface", "moltbook", "a", "thread"], capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        done = subprocess.run([str(HAND), "leads"], capture_output=True, text=True, env=env)
        self.assertEqual([lead["text"] for lead in json.loads(done.stdout)["leads"]], ["a thread"])


if __name__ == "__main__":
    unittest.main()
