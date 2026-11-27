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
    def test_world_scan_is_a_habit_that_listens_twice_a_day(self):
        book = load("world-scan")
        self.assertEqual((book["output"], book["nudge"], book["max_per_day"]), ("none", True, 2))
        self.assertEqual(runs(book), {
            "heard": ["hands/listen", "scan", "--max", "5"],
            "hn-top": ["hands/hn", "top", "--max", "6"],
            "moltbook-hot": ["hands/moltbook", "feed", "--sort", "hot", "--max", "5", "--brief"],
        })

    def test_the_posting_playbooks_read_what_was_heard(self):
        for name in ("moltbook-daily", "moltbook-evening"):
            got = runs(load(name))
            self.assertEqual((got["hn-top"][:2], got["hn-show"][:2]), (["hands/hn", "top"], ["hands/hn", "show"]), name)
        for name in ("morning", "take", "moltbook-daily", "moltbook-evening", "moltbook-general"):
            self.assertEqual(runs(load(name))["heard"][:3], ["hands/listen", "digest", "--days"], name)

    def test_a_read_of_somebody_elses_site_never_costs_the_run(self):
        for name in ("world-scan", "morning", "take", "moltbook-daily", "moltbook-evening", "moltbook-general"):
            for i in load(name)["inputs"]:
                if i["argv"][0] in ("hands/hn", "hands/listen"):
                    self.assertTrue(i["optional"], (name, i["name"]))
                    self.assertFalse(i["required"], (name, i["name"]))

    def test_the_cron_jobs_for_it_fit_the_cap(self):
        jobs = tomllib.loads((BRAIN / "cron.toml").read_text())["jobs"]
        self.assertLessEqual(len(jobs), cron_policy.MAX_JOBS)
        self.assertEqual(jobs["world-scan"]["command"], "bin/act world-scan")
        # Two listens a day from one job: the cap has no room for a second.
        self.assertEqual(len(cron_policy.fire_minutes(jobs["world-scan"]["schedule"], "world-scan")), 2)
        for name in ("world-scan", "gm"):
            self.assertTrue(jobs[name].get("enabled", True), name)
        self.assertNotIn("news-scan", jobs)
        self.assertNotIn("roast", jobs)
        # Kept in the tree, unscheduled.
        self.assertTrue((BRAIN / "playbooks" / "roast.md").is_file())
        self.assertTrue((BRAIN / "playbooks" / "news-scan.md").is_file())

    def test_the_builder_is_retired_and_nothing_schedules_it(self):
        """2026-11-24: the builder is retired; the agentic mind ships things
        itself (docs/mind.md). Its jobs, its agent and the playbooks that fed
        it are gone, and no job or playbook left calls a verb hands/build no
        longer has."""
        jobs = tomllib.loads((BRAIN / "cron.toml").read_text())["jobs"]
        pinned = tomllib.loads((REPO / "evals" / "cron.pinned.toml").read_text())["jobs"]
        for gone in ("builder", "build-ship", "build-idea", "pick-request", "self-improve"):
            self.assertNotIn(gone, jobs)
            self.assertNotIn(gone, pinned)
        every = {**jobs, **pinned}
        self.assertEqual([n for n, j in every.items() if j.get("agent") not in (None, "mind", "operator", "babysitter")], [])
        self.assertEqual([n for n, j in every.items() if "build-ship" in j.get("command", "")], [])
        self.assertFalse((REPO / "bin" / "build-ship").exists())
        self.assertFalse((BRAIN / "agents" / "builder.toml").exists())
        self.assertFalse((BRAIN / "agents" / "builder-self.md").exists())
        for name in ("build-idea", "pick-request", "self-improve", "upstream-pick"):
            self.assertFalse((BRAIN / "playbooks" / f"{name}.md").exists(), name)
        kept = ("jobs", "frozen", "self-sync")
        for path in sorted((BRAIN / "playbooks").glob("*.md")):
            book = load(path.stem)
            self.assertFalse(str(book["output"]).startswith("hand:build"), path.stem)
            for i in book["inputs"]:
                if i["argv"][0] == "hands/build":
                    self.assertIn(i["argv"][1], kept, (path.stem, i["argv"]))
        self.assertNotIn("bin/build-ship", cron_policy.BRAIN_SCRIPTS)
        self.assertNotIn("builder", cron_policy.AGENT_JOB_AGENTS)

    def test_the_only_cron_post_on_x_is_the_gm(self):
        """2026-11-27, the operator: "just one cron post on X which is the gm
        post where it combines its context (its history of actions), the news
        and a gm style message". Every enabled job, mine or pinned, that puts
        an original post on X is the gm; replies are not posts."""
        jobs = tomllib.loads((BRAIN / "cron.toml").read_text())["jobs"]
        pinned = tomllib.loads((REPO / "evals" / "cron.pinned.toml").read_text())["jobs"]
        on_x = []
        for name, job in {**jobs, **pinned}.items():
            if not job.get("enabled", True):
                continue
            argv = str(job.get("command", "")).split()
            if argv[:1] == ["bin/studio"]:
                on_x.append(name)
            elif argv[:1] == ["bin/act"]:
                book = load(argv[1])
                output = str(book["output"])
                surface = book.get("surface") or "x"
                if surface == "x" and (output == "post" or output.startswith(("hand:x", "hand:brief", "hand:firsts"))):
                    on_x.append(name)
        self.assertEqual(on_x, ["gm"])
        self.assertEqual((jobs["gm"]["schedule"], jobs["gm"].get("tz"), jobs["gm"]["command"]),
                         ("30 18 * * *", "Asia/Kolkata", "bin/act gm"))
        gm = load("gm")
        self.assertEqual((gm["output"], gm["max_per_day"]), ("post", 1))
        names = [i["name"] for i in gm["inputs"]]
        # The gap and the day's cap first, then the news, then my own history.
        self.assertEqual(names[0], "slot")
        self.assertTrue({"heard", "hn-top"} <= set(names), names)
        self.assertTrue({"lately", "material"} <= set(names), names)
        text = (BRAIN / "playbooks" / "gm.md").read_text()
        self.assertIn('"format": "gm"', text)
        self.assertNotIn("receipt", text.lower())

    def test_the_old_x_slots_are_kept_switched_off(self):
        jobs = tomllib.loads((BRAIN / "cron.toml").read_text())["jobs"]
        slots = {name: (jobs[name]["schedule"], jobs[name].get("tz"), jobs[name]["command"])
                 for name in ("studio-0430", "studio-1500", "studio-sunday", "studio-2300", "shiplog")}
        for name in slots:
            self.assertIs(jobs[name].get("enabled"), False, name)
        self.assertEqual(slots, {
            "studio-0430": ("30 4 * * *", "Asia/Kolkata", "bin/studio --slot 0430 --legacy x-quote --detach"),
            "studio-1500": ("0 15 * * 1-6", "Asia/Kolkata", "bin/studio --slot 1500 --legacy take --detach"),
            "studio-sunday": ("0 15 * * 0", "Asia/Kolkata", "bin/studio --slot 1500 --legacy growth --detach"),
            "studio-2300": ("0 23 * * *", "Asia/Kolkata", "bin/studio --slot 2300 --legacy show --detach"),
            "shiplog": ("10 23 * * *", "Asia/Kolkata", "bin/act shiplog"),
        })
        # Three a day since 2026-11-23: the 09:30 slot is gone.
        self.assertEqual(sorted(n for n in jobs if n.startswith("studio-")),
                         ["studio-0430", "studio-1500", "studio-2300", "studio-sunday"])
        # The replaced jobs are gone from cron: their slots are the studio's,
        # and each studio job names the old playbook it runs with STUDIO=0.
        # brief and peer-quote were taken out of cron on 2026-11-27 (the operator's call).
        for gone in ("brief", "peer-quote", "episode", "goals", "giveaway-plan", "reddit-browse"):
            self.assertNotIn(gone, jobs)
        for gone in ("x-quote", "morning", "take", "show", "growth"):
            self.assertNotIn(gone, jobs)
            self.assertTrue((BRAIN / "playbooks" / f"{gone}.md").is_file(), gone)
            self.assertIn("LEGACY since the studio cutover", (BRAIN / "playbooks" / f"{gone}.md").read_text())
        # The four daily slots (04:30, 09:30, 15:00, 23:00) keep five hours between them.
        starts = sorted([4 * 60 + 30, 9 * 60 + 30, 15 * 60, 23 * 60])
        gaps = [b - a for a, b in zip(starts, starts[1:])] + [starts[0] + 24 * 60 - starts[-1]]
        self.assertGreaterEqual(min(gaps), 5 * 60)
        # A legacy post job off its own slot reads the gap first and skips when it is shut.
        for name in ("x-quote", "growth", "shiplog"):
            self.assertIn('run = "hands/x slot"', (BRAIN / "playbooks" / f"{name}.md").read_text())
        for gone in ("humans", "milestone", "pr-merged", "wallet-receipt", "cost-of-living", "bounty-winner"):
            self.assertNotIn(gone, jobs)
            self.assertTrue((BRAIN / "playbooks" / f"{gone}.md").exists())
        self.assertNotIn("shipped", jobs)
        self.assertFalse((BRAIN / "playbooks" / "shipped.md").exists())
        # The posts are tagged for the lab, and take and show give way to a queued brief.
        for name in ("take", "show"):
            text = (BRAIN / "playbooks" / f"{name}.md").read_text()
            self.assertIn("brief", text)
            self.assertIn('"format"', text)
        self.assertIn("quote_of", (BRAIN / "playbooks" / "take.md").read_text())
        self.assertIn("images", (BRAIN / "playbooks" / "show.md").read_text())


if __name__ == "__main__":
    unittest.main()
