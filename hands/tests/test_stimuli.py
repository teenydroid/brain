"""teeny_hands.stimuli: the one inbox every surface writes to and bin/tick
drains into Teeny's mind thread. Idempotent on (surface, kind, ref), drained
in arrival order under caps, put back when a tick fails, and follow-ups that
come due arrive like anything else."""

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
from teeny_hands import stimuli  # noqa: E402

HAND = HERE.parent / "stimuli"
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

    def at(self, offset: float) -> None:
        os.environ["TEENY_NOW"] = str(T0 + offset)


class InboxTest(Scratch):
    def test_the_same_event_lands_once(self):
        self.assertTrue(stimuli.add("x", "mention", "111", "gm teeny", who="ana"))
        self.assertFalse(stimuli.add("x", "mention", "111", "gm teeny again", who="ana"))
        self.assertEqual(stimuli.counts()["pending"], 1)

    def test_the_health_check_is_a_surface(self):
        self.assertTrue(stimuli.add("health", "alarm", "outage:1", "your model is not answering"))

    def test_the_operator_on_the_host_is_a_surface(self):
        self.assertTrue(stimuli.add("host", "instruction", "host-1-ab", "tweet that", who="operator"))

    def test_hacker_news_is_a_surface(self):
        self.assertTrue(stimuli.add("hn", "comment", "42", "nice work", who="pg"))
        self.assertEqual(stimuli.bare_ref("hn:42"), "42")

    def test_one_ref_on_two_surfaces_is_two_events(self):
        stimuli.add("x", "mention", "7", "a")
        stimuli.add("moltbook", "comment", "7", "b")
        self.assertEqual(stimuli.counts()["pending"], 2)

    def test_take_drains_in_arrival_order_and_marks_the_batch(self):
        stimuli.add("x", "mention", "1", "first")
        self.at(10)
        stimuli.add("moltbook", "comment", "c1", "second", who="crab")
        batch = stimuli.take(max_items=10, max_chars=10_000)
        self.assertEqual([s["text"] for s in batch["stimuli"]], ["first", "second"])
        self.assertEqual(batch["stimuli"][1]["who"], "crab")
        self.assertTrue(batch["tick"])
        self.assertEqual(stimuli.counts(), {"pending": 0, "taken": 2, "done": 0})
        # A second tick while the first is still out sees nothing new.
        self.assertEqual(stimuli.take(max_items=10, max_chars=10_000)["stimuli"], [])

    def test_take_stops_at_the_item_cap(self):
        for n in range(5):
            stimuli.add("x", "mention", str(n), f"m{n}")
        self.assertEqual(len(stimuli.take(max_items=3, max_chars=10_000)["stimuli"]), 3)
        self.assertEqual(stimuli.counts()["pending"], 2)

    def test_take_stops_at_the_character_cap_but_always_takes_one(self):
        stimuli.add("x", "mention", "big", "x" * 5000)
        stimuli.add("x", "mention", "next", "y" * 10)
        batch = stimuli.take(max_items=10, max_chars=100)
        self.assertEqual([s["ref"] for s in batch["stimuli"]], ["big"])
        self.assertLessEqual(len(batch["stimuli"][0]["text"]), stimuli.MAX_TEXT_CHARS + 20)

    def test_a_nudge_keeps_more_of_its_text_than_anything_else(self):
        # A habit's nudge carries its playbook's inputs (bin/act): cut at the
        # usual length, the mind would get half a JSON object.
        stimuli.add("nudge", "world-scan", "world-scan:1", "n" * 12_000)
        stimuli.add("nudge", "world-scan", "world-scan:2", "n" * 40_000)
        stimuli.add("x", "mention", "big", "x" * 12_000)
        by_ref = {s["ref"]: s["text"] for s in stimuli.take(max_items=10, max_chars=200_000)["stimuli"]}
        self.assertEqual(len(by_ref["world-scan:1"]), 12_000)
        self.assertLessEqual(len(by_ref["world-scan:2"]), stimuli.NUDGE_TEXT_CHARS + 20)
        self.assertLessEqual(len(by_ref["big"]), stimuli.MAX_TEXT_CHARS + 20)

    def test_recent_can_be_brief_enough_for_a_playbook_input(self):
        stimuli.add("nudge", "world-scan", "world-scan:1", "n" * 9000, payload={"playbook": "world-scan"})
        stimuli.add("x", "mention", "7", "gm teeny", who="ana")
        full = stimuli.recent(10)["stimuli"]
        self.assertEqual(len([s for s in full if s["ref"] == "world-scan:1"][0]["text"]), 9000)
        brief = {s["ref"]: s for s in stimuli.recent(10, chars=300)["stimuli"]}
        self.assertEqual(len(brief["world-scan:1"]["text"]), 300)
        self.assertNotIn("payload", brief["world-scan:1"])
        self.assertEqual((brief["7"]["text"], brief["7"]["who"], brief["7"]["status"]), ("gm teeny", "ana", "pending"))

    def test_a_failed_tick_puts_its_batch_back(self):
        stimuli.add("x", "mention", "1", "first")
        tick = stimuli.take(max_items=10, max_chars=10_000)["tick"]
        stimuli.release(tick)
        self.assertEqual([s["ref"] for s in stimuli.take(max_items=10, max_chars=10_000)["stimuli"]], ["1"])

    def test_a_settled_tick_is_done_for_good(self):
        stimuli.add("x", "mention", "1", "first")
        tick = stimuli.take(max_items=10, max_chars=10_000)["tick"]
        stimuli.settle(tick)
        self.assertEqual(stimuli.counts(), {"pending": 0, "taken": 0, "done": 1})
        self.assertFalse(stimuli.add("x", "mention", "1", "first"))

    def test_a_tick_that_died_is_taken_back_after_an_hour(self):
        stimuli.add("x", "mention", "1", "first")
        stimuli.take(max_items=10, max_chars=10_000)
        self.at(30 * 60)
        self.assertEqual(stimuli.take(max_items=10, max_chars=10_000)["stimuli"], [])
        self.at(61 * 60)
        self.assertEqual(len(stimuli.take(max_items=10, max_chars=10_000)["stimuli"]), 1)

    def test_payload_round_trips(self):
        stimuli.add("gh", "comment", "teenydroid/x#4", "nice", payload={"repo": "teenydroid/x", "number": 4})
        got = stimuli.take(max_items=1, max_chars=1000)["stimuli"][0]
        self.assertEqual(got["payload"], {"repo": "teenydroid/x", "number": 4})

    def test_unknown_surfaces_are_refused(self):
        with self.assertRaises(hand.HandError):
            stimuli.add("myspace", "poke", "1", "hi")


