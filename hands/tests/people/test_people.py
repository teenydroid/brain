"""hands/people (docs/roadmap.md, A9): a record per person from their own words
and Teeny's promises, a promise recorded a week ago raises a follow-up today,
and a beat's prompt carries the records of the people in that beat only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))

from teeny_hands import hand, requests, stimuli  # noqa: E402
from teeny_hands.people import records  # noqa: E402

HAND = HERE.parents[1] / "people"
T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
DAY = 86400.0


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ.pop("DRY_RUN", None)
        self.at(T0)
        hand._ENV_CACHE = None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def at(self, t: float) -> None:
        os.environ["TEENY_NOW"] = str(t)

    def said(self, surface, who, ref, text, at):
        stimuli.add(surface, "mention" if surface == "x" else "message", ref, text, who=who, at=at)

    def regulars(self):
        self.said("x", "Ann", "1", "hey teeny, can you build me a plant timer?", T0 - 3 * DAY)
        self.said("x", "@ann", "2", "any news on the timer?", T0 - DAY)
        self.said("x", "bob", "3", "gm", T0 - DAY)
        self.said("mail", "Cleo <cleo@example.org>", "m1", "hello", T0 - 5 * DAY)
        self.said("mail", "cleo@example.org", "m2", "hello again", T0 - 2 * DAY)
        requests.record("x:mentions", "1", "ann", "can you build me a plant timer?")


class RecordTest(Scratch):
    def test_a_record_holds_their_words_and_requests_and_nothing_else(self):
        self.regulars()
        rec = records.record("x", "@ANN")
        self.assertEqual((rec["who"], rec["messages"], rec["days"], rec["regular"]), ("ann", 2, 2, True))
        self.assertEqual(rec["first_wrote"], "2026-09-11")
        self.assertEqual([s["ref"] for s in rec["said"]], ["1", "2"])
        self.assertEqual(rec["asked"][0]["asked"], "can you build me a plant timer?")
        self.assertEqual(set(rec), {"surface", "who", "messages", "days", "regular", "first_wrote", "last_wrote", "said", "asked"})

    def test_notes_teeny_wrote_about_someone_are_not_in_their_record(self):
        self.regulars()
        stimuli.note("ann seems lonely", who="ann")
        self.assertNotIn("lonely", json.dumps(records.record("x", "ann")))

    def test_a_skipped_request_is_left_out(self):
        self.regulars()
        conn = hand.db()
        conn.execute("UPDATE build_requests SET status = 'skipped'")
        conn.close()
        self.assertNotIn("asked", records.record("x", "ann"))

    def test_mail_matches_by_address(self):
        self.regulars()
        rec = records.record("mail", "CLEO@example.org")
        self.assertEqual((rec["messages"], rec["regular"]), (2, True))

    def test_list_names_the_regulars(self):
        self.regulars()
        got = records.listing()["people"]
        self.assertEqual([(p["surface"], p["who"]) for p in got], [("x", "ann"), ("mail", "cleo@example.org")])
        self.assertEqual(len(records.listing(regulars_only=False)["people"]), 3)

    def test_nobody_has_no_record(self):
        self.assertIsNone(records.record("x", "nobody"))


class PromiseTest(Scratch):
    def test_a_promise_recorded_a_week_ago_raises_a_follow_up_today(self):
        self.regulars()
        made = records.promise({"surface": "x", "who": "ann", "what": "ship the plant timer", "due_in_hours": 168, "ref": "x:2"})
        self.at(T0 + 3 * DAY)
        self.assertEqual(stimuli.raise_due(), 0)
        self.at(T0 + 7 * DAY + 60)
        self.assertEqual(stimuli.raise_due(), 1)
        raised = [s for s in stimuli.recent()["stimuli"] if s["surface"] == "self"]
        self.assertEqual(len(raised), 1)
        self.assertIn(f"a promise you made comes due (promise #{made['promise']})", raised[0]["text"])
        self.assertNotIn("ann", raised[0]["text"])  # the waiting list rides in every beat: it names no one
        self.assertEqual(raised[0]["payload"]["ref"], f"promise:{made['promise']}")
        # the beat that sees it gets ann's record, with the promise
        text = "\n".join(records.prompt_block([raised[0]]))
        self.assertIn("ship the plant timer", text)
        self.assertTrue(records.record("x", "ann")["promises"][0]["overdue"])

    def test_answering_a_post_does_not_clear_a_promise(self):
        self.regulars()
        records.promise({"surface": "x", "who": "ann", "what": "ship it", "due_in_hours": 24, "ref": "x:2"})
        self.assertEqual(stimuli.resolve(["x:2", "2"]), [])

    def test_a_kept_promise_clears_its_follow_up(self):
        self.regulars()
        made = records.promise({"surface": "x", "who": "ann", "what": "ship it", "due_in_hours": 24})
        out = records.close({"id": made["promise"], "note": "shipped"}, "kept")
        self.assertTrue(out["follow_up_cleared"])
        self.at(T0 + 2 * DAY)
        self.assertEqual(stimuli.raise_due(), 0)
        with self.assertRaises(hand.Refused):
            records.close({"id": made["promise"]}, "dropped")

    def test_no_promise_to_someone_who_never_wrote(self):
        with self.assertRaises(hand.Refused) as refused:
            records.promise({"surface": "x", "who": "stranger", "what": "follow you", "due_in_hours": 2})
        self.assertEqual(refused.exception.detail["rule"], "stranger")

    def test_a_promise_is_due_within_ninety_days(self):
        self.regulars()
        for hours in (0, -1, 91 * 24, True):
            with self.assertRaises(hand.HandError):
                records.promise({"surface": "x", "who": "ann", "what": "x", "due_in_hours": hours})

    def test_forget(self):
        self.regulars()
        records.promise({"surface": "x", "who": "ann", "what": "ship it", "due_in_hours": 24})
        self.assertEqual(records.forget("x", "ann")["promises"], 1)
        self.at(T0 + 2 * DAY)
        self.assertEqual(stimuli.raise_due(), 0)


class PromptTest(Scratch):
    def test_a_beat_carries_only_the_records_of_its_own_people(self):
        self.regulars()
        records.promise({"surface": "x", "who": "ann", "what": "ship the plant timer", "due_in_hours": 48})
        batch = [{"surface": "x", "kind": "mention", "who": "bob", "ref": "3", "text": "gm"}]
        self.assertEqual(records.prompt_block(batch), [])  # bob is no regular, and ann is not in this beat
        batch.append({"surface": "x", "kind": "mention", "who": "Ann", "ref": "2", "text": "any news?"})
        text = "\n".join(records.prompt_block(batch))
        self.assertIn("ann on x", text)
        self.assertIn("ship the plant timer", text)
        self.assertIn("plant timer?", text)  # what she asked to build
        self.assertNotIn("cleo", text)
        self.assertNotIn("bob", text)

    def test_a_due_promise_brings_its_persons_record(self):
        self.regulars()
        made = records.promise({"surface": "mail", "who": "cleo@example.org", "what": "send the notes", "due_in_hours": 1})
        batch = [{"surface": "self", "kind": "follow_up", "ref": "1", "text": "...",
                  "payload": {"ref": f"promise:{made['promise']}"}}]
        text = "\n".join(records.prompt_block(batch))
        self.assertIn("cleo@example.org on mail", text)
        self.assertIn("send the notes", text)
        self.assertNotIn("ann", text)


class PromiseAgeTest(Scratch):
    def test_a_promise_in_a_beat_says_how_long_ago_it_was_made(self):
        # A UTC date beside an IST header read as "two days ago" for a
        # promise made that morning (2026-11-13).
        self.regulars()
        records.promise({"surface": "x", "who": "ann", "what": "ship the plant timer", "due_in_hours": 48})
        self.at(T0 + 4 * 3600)
        text = "\n".join(records.prompt_block([{"surface": "x", "kind": "mention", "who": "ann", "ref": "2", "text": "?"}]))
        self.assertIn("you promised (made 4 h ago, due in 44 h; promise #1)", text)

    def test_an_overdue_promise_says_by_how_much(self):
        self.regulars()
        records.promise({"surface": "x", "who": "ann", "what": "ship it", "due_in_hours": 2})
        self.at(T0 + 3 * DAY)
        text = "\n".join(records.prompt_block([{"surface": "x", "kind": "mention", "who": "ann", "ref": "2", "text": "?"}]))
        self.assertIn("made 3 days ago, overdue by 2 days", text)


class CliTest(Scratch):
    def run_people(self, *args, stdin=None):
        done = subprocess.run([sys.executable, str(HAND), *args], input=stdin, capture_output=True, text=True,
                              env=dict(os.environ), timeout=60)
        return done.returncode, json.loads(done.stdout)

    def test_the_cli(self):
        self.regulars()
        code, out = self.run_people("promise", stdin='{"surface": "x", "who": "ann", "what": "ship it", "due_in_hours": 5}')
        self.assertEqual(code, 0, out)
        code, out = self.run_people("get", "x", "ann")
        self.assertEqual((code, out["person"]["promises"][0]["what"]), (0, "ship it"))
        self.assertEqual(self.run_people("get", "x", "nobody")[0], 2)
        self.assertEqual(self.run_people("list")[1]["people"][0]["open_promises"], 1)


if __name__ == "__main__":
    unittest.main()
