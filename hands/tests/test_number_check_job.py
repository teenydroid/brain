"""The daily number check (learning m113, issue #549): a brain-owned cron job
that wakes the mind to recompute every number the day's drafts state.

Pins that it exists, passes the cron rules, stays inside the mind's own home,
and adds no outbound surface.
"""

from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import cron_policy  # noqa: E402

CRON = REPO / "brain" / "cron.toml"
PINNED = REPO / "evals" / "cron.pinned.toml"


def _job() -> dict:
    with open(CRON, "rb") as fh:
        return tomllib.load(fh)["jobs"]["number-check"]


class NumberCheckJob(unittest.TestCase):
    def test_it_is_a_daily_mind_job(self):
        job = _job()
        self.assertEqual(job["agent"], "mind")
        self.assertNotEqual(job.get("enabled"), False)
        minute, hour, dom, mon, dow = job["schedule"].split()
        self.assertTrue(minute.isdigit() and hour.isdigit())
        self.assertEqual((dom, mon, dow), ("*", "*", "*"))

    def test_the_cron_rules_accept_it(self):
        jobs = cron_policy.load(CRON, PINNED)
        self.assertIn("teeny.number-check", [j["name"] for j in jobs])

    def test_it_is_not_a_pinned_job(self):
        with open(PINNED, "rb") as fh:
            self.assertNotIn("number-check", tomllib.load(fh).get("jobs", {}))

    def test_the_prompt_asks_for_the_check_and_the_names(self):
        prompt = _job()["prompt"]
        for needle in ("recomputes", "bad", "unverified", "notes/number-check.md", "home/tools/number-check"):
            self.assertIn(needle, prompt)

    def test_the_prompt_adds_no_outbound_surface(self):
        prompt = _job()["prompt"]
        self.assertIn("Do not edit,\npost, reply or send", prompt)
        self.assertNotIn("http", prompt)


if __name__ == "__main__":
    unittest.main()
