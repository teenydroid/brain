"""hands/build idea: the one build idea Teeny keeps a UTC day (who wants it,
where the need shows, a one-line pitch), and the daily job made from it: the
idea's brief when one is waiting, the standing brief when none is, and the
builds already shipped named in both so nothing is built twice."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from gh.support import T0, Scratch
from teeny_hands import hand
from teeny_hands.build import ideas, jobs

BUILD = Path(__file__).resolve().parents[2] / "build"
DAY = 86400

IDEA = {
    "title": "a changelog differ for agent prompts",
    "who": "@ana on X, and the Show HN thread on prompt drift",
    "source": "https://news.ycombinator.com/item?id=100",
    "pitch": "show what changed between two versions of a prompt file, line by line",
}


def ship(repo: str, summary: str, kind: str = "daily", at: float = T0) -> None:
    """A shipped job in the ledger, without the push."""
    conn = jobs.db()
    try:
        conn.execute(
            "INSERT INTO build_jobs (kind, slug, repo, text, workdir, status, claimed_at, shipped_at, summary)"
            " VALUES (?, ?, ?, 't', 'state/workbench/x', 'shipped', ?, ?, ?)",
            (kind, f"slug-{repo}", repo, at, at, summary),
        )
        conn.commit()
    finally:
        conn.close()


class IdeaTest(Scratch):
    def at(self, offset: float) -> None:
        os.environ["TEENY_NOW"] = str(T0 + offset)

    def test_an_idea_is_kept_with_who_wants_it_and_where_the_need_shows(self):
        out = ideas.keep(IDEA)
        self.assertEqual(out["idea"], {"day": "2026-09-14", **IDEA, "created_at": T0})
        self.assertEqual(ideas.listing()["today"], out["idea"])

    def test_one_idea_a_utc_day_and_a_second_replaces_nothing(self):
        ideas.keep(IDEA)
        with self.assertRaises(hand.Refused) as caught:
            ideas.keep({**IDEA, "title": "something else"})
        self.assertEqual(caught.exception.detail["rule"], "one_a_day")
        self.assertEqual(caught.exception.detail["idea"]["title"], IDEA["title"])
        self.assertEqual(ideas.listing()["today"]["title"], IDEA["title"])
        self.at(DAY)
        self.assertIsNone(ideas.listing()["today"])
        ideas.keep({**IDEA, "title": "tomorrow's thing"})
        self.assertEqual([i["title"] for i in ideas.listing()["ideas"]], ["tomorrow's thing", IDEA["title"]])

    def test_every_field_is_needed_and_nothing_else_is_taken(self):
        for bad in (None, [], "x", {**IDEA, "extra": 1}, {**IDEA, "who": ""}, {**IDEA, "source": "  "},
                    {**IDEA, "pitch": 3}, {k: v for k, v in IDEA.items() if k != "title"}):
            with self.assertRaises(hand.HandError, msg=repr(bad)):
                ideas.keep(bad)
        self.assertIsNone(ideas.listing()["today"])

    def test_each_field_is_one_line_and_cut_to_its_length(self):
        out = ideas.keep({**IDEA, "pitch": "line one\n\na second line\n" + "x" * 1000,
                          "title": "  a   spaced\ttitle "})["idea"]
        self.assertEqual(out["title"], "a spaced title")
        self.assertNotIn("\n", out["pitch"])
        self.assertEqual(len(out["pitch"]), ideas.LIMITS["pitch"])

    def test_words_the_guards_lexicon_holds_are_refused(self):
        with self.assertRaises(hand.Refused) as caught:
            ideas.keep({**IDEA, "pitch": "a tool that tells people to kill yourself"})
        self.assertEqual(caught.exception.detail["rule"], "guard")
        self.assertIsNone(ideas.listing()["today"])

    def test_the_verbs_by_the_command_line(self):
        env = {**os.environ}
        kept = subprocess.run([sys.executable, str(BUILD), "idea"], input=json.dumps(IDEA), capture_output=True,
                              text=True, env=env)
        self.assertEqual(kept.returncode, 0, kept.stderr)
        self.assertEqual(json.loads(kept.stdout)["idea"]["title"], IDEA["title"])
        again = subprocess.run([sys.executable, str(BUILD), "idea"], input=json.dumps(IDEA), capture_output=True,
                               text=True, env=env)
        self.assertEqual(again.returncode, 2)
        self.assertEqual(json.loads(again.stdout)["rule"], "one_a_day")
        shown = subprocess.run([sys.executable, str(BUILD), "ideas", "--limit", "5"], capture_output=True, text=True,
                               env=env, stdin=subprocess.DEVNULL)
        out = json.loads(shown.stdout)
        self.assertEqual((shown.returncode, out["today"]["title"], len(out["ideas"])), (0, IDEA["title"], 1))


class DailyJobTest(Scratch):
    def at(self, offset: float) -> None:
        os.environ["TEENY_NOW"] = str(T0 + offset)

    def test_the_daily_job_builds_the_idea_that_is_waiting(self):
        ideas.keep(IDEA)
        job = jobs.next_job()["job"]
        self.assertEqual(job["kind"], "daily")
        for part in IDEA.values():
            self.assertIn(part, job["request"])
        self.assertIn("standard library only", job["request"])
        self.assertIn("what it could not do", job["request"])
        self.assertEqual(ideas.listing()["today"]["job_id"], job["job_id"])

    def test_an_idea_kept_the_evening_before_is_tomorrows_daily_job(self):
        # The daily job is made at the first desk run after UTC midnight,
        # hours before that day's idea could be drafted.
        self.at(17 * 3600)
        ideas.keep(IDEA)
        self.at(DAY + 60)
        job = jobs.next_job()["job"]
        self.assertEqual(job["repo"], "daily-2026-09-15")
        self.assertIn(IDEA["title"], job["request"])

    def test_an_idea_is_built_once_and_a_stale_one_never(self):
        ideas.keep(IDEA)
        first = jobs.next_job()["job"]
        jobs.done({"job_id": first["job_id"], "status": "failed", "summary": "no", "tests": "none"})
        self.at(DAY)
        second = jobs.next_job()["job"]
        self.assertEqual(second["request"], jobs.DAILY_BRIEF)
        jobs.done({"job_id": second["job_id"], "status": "failed", "summary": "no", "tests": "none"})
        ideas.keep({**IDEA, "title": "left too long"})
        self.at(DAY + ideas.FRESH_SECS + DAY)
        self.assertNotIn("left too long", jobs.next_job()["job"]["request"])

    def test_with_no_idea_the_standing_brief_holds_the_same_bar(self):
        job = jobs.next_job()["job"]
        self.assertEqual(job["request"], jobs.DAILY_BRIEF)
        for part in ("a named person or a linked thread", "use every day yourself", "every package index",
                     "extending something you already shipped", "standard library only", "README", "tests that pass",
                     "what you built, for whom, and what it could not do"):
            self.assertIn(part, jobs.DAILY_BRIEF)
        self.assertLess(len(jobs.DAILY_BRIEF), 700)

    def test_the_daily_job_names_what_already_shipped_so_it_is_not_built_again(self):
        ship("csvstat", "csvstat: column statistics for a CSV file\nmore detail", at=T0 - DAY)
        ship("req-3-dice", "a dice roller", kind="request", at=T0 - 2 * DAY)
        ship("brain", "a change to myself", kind="self")
        for idea in (None, IDEA):
            if idea:
                self.at(DAY)
                ideas.keep(idea)
            job = jobs.next_job()["job"]
            self.assertIn("- csvstat: csvstat: column statistics for a CSV file\n- req-3-dice: a dice roller", job["request"])
            self.assertIn("Don't build any of these again; extending one is fine.", job["request"])
            self.assertNotIn("a change to myself", job["request"])
            self.assertNotIn("more detail", job["request"])
            jobs.done({"job_id": job["job_id"], "status": "failed", "summary": "no", "tests": "none"})
