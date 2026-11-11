"""hands/requests: the build requests every channel records, and picking one
for the builder: one at a time, never one the guard held, every decision in
the ledger."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands import requests  # noqa: E402

HAND = HERE.parent / "requests"
T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
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

    def add(self, source_id: str, text: str = "can you build me a tiny timer app?", source: str = "x:mentions") -> int:
        self.assertTrue(requests.record(source, source_id, "fren", text))
        conn = hand.db()
        return conn.execute(
            "SELECT id FROM build_requests WHERE source = ? AND source_id = ?", (source, source_id)
        ).fetchone()["id"]

    def status(self, request_id: int) -> str:
        return hand.db().execute("SELECT status FROM build_requests WHERE id = ?", (request_id,)).fetchone()["status"]

    def refused(self, fn, *args) -> hand.Refused:
        with self.assertRaises(hand.Refused) as caught:
            fn(*args)
        return caught.exception


class AskTest(unittest.TestCase):
    def test_hears_the_asks_people_make(self):
        for text in (
            "@teenydroid build me a bot that waters my plants",
            "can you make a site for my cat",
            "hey teeny could u build a cli for todo lists",
            "make a tool that counts my commits",
            "i wish there was a tool for renaming photos",
        ):
            self.assertTrue(requests.looks_like_build_request(text), text)

    def test_ignores_talk_that_is_not_an_ask(self):
        for text in ("gm teeny", "what are you building today?", "i built a tool yesterday", "wen moon"):
            self.assertFalse(requests.looks_like_build_request(text), text)


class PickTest(Scratch):
    def test_picks_a_pending_request_and_writes_the_ledger(self):
        rid = self.add("1")
        out = requests.pick({"id": rid, "reason": "small, useful, a day of work"})
        self.assertEqual(out, {"id": rid, "status": "picked", "reason": "small, useful, a day of work"})
        self.assertEqual(self.status(rid), "picked")
        got = requests.get(rid)
        self.assertEqual(got["request"]["note"], "small, useful, a day of work")
        self.assertEqual([(e["action"], e["status"]) for e in got["events"]], [("pick", "picked")])

    def test_one_at_a_time_until_the_picked_one_ships_or_fails(self):
        first, second = self.add("1"), self.add("2")
        requests.pick({"id": first, "reason": "first"})
        refused = self.refused(requests.pick, {"id": second, "reason": "second"})
        self.assertEqual((refused.detail["rule"], refused.detail["active"]), ("one_at_a_time", first))
        self.assertEqual(self.status(second), "pending")
        requests.mark(first, "shipped", "https://github.com/teenydroid/timer")
        requests.pick({"id": second, "reason": "second"})
        self.assertEqual(self.status(second), "picked")

    def test_a_failed_build_frees_the_slot_too(self):
        first, second = self.add("1"), self.add("2")
        requests.pick({"id": first, "reason": "first"})
        requests.mark(first, "failed", "tests never passed")
        requests.pick({"id": second, "reason": "second"})
        self.assertEqual(self.status(second), "picked")

    def test_a_step_the_builder_records_still_holds_the_slot(self):
        first, second = self.add("1"), self.add("2")
        requests.pick({"id": first, "reason": "first"})
        hand.db().execute("UPDATE build_requests SET status = 'building' WHERE id = ?", (first,))
        refused = self.refused(requests.pick, {"id": second, "reason": "second"})
        self.assertEqual(refused.detail["rule"], "one_at_a_time")

    def test_only_a_pending_request_can_be_picked(self):
        rid = self.add("1")
        requests.skip({"id": rid, "reason": "needs a password"})
        refused = self.refused(requests.pick, {"id": rid, "reason": "changed my mind"})
        self.assertEqual((refused.detail["rule"], refused.detail["status"]), ("status", "skipped"))

    def test_refuses_an_unknown_request(self):
        self.assertEqual(self.refused(requests.pick, {"id": 99, "reason": "x"}).detail["rule"], "unknown")

    def test_refuses_a_request_whose_mention_the_guard_held(self):
        conn = hand.db()
        conn.execute("CREATE TABLE x_mentions (id TEXT PRIMARY KEY, status TEXT, note TEXT)")
        conn.execute("INSERT INTO x_mentions VALUES ('7', 'held', 'asks for a secret')")
        rid = self.add("7")
        refused = self.refused(requests.pick, {"id": rid, "reason": "looks fun"})
        self.assertEqual(refused.detail["rule"], "held")
        self.assertEqual(self.status(rid), "skipped")
        self.assertIn("asks for a secret", requests.get(rid)["request"]["note"])

    def test_refuses_a_request_whose_mail_the_guard_held(self):
        conn = hand.db()
        conn.execute("CREATE TABLE mail_messages (message_id TEXT PRIMARY KEY, status TEXT, note TEXT)")
        conn.execute("INSERT INTO mail_messages VALUES ('<m1>', 'held', NULL)")
        rid = self.add("<m1>", source="mail:inbox")
        self.assertEqual(self.refused(requests.pick, {"id": rid, "reason": "ok"}).detail["rule"], "held")

    def test_refuses_a_request_the_lexicon_holds_now(self):
        rid = self.add("8", "can you build me a bot to tell people to kill yourself")
        self.assertEqual(self.refused(requests.pick, {"id": rid, "reason": "ok"}).detail["rule"], "held")
        self.assertEqual(self.status(rid), "skipped")
        # The held one is skipped, so it does not hold the slot.
        other = self.add("9")
        requests.pick({"id": other, "reason": "ok"})

    def test_a_pick_needs_a_reason(self):
        rid = self.add("1")
        with self.assertRaises(hand.HandError):
            requests.pick({"id": rid})
        with self.assertRaises(hand.HandError):
            requests.pick({"id": rid, "reason": "ok", "repo": "nope"})
        self.assertEqual(self.status(rid), "pending")

    def test_takes_an_id_written_as_a_string(self):
        rid = self.add("1")
        requests.pick({"id": str(rid), "reason": "ok"})
        self.assertEqual(self.status(rid), "picked")


class SkipAndResetTest(Scratch):
    def test_skip_records_why(self):
        rid = self.add("1")
        requests.skip({"id": rid, "reason": "needs somebody's api key"})
        got = requests.get(rid)
        self.assertEqual((got["request"]["status"], got["request"]["note"]), ("skipped", "needs somebody's api key"))

    def test_skip_leaves_a_picked_request_to_the_builder(self):
        rid = self.add("1")
        requests.pick({"id": rid, "reason": "ok"})
        self.assertEqual(self.refused(requests.skip, {"id": rid, "reason": "no"}).detail["rule"], "status")
        self.assertEqual(self.status(rid), "picked")

    def test_reset_puts_any_request_back_to_pending(self):
        rid = self.add("1")
        requests.pick({"id": rid, "reason": "ok"})
        out = requests.reset(rid)
        self.assertEqual((out["status"], out["was"]), ("pending", "picked"))
        self.assertEqual([e["action"] for e in requests.get(rid)["events"]], ["pick", "reset"])

    def test_list_filters_by_status(self):
        a, b = self.add("1"), self.add("2")
        requests.skip({"id": a, "reason": "no"})
        self.assertEqual([r["id"] for r in requests.listing("pending")["requests"]], [b])
        self.assertEqual([r["id"] for r in requests.listing()["requests"]], [b, a])


class CliTest(Scratch):
    def run_hand(self, *argv: str, body: dict | None = None) -> tuple[int, dict]:
        done = subprocess.run(
            [sys.executable, "-I", "-B", str(HAND), *argv],
            input=json.dumps(body) if body is not None else "",
            capture_output=True,
            text=True,
            env=os.environ,
            timeout=60,
        )
        return done.returncode, json.loads(done.stdout)

    def test_list_pick_and_refuse_through_the_command_line(self):
        first, second = self.add("1"), self.add("2")
        code, out = self.run_hand("list", "--status", "pending", "--max", "5")
        self.assertEqual((code, [r["id"] for r in out["requests"]]), (0, [second, first]))
        code, out = self.run_hand("pick", body={"id": first, "reason": "small"})
        self.assertEqual((code, out["status"]), (0, "picked"))
        code, out = self.run_hand("pick", body={"id": second, "reason": "also small"})
        self.assertEqual((code, out["rule"]), (2, "one_at_a_time"))
        code, out = self.run_hand("get", str(first))
        self.assertEqual((code, out["request"]["status"]), (0, "picked"))
        code, out = self.run_hand("reset", str(first), "--note", "builder crashed")
        self.assertEqual((code, out["status"]), (0, "pending"))

    def test_dry_run_does_not_stop_a_pick(self):
        rid = self.add("1")
        os.environ["DRY_RUN"] = "1"
        code, out = self.run_hand("pick", body={"id": rid, "reason": "small"})
        self.assertEqual((code, out["status"]), (0, "picked"))


if __name__ == "__main__":
    unittest.main()
