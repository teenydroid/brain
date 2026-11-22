"""hands/story record-run and runs: bin/studio's run records kept in
`studio_runs`, written under DRY_RUN too (the record of a run that posted
nothing), once per run id, and read back newest first."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HANDS = Path(__file__).resolve().parents[2]


def record(run_id: str, at: float, outcome: str = "picked", **extra) -> dict:
    return {"id": run_id, "at_unix": at, "outcome": outcome, "slot": "morning", "seed": 7, "explore": False,
            "models": {"writer": "w", "editor": "e"}, "pitches": [{"id": "p1"}], "drafts": [{"id": "d1"}, {"id": "d2"}],
            "calls": {"total": 6}, "secs": 12.5, "pick": {"label": "D1", "text": "hi"} if outcome == "picked" else None,
            **extra}


class RunsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        self.env = {k: v for k, v in os.environ.items() if k not in ("DRY_RUN", "TEENY_NOW")}
        self.env["TEENY_ROOT"] = str(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def hand(self, *argv: str, body: object | None = None, **env: str) -> tuple[int, dict]:
        done = subprocess.run([str(HANDS / "story"), *argv], capture_output=True, text=True, env={**self.env, **env},
                              input=json.dumps(body) if body is not None else "")
        return done.returncode, json.loads(done.stdout)

    def test_a_run_is_kept_and_read_back_newest_first(self):
        self.assertEqual(self.hand("record-run", body=record("r1", 100.0))[1]["recorded"], True)
        self.hand("record-run", body=record("r2", 200.0, outcome="vetoed", reason="none worth a slot"))
        code, got = self.hand("runs")
        self.assertEqual(code, 0)
        self.assertEqual([r["id"] for r in got["runs"]], ["r2", "r1"])
        newest, older = got["runs"]
        self.assertEqual((newest["outcome"], newest["reason"], newest["pick"]), ("vetoed", "none worth a slot", None))
        self.assertEqual((older["pitches"], older["drafts"], older["calls"]), (1, 2, 6))
        self.assertEqual(older["models"], {"writer": "w", "editor": "e"})
        self.assertNotIn("record", older)
        full = self.hand("runs", "--full", "--limit", "1")[1]["runs"]
        self.assertEqual(len(full), 1)
        self.assertEqual(full[0]["record"]["drafts"], [{"id": "d1"}, {"id": "d2"}])

    def test_a_dry_run_is_recorded_all_the_same(self):
        code, got = self.hand("record-run", body=record("dry1", 1.0), DRY_RUN="1")
        self.assertEqual((code, got["recorded"]), (0, True))
        self.assertEqual(len(self.hand("runs")[1]["runs"]), 1)

    def test_a_run_is_recorded_once(self):
        self.hand("record-run", body=record("r1", 1.0))
        self.assertEqual(self.hand("record-run", body=record("r1", 1.0))[1]["recorded"], False)
        self.assertEqual(len(self.hand("runs")[1]["runs"]), 1)

    def test_a_bad_record_is_an_error(self):
        for body in ({"at_unix": 1.0, "outcome": "picked"}, {"id": "x", "outcome": "picked"},
                     {"id": "x", "at_unix": 1.0, "outcome": "posted"}, ["not", "an", "object"]):
            code, got = self.hand("record-run", body=body)
            self.assertEqual(code, 1, body)
            self.assertFalse(got["ok"])


if __name__ == "__main__":
    unittest.main()


class SlotTest(RunsTest):
    """One run a slot (review of PR #379): a slot is claimed before any model
    turn, a second claim is refused, and a slot its run left `posting` (it
    died while handing hands/x a post) is marked in doubt by the next claim
    and never retried."""

    def claim(self, slot: str, run: str, **extra) -> dict:
        code, got = self.hand("slot-claim", body={"slot_id": slot, "run_id": run, **extra})
        self.assertEqual(code, 0, got)
        return got

    def mark(self, slot: str, run: str, status: str, **extra) -> dict:
        code, got = self.hand("slot-mark", body={"slot_id": slot, "run_id": run, "status": status, **extra})
        self.assertEqual(code, 0, got)
        return got

    def test_the_first_run_claims_the_slot_and_a_retry_finds_it_held(self):
        self.assertTrue(self.claim("2026-11-18:1500", "r1")["claimed"])
        again = self.claim("2026-11-18:1500", "r2")
        self.assertFalse(again["claimed"])
        self.assertEqual((again["slot"]["run_id"], again["slot"]["status"]), ("r1", "claimed"))
        self.assertTrue(self.claim("2026-11-18:2300", "r3")["claimed"])

    def test_a_claim_is_kept_under_dry_run_too(self):
        code, got = self.hand("slot-claim", body={"slot_id": "s", "run_id": "r1"}, DRY_RUN="1")
        self.assertTrue(got["claimed"])
        self.assertFalse(self.claim("s", "r2")["claimed"])

    def test_only_the_run_that_holds_the_slot_marks_it(self):
        self.claim("s", "r1")
        self.assertTrue(self.mark("s", "r1", "posting", intent={"text": "hi"})["marked"])
        self.assertFalse(self.mark("s", "r2", "posted")["marked"])
        self.assertTrue(self.mark("s", "r1", "posted", post_id="99")["marked"])
        held = self.claim("s", "r3")["slot"]
        self.assertEqual((held["status"], held["post_id"], held["intent"]), ("posted", "99", {"text": "hi"}))

    def test_a_slot_left_posting_is_in_doubt_at_the_next_claim_and_never_retried(self):
        self.claim("2026-11-18:1500", "r1")
        self.mark("2026-11-18:1500", "r1", "posting", intent={"text": "the post"})
        got = self.claim("2026-11-18:2300", "r2")
        self.assertEqual(got["in_doubt"], [{"slot_id": "2026-11-18:1500", "run_id": "r1", "intent": {"text": "the post"}}])
        self.assertFalse(self.claim("2026-11-18:1500", "r3")["claimed"])
        self.assertEqual(self.claim("2026-11-18:0430", "r4")["in_doubt"], [])

    def test_force_reclaims_for_the_operator(self):
        self.claim("s", "r1")
        got = self.claim("s", "r2", force=True)
        self.assertTrue(got["claimed"])
        self.assertEqual(got["replaced"]["run_id"], "r1")

    def test_bad_bodies_are_errors(self):
        code, _ = self.hand("slot-claim", body={"slot_id": "s"})
        self.assertNotEqual(code, 0)
        code, _ = self.hand("slot-mark", body={"slot_id": "s", "run_id": "r", "status": "nope"})
        self.assertNotEqual(code, 0)
