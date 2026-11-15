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


FAKE_X = """#!/usr/bin/env python3
import json, os, sys
root = os.environ["TEENY_ROOT"]
body = json.loads(sys.stdin.read())
with open(os.path.join(root, "x-calls.jsonl"), "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "body": body}) + "\\n")
if open(os.path.join(root, "x-mode")).read().strip() == "dry":
    print(json.dumps({"ok": True, "dry_run": True, "would_post": body["text"]}))
else:
    print(json.dumps({"ok": True, "id": "5000", "kind": "post"}))
"""


class VoteTest(Scratch):
    """P5: a weekly advisory vote, counted by code, one account one vote; the
    winner becomes the next pick and nothing else."""

    TEXT = "what should i build next? reply with a number. 1: a plant timer. 2: a pomodoro bot. 3: a recipe scaler"

    def setUp(self):
        super().setUp()
        from teeny_hands import votes
        from teeny_hands.x import mentions

        self.votes = votes
        (self.root / "hands").mkdir()
        (self.root / "hands" / "x").write_text(FAKE_X)
        (self.root / "hands" / "x").chmod(0o755)
        (self.root / "x-mode").write_text("live")
        self.ids = [self.add(f"q{i}", f"can you build me thing {i}?") for i in range(4)]
        conn = hand.db()
        conn.executescript(mentions.SCHEMA)
        conn.close()
        self.n = 0

    def at(self, t):
        os.environ["TEENY_NOW"] = str(t)

    def reply(self, author, text, at, status="offered", conversation="5000"):
        self.n += 1
        conn = hand.db()
        conn.execute("INSERT INTO x_mentions (id, author_id, author_username, conversation_id, text, post, status, seen_at, updated_at)"
                     " VALUES (?, ?, ?, ?, ?, '{}', ?, ?, ?)",
                     (f"r{self.n}", f"id-{author}", author, conversation, text, status, at, at))
        conn.close()

    def opened(self):
        out = self.votes.open_vote({"text": self.TEXT, "options": self.ids[:3]})
        self.assertTrue(out["opened"], out)
        return out

    def test_choice_reads_a_number_first(self):
        for text, want in (("2", 2), ("@teenydroid 3!", 3), ("vote #1 please", 1), ("#2", 2), ("i like 2", None),
                           ("12", None), ("4", None), ("@a @b, 1", 1)):
            self.assertEqual(self.votes.choice(text, 3), want, text)

    def test_one_account_one_vote_counted_by_code(self):
        self.opened()
        self.reply("ann", "@teenydroid 1", T0 + 100)
        self.reply("ann", "@teenydroid 2", T0 + 200)  # changed her mind: the last one counts
        self.reply("bob", "2", T0 + 300)
        self.reply("cat", "3 for sure", T0 + 400, status="held")  # the guard held it
        self.reply("gus", "3", T0 + 450, status="unscreened")  # the guard has not read it yet
        self.reply("dan", "1", T0 + 500, conversation="other")  # another thread
        self.reply("teenydroid", "1", T0 + 600)  # its own
        self.reply("eve", "nice idea", T0 + 700)  # not a vote
        self.reply("fay", "1", T0 + 8 * 86400)  # after the close
        got = self.votes.current()["vote"]["so_far"]
        self.assertEqual(got["accounts"], 2)
        self.assertEqual([t["votes"] for t in got["tally"]], [0, 2, 0])
        self.assertEqual(got["winner"], self.ids[1])

    def test_the_winner_is_the_next_pick_and_nothing_else(self):
        self.opened()
        self.reply("ann", "2", T0 + 100)
        self.reply("bob", "2", T0 + 200)
        self.reply("cat", "1", T0 + 300)
        self.at(T0 + 7 * 86400 + 1)
        self.assertEqual(self.votes.current()["next_pick"], self.ids[1])
        refused = self.refused(requests.pick, {"id": self.ids[0], "reason": "i like it"})
        self.assertEqual((refused.detail["rule"], refused.detail["winner"]), ("vote", self.ids[1]))
        self.assertEqual(requests.pick({"id": self.ids[1], "reason": "it won the vote"})["status"], "picked")
        self.assertEqual(self.votes.current()["vote"]["status"], "honoured")
        self.assertNotIn("next_pick", self.votes.current())

    def test_a_skipped_winner_binds_nothing(self):
        self.opened()
        self.reply("ann", "2", T0 + 100)
        self.at(T0 + 8 * 86400)
        requests.skip({"id": self.ids[1], "reason": "it needs an api key"})
        self.assertEqual(requests.pick({"id": self.ids[0], "reason": "next best"})["status"], "picked")

    def test_a_tie_or_no_votes_has_no_winner(self):
        self.opened()
        self.reply("ann", "1", T0 + 100)
        self.reply("bob", "2", T0 + 200)
        self.at(T0 + 8 * 86400)
        out = self.votes.current()
        self.assertEqual((out["vote"]["result"]["winner"], out["vote"]["result"]["why"]), (None, "a tie"))
        self.assertNotIn("next_pick", out)
        self.assertEqual(requests.pick({"id": self.ids[3], "reason": "free choice"})["status"], "picked")

    def test_opening_rules(self):
        refused = self.refused(self.votes.open_vote, {"text": self.TEXT + " cc @ann", "options": self.ids[:3]})
        self.assertEqual(refused.detail["rule"], "mention")
        refused = self.refused(self.votes.open_vote, {"text": "1: a. 2: b", "options": self.ids[:3]})
        self.assertEqual(refused.detail["rule"], "numbers")
        with self.assertRaises(hand.HandError):
            self.votes.open_vote({"text": self.TEXT, "options": [self.ids[0]]})
        requests.skip({"id": self.ids[2], "reason": "no"})
        self.assertEqual(self.refused(self.votes.open_vote, {"text": self.TEXT, "options": self.ids[:3]}).detail["rule"], "option")
        self.assertFalse((self.root / "x-calls.jsonl").exists())  # nothing was posted

    def test_one_vote_a_week_and_a_dry_run_opens_nothing(self):
        (self.root / "x-mode").write_text("dry")
        self.assertFalse(self.votes.open_vote({"text": self.TEXT, "options": self.ids[:3]})["opened"])
        self.assertIsNone(self.votes.current()["vote"])
        (self.root / "x-mode").write_text("live")
        self.opened()
        self.assertEqual(self.refused(self.votes.open_vote, {"text": self.TEXT, "options": self.ids[:3]}).detail["rule"], "open")
        self.at(T0 + 7 * 86400 + 1)
        # once it has closed, the next week's vote opens
        self.assertTrue(self.votes.open_vote({"text": self.TEXT, "options": [self.ids[0], self.ids[3]]})["opened"])

    def test_the_cli(self):
        env = dict(os.environ)
        done = subprocess.run([sys.executable, str(HAND), "vote"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual((done.returncode, json.loads(done.stdout)["vote"]), (0, None))
