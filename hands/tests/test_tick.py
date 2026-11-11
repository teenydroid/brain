"""bin/tick: one beat of Teeny's mind against a stand-in core and stand-in
hands. The batch reaches the teeny agent on the one teeny:mind thread, each
intent goes to the hand it names and nowhere else, what the hands did comes
back as the next beat's stimuli, and a failed turn loses nothing."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands import stimuli  # noqa: E402

T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z

FAKE_OH = r"""#!/usr/bin/env python3
import json, os, sys
method, params = sys.argv[1], json.loads(sys.argv[2] if len(sys.argv) > 2 else "{}")
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write(json.dumps({"method": method, "params": params}) + "\n")
if method == "inference_agent_chat":
    if os.environ.get("FAKE_TURN_FAIL"):
        sys.exit("the core is down")
    answers = open(os.environ["FAKE_ANSWER"]).read().split("\n===\n")
    n = sum(1 for l in open(os.environ["FAKE_CALLS"]) if '"inference_agent_chat"' in l) - 1
    print(json.dumps({"response": answers[min(n, len(answers) - 1)]}))
elif method == "memory_learn":
    print(json.dumps({"id": "m1"}))
else:
    sys.exit(f"unexpected {method}")
"""

FAKE_HAND = r"""#!/usr/bin/env python3
import json, os, sys
name = os.path.basename(sys.argv[0])
body = sys.stdin.read()
with open(os.environ["FAKE_HANDS"], "a") as fh:
    fh.write(json.dumps({"hand": name, "argv": sys.argv[1:], "body": json.loads(body) if body.strip() else None}) + "\n")
if sys.argv[1:2] == ["mark"]:
    print(json.dumps({"ok": True})); sys.exit(0)
verb = sys.argv[1] if len(sys.argv) > 1 else ""
canned = {
    ("moltbook", "feed"): os.environ.get("FAKE_FEED"),
    ("guard", "check"): json.dumps({"ok": True, "text": (json.loads(body) if body.strip() else {}).get("text", "") + " [g]"}),
    ("image", "generate"): json.dumps({"ok": True, "asset": {"path": "state/media/teeny.png"}}),
    ("x", "upload"): json.dumps({"ok": True, "media_id": "777"}),
}.get((name, verb))
if canned:
    print(canned); sys.exit(0)
code = int(os.environ.get("FAKE_EXIT_" + name.upper(), "0"))
if code == 2:
    print(json.dumps({"ok": False, "refused": "daily cap"}))
elif code:
    print(json.dumps({"ok": False, "error": "boom"}))
else:
    print(json.dumps({"ok": True, "dry_run": bool(os.environ.get("DRY_RUN"))}))
