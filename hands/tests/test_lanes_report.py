"""bin/lanes report (bin/lib/lanes_report.py): what the lanes spent, against their caps, log only."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import lanes_report  # noqa: E402

TODAY = dt.date(2026, 11, 28)
LANES = """
[[lane]]
id = "locked"
model = "glm-5.3-flash"
fallbacks = []
match = ["agent:x-reply:*"]
daily_tokens = 1000
thread_daily_tokens = 300

[[lane]]
id = "wake"
match = ["agent:mind:*"]
"""


def row(lane: str, session: str, total: int, model: str = "local-openai/glm-5.3-flash") -> str:
    return json.dumps({"ts": 0, "lane": lane, "session": session, "model": model, "input": total, "output": 0, "total": total})


class LanesReportTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.toml = self.root / "lanes.toml"
        self.toml.write_text(LANES)
        self.ledger = self.root / "lanes"
        self.ledger.mkdir()

    def day(self, day: str, *lines: str) -> None:
        (self.ledger / f"usage-{day}.jsonl").write_text("\n".join(lines) + "\n")

    def test_totals_per_lane_and_the_threads_that_went_over_their_caps(self):
        self.day("2026-11-27", row("locked", "agent:x-reply:x:1", 400), row("locked", "agent:x-reply:x:2", 700))
        self.day("2026-11-28", row("locked", "agent:x-reply:x:1", 100), row("wake", "agent:mind:cron:teeny.mind", 5000, "local-openai/kimi-k3"),
                 '{"lane": "lo')
        out = lanes_report.report(self.toml, self.ledger, 2, today=TODAY)
        self.assertEqual(out["days"], ["2026-11-27", "2026-11-28"])
        self.assertFalse(out["enforced"])
        locked = out["lanes"]["locked"]
        self.assertEqual((locked["tokens"], locked["calls"], locked["daily_cap"], locked["days_over"]), (1200, 3, 1000, ["2026-11-27"]))
        self.assertEqual(out["lanes"]["wake"]["models"], {"local-openai/kimi-k3": 5000})
        self.assertIsNone(out["lanes"]["wake"]["daily_cap"])
        top = {t["session"]: t for t in out["top_threads"]}
        self.assertEqual(out["top_threads"][0]["session"], "agent:mind:cron:teeny.mind")
        self.assertEqual(top["agent:x-reply:x:2"]["days_over"], ["2026-11-27"])
        self.assertEqual((top["agent:x-reply:x:1"]["tokens"], top["agent:x-reply:x:1"]["peak_day"]), (500, 400))
        self.assertEqual(top["agent:x-reply:x:1"]["days_over"], ["2026-11-27"])

    def test_only_the_days_asked_for(self):
        self.day("2026-11-27", row("locked", "agent:x-reply:x:1", 400))
        self.day("2026-11-28", row("locked", "agent:x-reply:x:1", 10))
        self.assertEqual(lanes_report.report(self.toml, self.ledger, 1, today=TODAY)["lanes"]["locked"]["tokens"], 10)

    def test_no_ledger_and_a_broken_lanes_toml_are_an_empty_report_not_a_failure(self):
        self.toml.write_text("not toml [")
        out = lanes_report.report(self.toml, self.root / "missing", 3, today=TODAY)
        self.assertEqual((out["lanes"], out["top_threads"]), ({}, []))
        with self.assertRaises(ValueError):
            lanes_report.report(self.toml, self.ledger, 0, today=TODAY)

    def test_the_cli_prints_json_and_refuses_a_bad_day_count(self):
        script = REPO / "bin" / "lib" / "lanes_report.py"
        proc = subprocess.run([sys.executable, "-I", str(script), str(self.toml), str(self.ledger), "2"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("lanes", json.loads(proc.stdout))
        bad = subprocess.run([sys.executable, "-I", str(script), str(self.toml), str(self.ledger), "zero"], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)


if __name__ == "__main__":
    unittest.main()
