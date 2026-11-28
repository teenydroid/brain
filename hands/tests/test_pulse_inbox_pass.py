"""The pulse says when the mind's wakes skipped their inbox pass
(teeny-guardrails records one line per mind run)."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import pulse  # noqa: E402

T0 = 1_795_996_800.0  # 2026-11-30T00:00:00Z


class InboxPassPulse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "state" / "mind").mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"TEENY_ROOT": str(self.root), "TEENY_NOW": str(T0)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)

    def record(self, *rows):
        (self.root / "state" / "mind" / "inbox-pass.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_no_record_says_nothing(self):
        self.assertIsNone(pulse.inbox_passes(T0 - 86400))

    def test_a_wake_given_the_block_that_did_nothing_is_counted_and_dated(self):
        self.record(
            {"at": "2026-11-29T10:00:00.000Z", "agent": "mind", "block": True, "owed": True},
            {"at": "2026-11-29T10:30:00.000Z", "agent": "mind", "block": True, "owed": True},
            {"at": "2026-11-29T11:00:00.000Z", "agent": "mind", "block": True, "owed": False},
            {"at": "2026-11-29T11:30:00.000Z", "agent": "mind", "block": False, "owed": False},
            {"at": "2026-11-29T11:31:00.000Z", "agent": "worker", "block": True, "owed": True},
            {"at": "2026-11-20T10:00:00.000Z", "agent": "mind", "block": True, "owed": True},
        )
        got = pulse.inbox_passes(T0 - 86400)
        self.assertEqual(got, {"wakes_given_the_block": 3, "wakes_that_skipped_it": 2,
                               "last_skipped": ["2026-11-29T10:30:00.000Z", "2026-11-29T10:00:00.000Z"]})

    def test_a_log_that_is_not_text_is_no_record_and_never_breaks_the_pulse(self):
        (self.root / "state" / "mind" / "inbox-pass.jsonl").write_bytes(b"\xff\xfe not utf-8\n")
        self.assertIsNone(pulse.inbox_passes(T0 - 86400))
        self.assertNotIn("inbox_pass", pulse.pulse(1))

    def test_the_pulse_carries_it(self):
        self.record({"at": "2026-11-29T10:00:00.000Z", "agent": "mind", "block": True, "owed": True})
        self.assertEqual(pulse.pulse(1)["inbox_pass"]["wakes_that_skipped_it"], 1)


if __name__ == "__main__":
    unittest.main()