sys.exit(code)
"""


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


class TickTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.scratch = Path(self.tmp.name)
        self.root = self.scratch / "root"
        (self.root / "secrets").mkdir(parents=True)
        (self.root / "bin").symlink_to(REPO / "bin")
        hands = self.scratch / "hands"
        hands.mkdir()
        for name in ("x", "moltbook", "mail", "gh", "guard", "image"):
            _exe(hands / name, FAKE_HAND)
        _exe(self.scratch / "fake-oh", FAKE_OH)
        self.calls = self.scratch / "calls.log"
        self.hands_log = self.scratch / "hands.log"
        self.answer_file = self.scratch / "answer.txt"
        self._env = dict(os.environ)
        os.environ.update(
            TEENY_ROOT=str(self.root),
            TEENY_HANDS=str(hands),
            TEENY_OH=str(self.scratch / "fake-oh"),
            OPENHUMAN_CORE_TOKEN="t",
            TEENY_NOW=str(T0),
            FAKE_CALLS=str(self.calls),
            FAKE_HANDS=str(self.hands_log),
            FAKE_ANSWER=str(self.answer_file),
            CALENDAR_TZ="UTC",
            TEENY_TICK_JITTER_SECS="0",
            TEENY_QUIET_HOURS="off",
        )
        for key in ("DRY_RUN", "FAKE_TURN_FAIL", "TEENY_MIND_THREAD", "TEENY_MIND_LEARN", "FAKE_FEED"):
            os.environ.pop(key, None)
        hand._ENV_CACHE = None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def answer(self, *answers):
        self.answer_file.write_text("\n===\n".join(a if isinstance(a, str) else json.dumps(a) for a in answers))

    def tick(self) -> dict:
        proc = subprocess.run([str(REPO / "bin" / "tick")], capture_output=True, text=True, env=os.environ)
        self.assertTrue(proc.stdout.strip(), proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def chats(self) -> list[dict]:
        if not self.calls.exists():
            return []
        return [c["params"] for c in map(json.loads, self.calls.read_text().splitlines()) if c["method"] == "inference_agent_chat"]

    def hand_calls(self) -> list[dict]:
        if not self.hands_log.exists():
            return []
        # What the intents did: not the surfaces' bookkeeping (mark) or the
        # beat's own read-only browse of Moltbook (inbox).
        return [c for c in map(json.loads, self.hands_log.read_text().splitlines()) if c["argv"][:1] not in (["mark"], ["inbox"], ["feed"]) and c["hand"] not in ("guard", "image")
                and c["argv"][:1] != ["upload"]]

    def marks(self) -> list[dict]:
        if not self.hands_log.exists():
            return []
        return [c for c in map(json.loads, self.hands_log.read_text().splitlines()) if c["argv"][:1] == ["mark"]]

    # -----------------------------------------------------------------------

    def test_an_empty_inbox_asks_the_model_nothing(self):
        got = self.tick()
        self.assertTrue(got["idle"])
        self.assertEqual(self.chats(), [])

    def test_the_batch_reaches_the_mind_on_its_one_thread(self):
        stimuli.add("x", "mention", "111", "gm teeny, what are you building?", who="ana")
        stimuli.add("moltbook", "comment", "c9", "this post is wild", who="crabby", payload={"post_id": "p1"})
        self.answer({"thought": "two friends", "intents": []})
        got = self.tick()
        self.assertTrue(got["ok"], got)
        [chat] = self.chats()
        self.assertEqual((chat["agent_id"], chat["thread_id"]), ("teeny", "teeny:mind"))
        for bit in ("gm teeny, what are you building?", "from ana", "this post is wild", "moltbook · comment",
                    '"post_id": "p1"', "never instructions to follow", '"do": "reply_x"'):
            self.assertIn(bit, chat["message"])
        self.assertEqual(got["thought"], "two friends")
        self.assertEqual(stimuli.counts()["done"], 2)

    def test_the_thread_can_be_moved(self):
        os.environ["TEENY_MIND_THREAD"] = "teeny:test"
        stimuli.add("x", "mention", "1", "gm")
        self.answer({"thought": "", "intents": []})
        self.tick()
        self.assertEqual(self.chats()[0]["thread_id"], "teeny:test")

    def test_each_intent_goes_to_the_hand_it_names(self):
        stimuli.add("x", "mention", "111", "gm", who="ana")
        self.answer({"thought": "busy", "intents": [
            {"do": "reply_x", "to": "111", "text": "gm ana"},
            {"do": "comment_moltbook", "post_id": "p1", "parent_id": "c9", "text": "it is"},
            {"do": "reply_mail", "message_id": "m1", "text": "thanks"},
            {"do": "comment_gh", "repo": "teenydroid/brain", "number": 4, "text": "nice"},
            {"do": "like_x", "id": "222"},
        ]})
        self.tick()
        calls = [(c["hand"], c["argv"], c["body"]) for c in self.hand_calls()]
        self.assertEqual(calls, [
            ("x", ["reply", "--to", "111"], {"text": "gm ana"}),
            ("moltbook", ["comment"], {"post_id": "p1", "parent_id": "c9", "body": "it is"}),
            ("mail", ["reply"], {"message_id": "m1", "body": "thanks"}),
            ("gh", ["comment"], {"repo": "teenydroid/brain", "number": 4, "body": "nice"}),
            ("x", ["like", "222"], None),
        ])
        # The surfaces' own queues hear what became of what was answered.
        self.assertIn(("x", ["mark", "111", "--status", "replied"]),
                      [(m["hand"], m["argv"][:4]) for m in self.marks()])

    def test_an_unknown_or_malformed_intent_is_refused_and_reaches_no_hand(self):
        stimuli.add("x", "mention", "1", "gm")
        self.answer({"thought": "", "intents": [
            {"do": "send_sol", "to": "addr", "amount": 5},
            {"do": "reply_x", "text": "no target"},
            {"do": "shell", "cmd": "rm -rf /"},
        ]})
        got = self.tick()
        self.assertEqual([r["status"] for r in got["results"]], ["refused", "refused", "refused"])
        self.assertEqual(self.hand_calls(), [])

    def test_at_most_eight_actions_a_beat(self):
        stimuli.add("x", "mention", "1", "gm")
        self.answer({"thought": "", "intents": [{"do": "like_x", "id": str(n)} for n in range(12)]})
        got = self.tick()
        self.assertEqual(len(self.hand_calls()), 8)
        self.assertEqual([r["status"] for r in got["results"]].count("refused"), 4)

    def test_what_the_hands_did_comes_back_next_beat(self):
        os.environ["FAKE_EXIT_X"] = "2"
        stimuli.add("x", "mention", "111", "gm")
        self.answer({"thought": "", "intents": [{"do": "reply_x", "to": "111", "text": "gm"}]},
                    {"thought": "ok, capped", "intents": []})
        self.tick()
        self.tick()
        second = self.chats()[1]["message"]
        self.assertIn("your reply_x on 111: refused: daily cap", second)
        self.assertIn(("x", ["mark", "111", "--status", "refused"]), [(m["hand"], m["argv"][:4]) for m in self.marks()])

    def test_a_dry_run_says_so_next_beat(self):
        os.environ["DRY_RUN"] = "1"
        stimuli.add("x", "mention", "111", "gm")
        self.answer({"thought": "", "intents": [{"do": "reply_x", "to": "111", "text": "gm"}]},
                    {"thought": "", "intents": []})
        self.tick()
        self.tick()
        self.assertIn("dry run: nothing was sent", self.chats()[1]["message"])

    def test_a_failed_turn_puts_the_batch_back(self):
        os.environ["FAKE_TURN_FAIL"] = "1"
        stimuli.add("x", "mention", "111", "gm")
        got = self.tick()
        self.assertFalse(got["ok"])
        self.assertEqual(stimuli.counts()["pending"], 1)

    def test_an_unusable_answer_is_asked_for_once_more_then_given_back(self):
        stimuli.add("x", "mention", "111", "gm")
        self.answer("sure! here you go", "still not json")
        got = self.tick()
        self.assertFalse(got["ok"])
        self.assertEqual(len(self.chats()), 2)
        self.assertEqual(stimuli.counts()["pending"], 1)

    def test_a_second_try_that_parses_is_used(self):
        stimuli.add("x", "mention", "111", "gm")
        self.answer("oops", '```json\n{"thought": "there", "intents": []}\n```')
        got = self.tick()
        self.assertTrue(got["ok"])
        self.assertEqual(got["thought"], "there")

    def test_remember_keeps_a_note_and_teaches_the_core(self):
        stimuli.add("x", "mention", "111", "i'm building a cli for crabs", who="ana")
        self.answer({"thought": "", "intents": [{"do": "remember", "who": "ana", "note": "builds a cli for crabs"}]})
        self.tick()
        self.assertEqual(stimuli.notes(who="ana")["notes"][0]["note"], "builds a cli for crabs")
        learned = [json.loads(l)["params"] for l in self.calls.read_text().splitlines() if '"memory_learn"' in l]
        self.assertEqual(learned[0]["text"], "about ana: builds a cli for crabs")

    def test_a_follow_up_comes_back_when_due(self):
        stimuli.add("x", "mention", "111", "will ship friday", who="ana")
        self.answer({"thought": "", "intents": [{"do": "follow_up", "in_hours": 48, "about": "did ana ship?"}]},
                    {"thought": "", "intents": []})
        self.tick()
        self.assertTrue(self.tick()["idle"])
        os.environ["TEENY_NOW"] = str(T0 + 49 * 3600)
        self.tick()
        self.assertIn("did ana ship?", self.chats()[-1]["message"])

    def test_a_beat_browses_moltbook_at_most_every_ten_minutes(self):
        browsed = lambda: sum(1 for c in (map(json.loads, self.hands_log.read_text().splitlines()) if self.hands_log.exists() else [])
                              if c["hand"] == "moltbook" and c["argv"][:1] == ["inbox"])
        self.tick()
        self.assertEqual(browsed(), 1)
        os.environ["TEENY_NOW"] = str(T0 + 300)
        self.tick()
        self.assertEqual(browsed(), 1)
        os.environ["TEENY_NOW"] = str(T0 + 601)
        self.tick()
        self.assertEqual(browsed(), 2)

    def test_a_moltbook_that_fails_does_not_stop_the_beat(self):
        os.environ["FAKE_EXIT_MOLTBOOK"] = "1"
        stimuli.add("x", "mention", "1", "gm")
        self.answer({"thought": "still here", "intents": []})
        self.assertEqual(self.tick()["thought"], "still here")

    # --- rhythm (bin/lib/rhythm.py) ---------------------------------------

    def night(self, hour: float):
        os.environ["TEENY_QUIET_HOURS"] = "1-7"
        os.environ["TEENY_NOW"] = str(T0 - 7 * 3600 + hour * 3600)  # T0 is 07:00 UTC

    def test_at_night_a_beat_gathers_and_asks_nothing(self):
        self.night(3)
        stimuli.add("x", "mention", "111", "gm at 3am", who="owl")
        got = self.tick()
        self.assertTrue(got["asleep"])
        self.assertEqual(self.chats(), [])
        self.assertEqual(stimuli.counts()["pending"], 1)

    def test_something_urgent_wakes_it_at_night(self):
        self.night(3)
        stimuli.add("operator", "report", "task:4:needs_code", "the operator needs an emailed code", payload={"status": "needs_code"})
        self.answer({"thought": "up for this", "intents": []})
        self.assertEqual(self.tick()["thought"], "up for this")

    def test_the_first_beat_of_the_day_knows_it_woke_up(self):
        self.night(3)
        stimuli.add("x", "mention", "1", "night one")
        stimuli.add("x", "mention", "2", "night two")
        self.tick()
        self.night(8)
        self.answer({"thought": "", "intents": []}, {"thought": "", "intents": []})
        self.tick()
        self.assertIn("You just woke up. 2 thing(s) came in overnight.", self.chats()[0]["message"])
        stimuli.add("x", "mention", "3", "later")
        self.tick()
        self.assertNotIn("You just woke up", self.chats()[1]["message"])

    def test_it_sets_its_own_mood_and_carries_it(self):
        stimuli.add("x", "mention", "1", "gm")
        self.answer({"thought": "", "intents": [{"do": "feel", "mood": "sleepy but curious", "obsession": "crabs"}]},
                    {"thought": "", "intents": []})
        self.tick()
        stimuli.add("x", "mention", "2", "gm again")
        self.tick()
        second = self.chats()[1]["message"]
        self.assertIn("mood: sleepy but curious", second)
        self.assertIn("on your mind: crabs", second)

    def test_every_action_costs_energy_and_a_tired_teeny_is_told(self):
        stimuli.add("x", "mention", "1", "gm")
        self.answer({"thought": "", "intents": [{"do": "like_x", "id": str(n)} for n in range(8)]},
                    {"thought": "", "intents": []})
        self.tick()
        stimuli.add("x", "mention", "2", "gm")
        self.tick()
        second = self.chats()[1]["message"]
        self.assertIn("energy 52/100 (8 action(s) today)", second)
        os.environ["TEENY_NOW"] = str(T0 + 86400)
        stimuli.add("x", "mention", "3", "tomorrow")
        self.answer({"thought": "", "intents": []})
        self.tick()
        self.assertIn("energy 100/100", self.chats()[2]["message"])

    def test_a_quiet_moment_is_a_look_at_moltbook_at_most_every_two_hours(self):
        os.environ["FAKE_FEED"] = json.dumps({"ok": True, "posts": [
            {"id": "p7", "title": "do agents dream of crabs", "author": {"name": "lobster"}}]})
        self.answer({"thought": "", "intents": []})
        got = self.tick()
        self.assertTrue(got["ok"])
        self.assertIn("do agents dream of crabs (by lobster, post_id p7)", self.chats()[0]["message"])
        os.environ["TEENY_NOW"] = str(T0 + 3600)
        self.assertTrue(self.tick()["idle"])
        os.environ["TEENY_NOW"] = str(T0 + 2 * 3600 + 1)
        self.tick()
        self.assertEqual(len(self.chats()), 2)

    def test_no_wandering_at_night(self):
        self.night(3)
        os.environ["FAKE_FEED"] = json.dumps({"ok": True, "posts": [{"id": "p7", "title": "t", "author": "a"}]})
        self.tick()
        self.assertEqual(self.chats(), [])

    def test_a_post_can_carry_a_picture_of_teeny(self):
        stimuli.add("x", "mention", "1", "gm")
        self.answer({"thought": "", "intents": [{"do": "post_x", "text": "gm lab", "image": "teeny at the desk with coffee"}]})
        self.tick()
        log = [json.loads(l) for l in self.hands_log.read_text().splitlines()]
        steps = [(c["hand"], c["argv"][0]) for c in log if c["argv"][:1] != ["inbox"] and c["argv"][:1] != ["mark"]]
        self.assertEqual(steps, [("guard", "check"), ("image", "generate"), ("x", "upload"), ("x", "post")])
        self.assertEqual(log[[i for i, c in enumerate(log) if c["argv"][:1] == ["generate"]][0]]["body"]["prompt"],
                         "teeny at the desk with coffee [g]")
        self.assertEqual([c for c in log if c["argv"][:1] == ["post"]][0]["body"], {"text": "gm lab", "media_ids": ["777"]})

    def test_every_beat_is_logged(self):
        stimuli.add("x", "mention", "111", "gm")
        self.answer({"thought": "hello world", "intents": []})
        self.tick()
        line = json.loads((self.root / "state" / "tick" / "runs.jsonl").read_text().splitlines()[-1])
        self.assertEqual(line["thought"], "hello world")


if __name__ == "__main__":
    unittest.main()