class FollowUpTest(Scratch):
    def test_a_follow_up_arrives_when_it_comes_due(self):
        stimuli.follow_up(T0 + 3600, "check whether ana's cli shipped", ref="x:111")
        self.assertEqual(stimuli.raise_due(), 0)
        self.at(3601)
        self.assertEqual(stimuli.raise_due(), 1)
        batch = stimuli.take(max_items=10, max_chars=10_000)["stimuli"]
        self.assertEqual((batch[0]["surface"], batch[0]["kind"]), ("self", "follow_up"))
        self.assertIn("ana's cli", batch[0]["text"])
        # Raised once only.
        self.assertEqual(stimuli.raise_due(), 0)

    def test_a_follow_up_in_the_past_or_too_far_ahead_is_refused(self):
        with self.assertRaises(hand.HandError):
            stimuli.follow_up(T0 - 10, "yesterday")
        with self.assertRaises(hand.HandError):
            stimuli.follow_up(T0 + 400 * 86400, "next year")

    def test_open_follow_ups_are_listed_soonest_first(self):
        stimuli.follow_up(T0 + 7200, "later")
        stimuli.follow_up(T0 + 3600, "sooner")
        self.assertEqual([f["about"] for f in stimuli.follow_ups()["follow_ups"]], ["sooner", "later"])

    def test_a_dropped_follow_up_never_comes_due(self):
        dropped = stimuli.follow_up(T0 + 3600, "send the japanese reply", ref="111")
        kept = stimuli.follow_up(T0 + 3600, "did ana ship?")
        self.assertEqual(stimuli.drop_follow_up(id=dropped["id"]), [dropped["id"]])
        self.at(3601)
        self.assertEqual(stimuli.raise_due(), 1)
        [raised] = stimuli.take(max_items=10, max_chars=10_000)["stimuli"]
        self.assertEqual(raised["ref"], str(kept["id"]))

    def test_follow_ups_are_dropped_by_ref_with_or_without_the_surface(self):
        a = stimuli.follow_up(T0 + 3600, "answer ana", ref="x:111")
        b = stimuli.follow_up(T0 + 7200, "answer ana again", ref="111")
        stimuli.follow_up(T0 + 3600, "something else", ref="222")
        self.assertEqual(sorted(stimuli.drop_follow_up(ref="111")), sorted([a["id"], b["id"]]))
        self.assertEqual([f["ref"] for f in stimuli.follow_ups()["follow_ups"]], ["222"])

    def test_dropping_needs_an_id_or_a_ref_and_drops_only_what_waits(self):
        with self.assertRaises(hand.HandError):
            stimuli.drop_follow_up()
        f = stimuli.follow_up(T0 + 60, "soon")
        self.at(61)
        stimuli.raise_due()
        self.assertEqual(stimuli.drop_follow_up(id=f["id"]), [])  # already raised: nothing left to drop
        self.assertEqual(stimuli.drop_follow_up(id=999), [])

    def test_answering_the_thing_a_follow_up_is_about_resolves_it(self):
        parked = stimuli.follow_up(T0 + 3600, "send the japanese reply", ref="x:111")
        stimuli.follow_up(T0 + 3600, "did ana ship?", ref="x:222")
        stimuli.follow_up(T0 + 3600, "no ref at all")
        got = stimuli.resolve(["111"])
        self.assertEqual(got, [{"id": parked["id"], "about": "send the japanese reply", "ref": "x:111"}])
        self.at(3601)
        self.assertEqual(stimuli.raise_due(), 2)
        self.assertEqual(stimuli.follow_ups("resolved")["follow_ups"][0]["id"], parked["id"])

    def test_resolve_spares_the_follow_ups_it_is_told_to_keep(self):
        f = stimuli.follow_up(T0 + 3600, "see whether ana answers back", ref="111")
        self.assertEqual(stimuli.resolve(["111"], keep=[f["id"]]), [])
        self.assertEqual(len(stimuli.follow_ups()["follow_ups"]), 1)


