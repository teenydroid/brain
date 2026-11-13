"""Teeny's intake, as the brain in this repository wires it: the playbooks
that read the outside world load under bin/act's own rules, every read of
somebody else's site is optional (its outage never costs a post), and the
cron jobs that run them fit the job cap."""

from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))
sys.path.insert(0, str(REPO / "hands" / "lib"))

import cron_policy  # noqa: E402
import playbook  # noqa: E402

BRAIN = REPO / "brain"


def load(name: str) -> dict:
    return playbook.load(BRAIN, name, REPO / "hands")


def runs(book: dict) -> dict[str, list[str]]:
    return {i["name"]: i["argv"] for i in book["inputs"]}


class IntakeTest(unittest.TestCase):
    def test_world_scan_is_a_habit_that_reads_hacker_news_and_moltbook(self):
        book = load("world-scan")
        self.assertEqual((book["output"], book["nudge"]), ("none", True))
        self.assertEqual(runs(book), {
            "hn-top": ["hands/hn", "top", "--max", "10"],
            "hn-show": ["hands/hn", "show", "--max", "8"],
            "hn-ask": ["hands/hn", "ask", "--max", "5"],
            "moltbook-hot": ["hands/moltbook", "feed", "--sort", "hot", "--max", "5", "--brief"],
        })

    def test_build_idea_keeps_its_idea_through_the_build_hand(self):
        book = load("build-idea")
        self.assertEqual((book["output"], book["surface"], book["nudge"]), ("hand:build idea", "github", False))
        got = runs(book)
        self.assertLessEqual(len(got), playbook.MAX_INPUTS)
        for name in ("hn-show", "hn-ask", "pending", "people", "issues", "shipped", "recent"):
            self.assertIn(name, got)
        self.assertEqual(got["shipped"], ["hands/build", "jobs", "--status", "shipped", "--limit", "30"])

    def test_the_posting_playbooks_read_hacker_news_and_self_improve_reads_people(self):
        for name in ("morning", "moltbook-daily", "moltbook-evening"):
            got = runs(load(name))
            self.assertEqual((got["hn-top"][:2], got["hn-show"][:2]), (["hands/hn", "top"], ["hands/hn", "show"]), name)
        self.assertEqual(runs(load("self-improve"))["people"], ["hands/people", "list"])

    def test_self_improve_reads_the_frozen_list_before_it_proposes(self):
        book = load("self-improve")
        got = runs(book)
        self.assertLessEqual(len(got), playbook.MAX_INPUTS)
        self.assertEqual(got["frozen"], ["hands/build", "frozen"])
        frozen_input = next(i for i in book["inputs"] if i["name"] == "frozen")
        self.assertTrue(frozen_input["optional"])
        self.assertIn("hands/lib/teeny_hands/build/jobs.py", book["prompt"])

    def test_a_read_of_somebody_elses_site_never_costs_the_run(self):
        for name in ("morning", "moltbook-daily", "moltbook-evening", "build-idea"):
            for i in load(name)["inputs"]:
                if i["argv"][0] == "hands/hn":
                    self.assertTrue(i["optional"], (name, i["name"]))
                    self.assertFalse(i["required"], (name, i["name"]))

    def test_the_cron_jobs_for_it_fit_the_cap(self):
        jobs = tomllib.loads((BRAIN / "cron.toml").read_text())["jobs"]
        self.assertLessEqual(len(jobs), cron_policy.MAX_JOBS)
        self.assertEqual(jobs["world-scan"]["command"], "bin/act world-scan")
        self.assertEqual(jobs["build-idea"]["command"], "bin/act build-idea")
        for name in ("world-scan", "build-idea", "shipped"):
            self.assertTrue(jobs[name].get("enabled", True), name)
        self.assertNotIn("news-scan", jobs)
        self.assertNotIn("roast", jobs)
        # Kept in the tree, unscheduled.
        self.assertTrue((BRAIN / "playbooks" / "roast.md").is_file())
        self.assertTrue((BRAIN / "playbooks" / "news-scan.md").is_file())


if __name__ == "__main__":
    unittest.main()
