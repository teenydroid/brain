"""bin/tick: one beat of Teeny's mind against a stand-in core and stand-in
hands. The batch reaches the teeny agent on the one teeny:mind thread, each
intent goes to the hand it names and nowhere else, what the hands did comes
back as the next beat's stimuli, and a failed turn loses nothing."""

import fcntl
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
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
    import time
    time.sleep(float(os.environ.get("FAKE_TURN_SLEEP") or 0))
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
        for name in ("x", "moltbook", "mail", "gh", "guard", "image", "telegram"):
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
        for key in ("DRY_RUN", "FAKE_TURN_FAIL", "FAKE_TURN_SLEEP", "TEENY_MIND_THREAD", "TEENY_MIND_LEARN", "FAKE_FEED"):
            os.environ.pop(key, None)
        hand._ENV_CACHE = None
        self.answer({"thought": "", "intents": []})
        self.detached: list[int] = []

    def tearDown(self):
        for pid in self.detached:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
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
        return [c for c in map(json.loads, self.hands_log.read_text().splitlines()) if c["argv"][:1] not in (["mark"], ["inbox"], ["feed"], ["ingest"]) and c["hand"] not in ("guard", "image")
                and c["argv"][:1] != ["upload"]]

    def marks(self) -> list[dict]:
        if not self.hands_log.exists():
            return []
        return [c for c in map(json.loads, self.hands_log.read_text().splitlines()) if c["argv"][:1] == ["mark"]]

    # -----------------------------------------------------------------------

    def runs(self) -> list[dict]:
        path = self.root / "state" / "tick" / "runs.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def learned(self) -> list[dict]:
        if not self.calls.exists():
            return []
        return [c["params"] for c in map(json.loads, self.calls.read_text().splitlines()) if c["method"] == "memory_learn"]

    def test_an_awake_beat_with_an_empty_inbox_still_thinks(self):
        self.answer({"thought": "a slow morning", "intents": [{"do": "feel", "mood": "calm"}]})
        got = self.tick()
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["kind"], "quiet")
        self.assertFalse(got.get("idle"))
        [chat] = self.chats()
        self.assertIn("### self · quiet", chat["message"])
        self.assertIn("Nothing new came in", chat["message"])
        self.assertEqual(got["thought"], "a slow morning")

    def test_every_awake_beat_gets_its_own_quiet_moment(self):
        self.tick()
        self.tick()  # the same second: a fresh quiet stimulus all the same
        self.assertEqual(len(self.chats()), 2)
        for chat in self.chats():
            self.assertEqual(chat["message"].count("### self · quiet"), 1)

    def test_a_quiet_beat_with_something_waiting_is_not_quiet(self):
        stimuli.add("x", "mention", "1", "gm")
        self.answer({"thought": "", "intents": []})
        got = self.tick()
        self.assertEqual(got["kind"], "beat")
        self.assertNotIn("self · quiet", self.chats()[0]["message"])

    def test_the_quiet_moment_says_what_the_mind_can_act_on(self):
        stimuli.follow_up(T0 + 10 * 3600, "did ana ship?")
        stimuli.add("x", "mention", "1", "gm")
        self.answer({"thought": "", "intents": [{"do": "like_x", "id": "1"}]}, {"thought": "", "intents": []})
        self.tick()
        os.environ["TEENY_NOW"] = str(T0 + 60)
        self.tick()  # how the like landed
        os.environ["TEENY_NOW"] = str(T0 + 3 * 3600 + 60)
        stimuli.follow_up(T0 + 5 * 3600, "write to bob")
        self.tick()
        quiet = self.chats()[2]["message"]
        self.assertIn("self · quiet", quiet)
        self.assertIn("Your last action out in the world was 3 h ago", quiet)
        self.assertIn("1 action(s) today", quiet)
        self.assertIn("2 follow-up(s) waiting; the next: write to bob", quiet)

    def test_a_tired_teeny_is_told_resting_is_a_full_answer(self):
        state = self.root / "state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "mind.json").write_text(json.dumps({"day": "2026-09-14", "actions_today": 14}))
        self.tick()
        quiet = self.chats()[0]["message"]
        self.assertIn("self · quiet", quiet)
        self.assertIn("Resting is fine", quiet)

    def test_a_quiet_beat_whose_turn_fails_does_not_come_back_stale(self):
        os.environ["FAKE_TURN_FAIL"] = "1"
        self.assertFalse(self.tick()["ok"])
        self.assertEqual(stimuli.counts()["pending"], 0)
        del os.environ["FAKE_TURN_FAIL"]
        self.tick()
        self.assertEqual(self.chats()[-1]["message"].count("### self · quiet"), 1)

    # --- every beat leaves a trace -----------------------------------------

    def test_every_kind_of_beat_is_logged(self):
        self.tick()  # quiet
        stimuli.add("x", "mention", "1", "gm")
        self.tick()  # beat
        os.environ["FAKE_TURN_FAIL"] = "1"
        stimuli.add("x", "mention", "2", "gm again")
        self.tick()  # failed
        del os.environ["FAKE_TURN_FAIL"]
        with hand.lock("tick"):
            self.assertEqual(self.tick()["kind"], "busy")
        self.night(3)
        self.tick()  # asleep
        kinds = [r.get("kind") for r in self.runs()]
        self.assertEqual(kinds, ["quiet", "beat", "failed", "busy", "asleep"])
        asleep, busy = self.runs()[-1], self.runs()[-2]
        # The no-ops keep `idle`, which the reflection skips.
        self.assertTrue(asleep["asleep"] and asleep["idle"])
        self.assertTrue(busy["idle"])
        self.assertEqual(asleep["waiting"], 1)
        for r in self.runs():
            self.assertIn("at", r)

    def test_an_answer_with_no_intents_still_leaves_an_inward_trace(self):
        stimuli.add("x", "mention", "1", "gm")
        self.answer({"thought": "nothing to say to anyone right now", "intents": []})
        got = self.tick()
        [result] = got["results"]
        self.assertEqual((result["do"], result["status"]), ("journal", "done"))
        self.assertTrue(result["intent"]["auto"])
        self.assertEqual(len(self.chats()), 1)  # never asked again
        self.assertEqual(self.runs()[-1]["results"], got["results"])
        self.assertIn("nothing to say to anyone right now", self.journal())

    def test_only_skips_count_as_no_intents(self):
        stimuli.add("x", "mention", "1", "gm")
        self.answer({"thought": "pass", "intents": [{"do": "skip"}]})
        got = self.tick()
        self.assertEqual([r["do"] for r in got["results"]], ["skip", "journal"])

    def test_an_answer_that_did_something_gets_no_automatic_act(self):
        stimuli.add("x", "mention", "1", "gm")
        self.answer({"thought": "", "intents": [{"do": "feel", "mood": "ok"}]})
        self.assertEqual([r["do"] for r in self.tick()["results"]], ["feel"])

    def journal(self, day="2026-09-14") -> str:
        path = self.root / "state" / "journal" / f"{day}.md"
        return path.read_text() if path.exists() else ""

    def test_each_thinking_beat_is_journaled_and_taught_to_the_core(self):
        stimuli.add("x", "mention", "111", "gm", who="ana")
        self.answer({"thought": "ana said gm", "intents": [
            {"do": "reply_x", "to": "111", "text": "gm ana"},
            {"do": "journal", "text": "i like mornings"},
        ]}, {"thought": "a lull", "intents": [{"do": "feel", "mood": "easy"}]})
        self.tick()
        os.environ["TEENY_NOW"] = str(T0 + 300)
        self.tick()
        page = self.journal()
        self.assertTrue(page.startswith("# Monday 14 September 2026\n"))
        self.assertIn("## 07:00 · beat", page)
        self.assertIn("ana said gm", page)
        self.assertIn("reply_x on 111: done", page)
        self.assertIn("i like mornings", page)
        self.assertNotIn("gm ana", page)  # what was said goes to the hands, not the journal
        self.assertIn("## 07:05 · beat", page)  # it reads how the reply landed
        self.assertIn("a lull", page)
        journaled = [l for l in self.learned() if l["text"].startswith("journal, ")]
        self.assertEqual(len(journaled), 2)
        self.assertEqual(journaled[0]["kind"], "other")
        self.assertIn("ana said gm", journaled[0]["text"])
        self.assertEqual(self.runs()[-1]["journal"]["learned"], True)

    def test_the_journal_is_kept_but_not_taught_when_learning_is_off(self):
        os.environ["TEENY_MIND_LEARN"] = "0"
        self.answer({"thought": "quiet one", "intents": []})
        self.tick()
        self.assertIn("quiet one", self.journal())
        self.assertEqual(self.learned(), [])

    def test_the_journal_follows_teenys_own_calendar(self):
        os.environ["CALENDAR_TZ"] = "Asia/Kolkata"
        os.environ["TEENY_NOW"] = str(T0 + 17 * 3600)  # 00:00 UTC, 05:30 the next day in Kolkata
        self.answer({"thought": "up early", "intents": []})
        self.tick()
        self.assertIn("## 05:30 · quiet", self.journal("2026-09-15"))

    def test_asleep_and_failed_beats_are_not_journaled(self):
        self.night(3)
        self.tick()
        self.night(9)
        os.environ["FAKE_TURN_FAIL"] = "1"
        self.tick()
        self.assertEqual(self.journal(), "")
        self.assertEqual(self.learned(), [])

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

    def test_say_telegram_goes_to_the_telegram_hand(self):
        stimuli.add("telegram", "dm", "42:7", "@steve in a DM:\nhey teeny", who="@steve")
        self.answer({"thought": "steve!", "intents": [
            {"do": "say_telegram", "chat_id": 42, "reply_to": 7, "text": "hey steve"},
            {"do": "say_telegram", "chat_id": -100123, "text": "gm group"},
        ]})
        self.tick()
        calls = [(c["hand"], c["argv"], c["body"]) for c in self.hand_calls()]
        self.assertEqual(calls, [
            ("telegram", ["send"], {"chat_id": "42", "reply_to": "7", "text": "hey steve"}),
            ("telegram", ["send"], {"chat_id": "-100123", "text": "gm group"}),
        ])

    def test_a_beat_reads_telegram_only_when_a_bot_token_is_set(self):
        self.tick()
        ingests = lambda: [c for c in map(json.loads, self.hands_log.read_text().splitlines())
                           if c["hand"] == "telegram" and c["argv"] == ["ingest"]] if self.hands_log.exists() else []
        self.assertEqual(ingests(), [])
        (self.root / "secrets" / "teeny.env").write_text("TELEGRAM_BOT_TOKEN=123:abc\n")
        hand._ENV_CACHE = None
        self.tick()
        self.assertEqual(len(ingests()), 1)

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
        self.assertEqual(self.tick()["kind"], "quiet")
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

    def test_an_owner_on_telegram_wakes_it_at_night_and_a_stranger_does_not(self):
        self.night(3)
        stimuli.add("telegram", "mention", "-1:5", "someone in a group at 3am", payload={"chat_id": -1})
        self.assertTrue(self.tick()["asleep"])
        stimuli.add("telegram", "dm", "42:7", "@steve in a DM: you up?", payload={"chat_id": 42, "owner": True})
        self.answer({"thought": "for steve, always", "intents": []})
        self.assertEqual(self.tick()["thought"], "for steve, always")

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

    def test_waking_to_an_empty_inbox_says_nothing_came_in(self):
        self.night(3)
        self.tick()
        self.night(8)
        self.tick()
        message = self.chats()[0]["message"]
        self.assertIn("You just woke up. Nothing came in overnight.", message)
        self.assertIn("self · quiet", message)

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
        self.assertIn("energy 76/100 (8 action(s) today)", second)
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
        looked = "do agents dream of crabs (by lobster, post_id p7)"
        self.assertIn(looked, self.chats()[0]["message"])
        # A look at the world is a quiet beat's moment: no extra quiet prompt.
        self.assertNotIn("self · quiet", self.chats()[0]["message"])
        os.environ["TEENY_NOW"] = str(T0 + 3600)
        self.tick()
        self.assertNotIn(looked, self.chats()[1]["message"])
        self.assertIn("self · quiet", self.chats()[1]["message"])
        os.environ["TEENY_NOW"] = str(T0 + 2 * 3600 + 1)
        self.tick()
        self.assertIn(looked, self.chats()[2]["message"])

    def test_no_wandering_at_night(self):
        self.night(3)
        os.environ["FAKE_FEED"] = json.dumps({"ok": True, "posts": [{"id": "p7", "title": "t", "author": "a"}]})
        self.tick()
        self.assertEqual(self.chats(), [])

    def test_a_night_beat_with_an_empty_inbox_asks_nothing(self):
        self.night(3)
        got = self.tick()
        self.assertTrue(got["asleep"])
        self.assertEqual(self.chats(), [])
        self.assertEqual(stimuli.counts()["pending"], 0)

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


    # --- the cron call: bin/tick --detach ---------------------------------------------
    # The core gives up on a cron shell job after 300 s (TinyBox's module call
    # timeout, docs/oh-gaps.md G12), and a beat can take longer. The cron call
    # starts the beat in its own session and returns; the beat logs itself.

    def detach(self) -> tuple[dict, float]:
        started = time.monotonic()
        proc = subprocess.run([str(REPO / "bin" / "tick"), "--detach"], capture_output=True, text=True,
                              env=os.environ, timeout=120)
        took = time.monotonic() - started
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        if isinstance(out.get("pid"), int):
            self.detached.append(out["pid"])
        return out, took

    def wait_for(self, what, timeout: float = 30.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            got = what()
            if got:
                return got
            time.sleep(0.1)
        self.fail("timed out waiting")

    def tick_lock_held(self) -> bool:
        path = self.root / "state" / "locks" / "tick.lock"
        if not path.exists():
            return False
        with open(path, "a+") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(fh, fcntl.LOCK_UN)
            return False

    def test_the_pinned_tick_runs_detached(self):
        import tomllib

        jobs = tomllib.loads((REPO / "evals" / "cron.pinned.toml").read_text())["jobs"]
        self.assertEqual(jobs["tick"]["command"], "bin/tick --detach")

    def test_the_cron_call_returns_at_once_and_the_beat_carries_on(self):
        stimuli.add("x", "mention", "1", "gm")
        self.answer({"thought": "took my time", "intents": [{"do": "like_x", "id": "1"}]})
        os.environ["FAKE_TURN_SLEEP"] = "3"
        out, took = self.detach()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["kind"], "started")
        self.assertEqual(out["log"], "state/tick/beat.log")
        self.assertLess(took, 2.0)
        self.assertEqual(self.runs(), [])  # still thinking
        [line] = self.wait_for(self.runs)
        self.assertEqual(line["kind"], "beat")
        self.assertEqual(line["thought"], "took my time")
        self.assertEqual([c["argv"] for c in self.hand_calls()], [["like", "1"]])
        log = self.root / "state" / "tick" / "beat.log"
        self.wait_for(lambda: '"thought": "took my time"' in log.read_text())

    def test_the_jitter_is_spent_in_the_detached_beat_not_the_cron_call(self):
        os.environ["TEENY_TICK_JITTER_SECS"] = "180"
        out, took = self.detach()
        self.assertEqual(out["kind"], "started")
        self.assertLess(took, 2.0)
        self.assertEqual(self.chats(), [])
        # A beat waiting out its jitter already holds its slot.
        self.wait_for(self.tick_lock_held, timeout=10)
        second, _ = self.detach()
        self.assertEqual(second["kind"], "busy")

    def test_a_beat_still_running_makes_the_next_cron_call_busy(self):
        os.environ["FAKE_TURN_SLEEP"] = "4"
        first, _ = self.detach()
        self.assertEqual(first["kind"], "started")
        self.wait_for(self.tick_lock_held)
        second, took = self.detach()
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["kind"], "busy")
        self.assertNotIn("pid", second)
        self.assertLess(took, 2.0)
        self.wait_for(self.runs)
        self.wait_for(lambda: not self.tick_lock_held())
        self.assertEqual(len(self.chats()), 1)
        self.assertEqual([r["kind"] for r in self.runs()], ["quiet"])

    def test_the_beat_log_is_rotated_before_it_grows_too_big(self):
        sys.path.insert(0, str(REPO / "bin" / "lib"))
        import detach

        log = self.root / "state" / "tick" / "beat.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("old\n" * (detach.LOG_MAX_BYTES // 4 + 1))
        out, _ = self.detach()
        self.assertEqual(out["kind"], "started")
        self.wait_for(self.runs)
        self.wait_for(lambda: not self.tick_lock_held())
        self.assertTrue((log.parent / "beat.log.1").read_text().startswith("old\n"))
        self.assertLess(log.stat().st_size, 10_000)
        self.assertNotIn("old\n", log.read_text())


if __name__ == "__main__":
    unittest.main()
