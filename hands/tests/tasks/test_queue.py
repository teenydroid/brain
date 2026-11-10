"""hands/tasks: adding only on the allowed websites, claiming in order,
stale claims, finishing with every outcome, and screenshots copied under
state/media/ where hands/x upload takes them."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parent.parent / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from teeny_hands import hand, websites  # noqa: E402
from teeny_hands.tasks import queue  # noqa: E402

REPO = HERE.parents[2]
TASKS = REPO / "hands" / "tasks"
T0 = 1_789_369_200.0
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        (self.root / "deploy").mkdir()
        shutil.copy(REPO / "deploy" / "browser-allow.txt", self.root / "deploy" / "browser-allow.txt")
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = str(T0)
        os.environ.pop("DRY_RUN", None)
        hand._ENV_CACHE = None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def add(self, **more):
        body = {"goal": "find the issue count", "start_url": "https://github.com/teenydroid", "done_when": "you know it",
                **more}
        return queue.add(body, source="test")


class AddTest(Scratch):
    def test_queues_a_task_on_an_allowed_website(self):
        out = self.add(kind="research")
        self.assertEqual(out["status"], "pending")
        row = queue.listing()["tasks"][0]
        self.assertEqual((row["kind"], row["source"], row["start_url"]), ("research", "test", "https://github.com/teenydroid"))

    def test_subdomains_of_an_allowed_host_are_allowed(self):
        self.add(start_url="https://gist.github.com/x")

    def test_refuses_anywhere_else(self):
        for url in ("https://example.com", "https://github.com.evil.example/", "ftp://github.com/",
                    "javascript:alert(1)", "https://user:pw@github.com/", "https://notgithub.com/"):
            with self.subTest(url=url), self.assertRaises(hand.Refused) as caught:
                self.add(start_url=url)
            self.assertEqual(caught.exception.detail["rule"], "website")

    def test_bad_bodies(self):
        for bad in (None, {}, {"goal": "g", "start_url": "https://github.com"},
                    {"goal": "", "start_url": "https://github.com", "done_when": "d"},
                    {"goal": "g", "start_url": "https://github.com", "done_when": "d", "kind": "Bad Kind"},
                    {"goal": "g", "start_url": "https://github.com", "done_when": "d", "extra": 1}):
            with self.subTest(body=bad), self.assertRaises(hand.HandError):
                queue.add(bad)

    def test_a_full_queue_refuses(self):
        for _ in range(queue.MAX_PENDING):
            self.add()
        with self.assertRaises(hand.Refused) as caught:
            self.add()
        self.assertEqual(caught.exception.detail["rule"], "queue_full")


class ClaimTest(Scratch):
    def test_claims_the_oldest_and_resumes_its_own_claim(self):
        first = self.add(goal="one")["task_id"]
        self.add(goal="two")
        out = queue.next_task()["task"]
        self.assertEqual((out["task_id"], out["goal"], out["resumed"]), (first, "one", False))
        self.assertTrue(Path(out["screenshot_dir"]).is_dir())
        self.assertEqual(Path(out["screenshot_dir"]), self.root / "state" / "media" / "tasks" / str(first))
        again = queue.next_task()["task"]
        self.assertEqual((again["task_id"], again["resumed"]), (first, True))

    def test_nothing_waiting_is_an_answer(self):
        self.assertEqual(queue.next_task(), {"task": None, "reason": "no task is waiting"})

    def test_a_stale_claim_goes_back_once_then_fails(self):
        tid = self.add()["task_id"]
        queue.next_task()
        os.environ["TEENY_NOW"] = str(T0 + queue.STALE_CLAIM_SECONDS + 1)
        self.assertEqual(queue.next_task()["task"]["task_id"], tid)  # back to pending, claimed again
        os.environ["TEENY_NOW"] = str(T0 + 2 * queue.STALE_CLAIM_SECONDS + 2)
        self.assertIsNone(queue.next_task()["task"])
        self.assertEqual(queue.listing("failed")["tasks"][0]["id"], tid)


class DoneTest(Scratch):
    def claimed(self):
        self.add()
        return queue.next_task()["task"]

    def test_each_outcome(self):
        for outcome in queue.OUTCOMES:
            with self.subTest(outcome=outcome):
                task = self.claimed()
                out = queue.done({"task_id": task["task_id"], "outcome": outcome, "notes": "n"})
                self.assertEqual(out["status"], outcome)

    def test_a_payment_checkpoint_ends_as_needs_human(self):
        task = self.claimed()
        queue.done({"task_id": task["task_id"], "outcome": "needs_human", "notes": "the page asked for a card"})
        self.assertEqual(queue.listing("needs_human")["tasks"][0]["notes"], "the page asked for a card")

    def test_screenshots_are_copied_under_state_media(self):
        task = self.claimed()
        downloads = self.root / "state" / "browser" / "downloads"
        downloads.mkdir(parents=True)
        (downloads / "page.png").write_bytes(PNG)
        own = Path(task["screenshot_dir"]) / "own.png"
        own.write_bytes(PNG)
        out = queue.done({"task_id": task["task_id"], "outcome": "ok", "notes": "done",
                          "screenshots": ["state/browser/downloads/page.png", str(own)]})
        tid = task["task_id"]
        self.assertEqual(out["screenshots"], [f"state/media/tasks/{tid}/page.png", f"state/media/tasks/{tid}/own.png"])
        self.assertEqual((self.root / out["screenshots"][0]).read_bytes(), PNG)

    def test_screenshots_from_anywhere_else_are_refused(self):
        task = self.claimed()
        (self.root / "secrets" / "x.png").write_bytes(PNG)
        media = self.root / "state" / "media"
        (media / "notes.txt").write_text("x")
        for path, rule in (("secrets/x.png", "screenshot_path"), ("state/media/../../secrets/x.png", "screenshot_path"),
                           ("state/media/notes.txt", "screenshot_type")):
            with self.subTest(path=path), self.assertRaises(hand.Refused) as caught:
                queue.done({"task_id": task["task_id"], "outcome": "ok", "notes": "n", "screenshots": [path]})
            self.assertEqual(caught.exception.detail["rule"], rule)
        self.assertEqual(queue.listing("claimed")["tasks"][0]["id"], task["task_id"])

    def test_only_a_claimed_task_and_only_once(self):
        task = self.claimed()
        queue.done({"task_id": task["task_id"], "outcome": "failed", "notes": "n"})
        with self.assertRaises(hand.Refused):
            queue.done({"task_id": task["task_id"], "outcome": "ok", "notes": "n"})
        with self.assertRaises(hand.Refused):
            queue.done({"task_id": 99, "outcome": "ok", "notes": "n"})

    def test_bad_bodies(self):
        task = self.claimed()
        good = {"task_id": task["task_id"], "outcome": "ok", "notes": "n"}
        for bad in ({**good, "outcome": "paid"}, {**good, "notes": ""}, {**good, "task_id": "1"},
                    {**good, "screenshots": "a.png"}, {**good, "screenshots": ["x.png"] * 11}, {**good, "x": 1}):
            with self.subTest(body=bad), self.assertRaises(hand.HandError):
                queue.done(bad)


class CliTest(Scratch):
    def run_tasks(self, *argv, body=None):
        return subprocess.run([sys.executable, str(TASKS), *argv], capture_output=True, text=True,
                              input=json.dumps(body) if body is not None else "")

    def test_the_contract(self):
        added = self.run_tasks("add", "--source", "playbook:demo",
                               body={"goal": "g", "start_url": "https://pypi.org/project/x", "done_when": "d"})
        self.assertEqual(added.returncode, 0, added.stderr)
        refused = self.run_tasks("add", body={"goal": "g", "start_url": "https://example.com", "done_when": "d"})
        self.assertEqual(refused.returncode, 2)
        listed = json.loads(self.run_tasks("list", "--status", "pending").stdout)
        self.assertEqual(listed["tasks"][0]["source"], "playbook:demo")


class WebsitesTest(unittest.TestCase):
    def test_the_repository_list_parses_and_has_what_teeny_uses(self):
        hosts = websites.load(REPO)
        for host in ("github.com", "api.github.com", "moltbook.com", "agentmail.to", "solscan.io", "pypi.org"):
            self.assertIn(host, hosts)

    def test_a_typo_or_a_wildcard_is_an_error(self):
        for text in ("*\n", "github.com\n*.example.com\n", "https://github.com\n", "# only comments\n", ""):
            with self.subTest(text=text), self.assertRaises(websites.BadList):
                websites.parse(text)