class CliTest(Scratch):
    def hand(self, *args, body=None):
        out = subprocess.run([sys.executable, str(HAND), *args], input=json.dumps(body) if body is not None else "",
                             capture_output=True, text=True, env=os.environ)
        return out.returncode, json.loads(out.stdout) if out.stdout.strip() else None

    def test_add_take_settle_through_the_hand(self):
        code, got = self.hand("add", body={"surface": "x", "kind": "mention", "ref": "1", "text": "gm", "who": "ana"})
        self.assertEqual((code, got["added"]), (0, True))
        code, got = self.hand("take", "--max", "5")
        self.assertEqual(code, 0)
        self.assertEqual(got["stimuli"][0]["who"], "ana")
        code, _ = self.hand("settle", got["tick"])
        self.assertEqual(code, 0)
        self.assertEqual(self.hand("counts")[1]["done"], 1)

    def test_follow_up_through_the_hand(self):
        code, got = self.hand("follow-up", body={"in_hours": 2, "about": "check the pr"})
        self.assertEqual(code, 0)
        self.assertEqual(got["due_at"], T0 + 7200)

    def test_drop_follow_up_through_the_hand(self):
        _, got = self.hand("follow-up", body={"in_hours": 2, "about": "check the pr", "ref": "teenydroid/brain#4"})
        code, dropped = self.hand("drop-follow-up", body={"id": got["id"]})
        self.assertEqual((code, dropped["dropped"]), (0, [got["id"]]))
        self.assertEqual(self.hand("follow-ups")[1]["follow_ups"], [])


if __name__ == "__main__":
    unittest.main()
