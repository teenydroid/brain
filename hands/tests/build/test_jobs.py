"""hands/build: claiming in order (open claim, picked request, daily),
finishing, stale claims, the requests written back, and shipping through
hands/gh's actions against the fake GitHub and local bare remotes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from gh.support import T0, Scratch, git
from teeny_hands import hand
from teeny_hands import requests as build_requests
from teeny_hands.build import jobs

BUILD = Path(__file__).resolve().parents[2] / "build"
PASSING = "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n"
FAILING = "import unittest\n\nclass T(unittest.TestCase):\n    def test_greets(self):\n        self.assertEqual('hi', 'hello')\n"


def request(text: str, status: str = "picked", source_id: str | None = None) -> int:
    conn = jobs.db()
    try:
        build_requests.record("x", source_id or text, "someone", text, conn=conn)
        row = conn.execute("SELECT id FROM build_requests WHERE source_id = ?", (source_id or text,)).fetchone()
        conn.execute("UPDATE build_requests SET status = ? WHERE id = ?", (status, row["id"]))
        return row["id"]
    finally:
        conn.close()


def request_row(request_id: int) -> dict:
    conn = jobs.db()
    try:
        return dict(conn.execute("SELECT * FROM build_requests WHERE id = ?", (request_id,)).fetchone())
    finally:
        conn.close()


class QueueTest(Scratch):
    def test_claims_the_oldest_picked_request_and_makes_its_folder(self):
        request("make me a pomodoro timer", status="pending")
        first = request("can you build a dice roller cli")
        request("please make a haiku counter")
        out = jobs.next_job()["job"]
        self.assertEqual((out["kind"], out["request"]), ("request", "can you build a dice roller cli"))
        self.assertEqual(out["workdir"], f"state/workbench/req-{first}-can-you-build-a-dice-roller-cli")
        self.assertEqual(out["repo"], f"req-{first}-can-you-build-a-dice-roller-cli")
        self.assertTrue((self.root / out["workdir"]).is_dir())
        self.assertFalse(out["resumed"])

    def test_an_open_claim_is_resumed_before_anything_new(self):
        request("build me a todo app")
        first = jobs.next_job()["job"]
        request("build me a timer")
        again = jobs.next_job()["job"]
        self.assertEqual(again["job_id"], first["job_id"])
        self.assertTrue(again["resumed"])

    def test_a_daily_job_when_no_request_waits_and_only_one_a_day(self):
        out = jobs.next_job()["job"]
        self.assertEqual((out["kind"], out["repo"]), ("daily", "daily-2026-09-14"))
        jobs.done({"job_id": out["job_id"], "status": "failed", "summary": "ran out of ideas", "tests": "none ran"})
        nothing = jobs.next_job()
        self.assertIsNone(nothing["job"])
        self.assertIn("nothing to build", nothing["reason"])
        # The next UTC day brings a new one.

        os.environ["TEENY_NOW"] = str(T0 + 86400)
        self.assertEqual(jobs.next_job()["job"]["repo"], "daily-2026-09-15")

    def test_a_request_is_built_once(self):
        rid = request("build me a clock")
        job = jobs.next_job()["job"]
        (self.root / job["workdir"] / "clock.py").write_text("print('tick')\n")
        jobs.done({"job_id": job["job_id"], "status": "ok", "summary": "a clock", "tests": ["python3 -m unittest: 2 passed"]})
        nxt = jobs.next_job()["job"]
        self.assertEqual(nxt["kind"], "daily")
        self.assertEqual(request_row(rid)["status"], "picked")  # shipped only once it ships

    def test_a_stale_claim_is_failed_and_its_request_marked(self):
        rid = request("build me a calculator")
        job = jobs.next_job()["job"]

        os.environ["TEENY_NOW"] = str(T0 + jobs.STALE_CLAIM_SECONDS + 1)
        out = jobs.next_job()
        self.assertEqual(out["abandoned"], [job["job_id"]])
        self.assertEqual(out["job"]["kind"], "daily")
        self.assertEqual(request_row(rid)["status"], "failed")
        self.assertEqual(jobs.listing("failed")["jobs"][0]["summary"], "abandoned: the builder never reported back")


class DoneTest(Scratch):
    def claim(self, text="build me a thing"):
        self.rid = request(text)
        return jobs.next_job()["job"]

    def test_ok_needs_something_in_the_folder(self):
        job = self.claim()
        with self.assertRaises(hand.Refused):
            jobs.done({"job_id": job["job_id"], "status": "ok", "summary": "x", "tests": "1 passed"})
        (self.root / job["workdir"] / "a.py").write_text("x = 1\n")
        out = jobs.done({"job_id": job["job_id"], "status": "ok", "summary": "x", "tests": "1 passed", "repo": "tiny-thing"})
        self.assertEqual(out, {"job_id": job["job_id"], "status": "ok", "summary": "x", "tests": "1 passed"})
        row = jobs.listing("ok")["jobs"][0]
        self.assertEqual((row["repo"], row["tests"]), ("tiny-thing", "1 passed"))

    def test_failed_marks_the_request_failed_with_the_reason(self):
        job = self.claim()
        jobs.done({"job_id": job["job_id"], "status": "failed", "summary": "asked for a wallet drainer", "tests": "none"})
        self.assertEqual((request_row(self.rid)["status"], request_row(self.rid)["note"]),
                         ("failed", "asked for a wallet drainer"))

    def test_a_request_somebody_unpicked_is_left_alone(self):
        job = self.claim()
        build_requests.mark(self.rid, "skipped", "changed my mind")
        jobs.done({"job_id": job["job_id"], "status": "failed", "summary": "no", "tests": "none"})
        self.assertEqual(request_row(self.rid)["status"], "skipped")

    def test_only_a_claimed_job_and_only_once(self):
        job = self.claim()
        jobs.done({"job_id": job["job_id"], "status": "failed", "summary": "no", "tests": "none"})
        with self.assertRaises(hand.Refused):
            jobs.done({"job_id": job["job_id"], "status": "failed", "summary": "no", "tests": "none"})
        with self.assertRaises(hand.Refused):
            jobs.done({"job_id": 999, "status": "failed", "summary": "no", "tests": "none"})

    def test_bad_bodies(self):
        job = self.claim()
        good = {"job_id": job["job_id"], "status": "failed", "summary": "s", "tests": "t"}
        for bad in (None, [], {**good, "status": "done"}, {**good, "job_id": "1"}, {**good, "job_id": True},
                    {**good, "summary": ""}, {**good, "tests": []}, {**good, "tests": 3}, {**good, "extra": 1},
                    {**good, "repo": "a/b"}, {**good, "repo": "x.git"}, {**good, "repo": "brain"},
                    {**good, "repo": "Brain"}):
            with self.subTest(body=bad), self.assertRaises(hand.HandError):
                jobs.done(bad)


    # --- the repository's name when the report gives none --------------------------------

    def daily(self, summary="statediff: a stdlib-only Python 3 referee CLI\nmore", **extra):
        job = jobs.next_job()["job"]
        self.assertEqual(job["kind"], "daily")
        (self.root / job["workdir"] / "a.py").write_text("x = 1\n")
        jobs.done({"job_id": job["job_id"], "status": "ok", "summary": summary, "tests": "1 passed", **extra})
        return jobs.listing("ok")["jobs"][0]["repo"]

    def test_a_daily_build_without_a_repo_is_named_after_what_its_summary_leads_with(self):
        self.assertEqual(self.daily(), "statediff")

    def test_a_request_build_without_a_repo_is_named_after_its_summary_too(self):
        self.claim("build me a csv stats tool")
        job = jobs.listing("claimed")["jobs"][0]
        (self.root / job["workdir"] / "a.py").write_text("x = 1\n")
        jobs.done({"job_id": job["id"], "status": "ok", "summary": "csvstat: column stats for a CSV", "tests": "t"})
        self.assertEqual(jobs.listing("ok")["jobs"][0]["repo"], "csvstat")

    def test_the_reported_repo_still_wins(self):
        self.assertEqual(self.daily(repo="refusal-ledger"), "refusal-ledger")

    def test_the_slug_stays_when_the_summary_gives_no_usable_name(self):
        for day, summary in enumerate(("a referee for state diffs", "state diff: two words", "brain: a change to me",
                                       "Brain: a change to me", "x.git: odd", "-dash: odd", ": nothing first",
                                       "a/b: slash", "..: dots"), start=1):
            with self.subTest(summary=summary):
                os.environ["TEENY_NOW"] = str(T0 + day * 86400)  # one daily job a day
                self.assertTrue(self.daily(summary).startswith("daily-2026-09-"), summary)

    def test_the_slug_stays_when_another_job_already_has_that_name(self):
        conn = jobs.db()
        conn.execute("INSERT INTO build_jobs (kind, slug, repo, text, workdir, status, claimed_at)"
                     " VALUES ('daily', 'daily-2026-09-13', 'StateDiff', 'x', 'w', 'shipped', 0)")
        conn.commit()
        conn.close()
        self.assertEqual(self.daily(), "daily-2026-09-14")

    def test_the_slug_stays_when_the_account_already_has_that_repository(self):
        self.gh.route("GET", "/repos/teenydroid/statediff", {"full_name": "teenydroid/statediff",
                                                             "owner": {"login": "teenydroid"}})
        self.assertEqual(self.daily(), "daily-2026-09-14")

    def test_the_slug_stays_when_github_cannot_say(self):
        self.gh.route("GET", "/repos/teenydroid/statediff", {"message": "boom"}, status=500)
        self.assertEqual(self.daily(), "daily-2026-09-14")

    def test_the_report_shape_asks_for_a_short_tool_name(self):
        self.assertIn("short name for the tool", jobs.REPORT_SHAPE)
        self.assertIn("never a date", jobs.REPORT_SHAPE)



JAILED = __import__("teeny_hands.build.verify", fromlist=["verify"]).landlock_abi() > 0
needs_landlock = __import__("unittest").skipUnless(JAILED, "this kernel has no Landlock: the ship step's test run cannot be jailed here")


class ShipTest(Scratch):
    def setUp(self):
        super().setUp()
        if not JAILED:
            # Shipping itself is what these tests are about; on a kernel with no
            # Landlock the jailed run is stood in for (the tests that need the
            # real jail are skipped, below).
            from unittest import mock
            from teeny_hands.build import verify
            patcher = mock.patch.object(verify, "run", return_value={"ok": True, "jailed": True, "ran": 1, "output": "OK"})
            patcher.start()
            self.addCleanup(patcher.stop)

    def finished(self, text="build me a greeter", files=None, repo=None):
        rid = request(text)
        job = jobs.next_job()["job"]
        for rel, body in (files or {"greet.py": "print('hi')\n", "README.md": "# greeter\n", "test_greet.py": PASSING}).items():
            (self.root / job["workdir"] / rel).write_text(body)
        jobs.done({"job_id": job["job_id"], "status": "ok", "summary": "a tiny greeter\nmore detail",
                   "tests": "1 passed", **({"repo": repo} if repo else {})})
        return rid, job

    def test_creates_the_repo_pushes_and_marks_everything_shipped(self):
        self.me()
        rid, job = self.finished(repo="greeter")
        self.gh.route("POST", "/user/repos", {"full_name": "teenydroid/greeter", "html_url": "https://github.com/teenydroid/greeter"},
                      status=201)
        remote = self.remote("teenydroid/greeter")
        out = jobs.ship()
        self.assertEqual(len(out["shipped"]), 1, out)
        shipped = out["shipped"][0]
        self.assertTrue(shipped["created"])
        self.assertEqual(self.gh.requests("POST", "/user/repos")[0].json()["description"], "a tiny greeter")
        self.assertEqual(git("-C", str(remote), "log", "--format=%s", "main").strip(), "a tiny greeter")
        files = git("-C", str(remote), "ls-tree", "--name-only", "main").split()
        self.assertEqual(sorted(files), [".gitignore", "README.md", "greet.py", "test_greet.py"])
        row = jobs.listing("shipped")["jobs"][0]
        self.assertEqual(row["repo_url"], "https://github.com/teenydroid/greeter")
        self.assertEqual(row["commit_url"], f"https://github.com/teenydroid/greeter/commit/{shipped['commit']}")
        self.assertEqual((request_row(rid)["status"], request_row(rid)["note"]),
                         ("shipped", "https://github.com/teenydroid/greeter"))
        # Shipped is shipped: the next run has nothing to do.
        self.assertEqual(jobs.ship()["results"], [])

    def test_an_existing_repo_is_pushed_to_not_created(self):
        self.me()
        self.finished(repo="greeter")
        self.gh.route("GET", "/repos/teenydroid/greeter", {"full_name": "teenydroid/greeter", "owner": {"login": "teenydroid"}})
        self.remote("teenydroid/greeter")
        out = jobs.ship()
        self.assertFalse(out["shipped"][0]["created"])
        self.assertEqual(self.gh.requests("POST", "/user/repos"), [])

    def test_dry_run_ships_nothing_and_marks_nothing(self):
        self.configure(DRY_RUN="1")
        rid, job = self.finished(repo="greeter")
        out = jobs.ship()
        self.assertEqual(out["results"][0]["would_push"], "teenydroid/greeter")
        self.assertEqual(self.gh.seen, [])
        self.assertEqual(jobs.listing("ok")["jobs"][0]["id"], job["job_id"])
        self.assertEqual(request_row(rid)["status"], "picked")
        self.assertFalse((self.root / job["workdir"] / ".git").exists())

    def test_a_secret_is_never_shipped_and_the_job_gives_up_after_retries(self):
        self.me()
        rid, job = self.finished(files={"config.py": "KEY = 'ghp_" + "a" * 36 + "'\n", "test_c.py": PASSING}, repo="leaky")
        self.gh.route("POST", "/user/repos", {"full_name": "teenydroid/leaky"}, status=201)
        self.remote("teenydroid/leaky")
        for attempt in range(1, jobs.MAX_SHIP_ATTEMPTS + 1):
            out = jobs.ship()
            self.assertEqual(out["shipped"], [])
            self.assertEqual(out["results"][0]["attempts"], attempt)
        self.assertTrue(out["results"][0]["gave_up"])
        self.assertEqual(jobs.listing("failed")["jobs"][0]["ship_error"], "the push would publish what looks like a secret")
        self.assertEqual(request_row(rid)["status"], "failed")
        self.assertEqual(jobs.ship()["results"], [])

    # --- the ship step's own test run (docs/roadmap.md, A6) --------------------------------

    def stimuli_now(self):
        from teeny_hands import stimuli
        return stimuli.recent()["stimuli"]

    @needs_landlock
    def test_a_report_of_success_with_failing_tests_is_recorded_as_failed_with_the_output(self):
        self.me()
        rid, job = self.finished(files={"greet.py": "print('hi')\n", "test_greet.py": FAILING}, repo="greeter")
        out = jobs.ship()
        self.assertEqual(out["shipped"], [])
        self.assertEqual(out["results"][0]["error"], "its tests did not pass when the ship step ran them")
        self.assertEqual(self.gh.writes(), [])
        row = jobs.listing("failed")["jobs"][0]
        self.assertEqual((row["id"], row["ship_error"]), (job["job_id"], "its tests did not pass when the ship step ran them"))
        self.assertFalse(row["verified"]["ok"])
        self.assertTrue(row["verified"]["jailed"])
        self.assertIn("AssertionError: 'hi' != 'hello'", row["verified"]["output"])
        self.assertEqual(request_row(rid)["status"], "failed")
        (told,) = self.stimuli_now()
        self.assertEqual((told["surface"], told["kind"]), ("builder", "tests_failed"))
        self.assertIn("you reported it ok", told["text"])
        self.assertIn("'hi' != 'hello'", told["text"])
        self.assertEqual(jobs.ship()["results"], [])  # failed is failed: not tried again

    @needs_landlock
    def test_no_tests_is_not_tested(self):
        self.me()
        self.finished(files={"greet.py": "print('hi')\n"}, repo="greeter")
        out = jobs.ship()
        self.assertEqual(out["results"][0]["verified"]["output"], "no tests were found to run")
        self.assertEqual(self.gh.writes(), [])

    @needs_landlock
    def test_the_tests_run_jailed_away_from_secrets_and_the_network(self):
        self.me()
        (self.root / "secrets" / "probe.txt").write_text("SENTINEL")
        probe = (
            "import socket, unittest\n\nclass T(unittest.TestCase):\n"
            f"    def test_secrets(self):\n        print(open({str(self.root / 'secrets' / 'probe.txt')!r}).read())\n"
            "    def test_net(self):\n        socket.create_connection(('127.0.0.1', 9), timeout=2)\n"
        )
        self.finished(files={"test_probe.py": probe}, repo="probe")
        out = jobs.ship()
        checked = out["results"][0]["verified"]
        self.assertFalse(checked["ok"])
        self.assertIn("PermissionError", checked["output"])
        self.assertNotIn("SENTINEL", checked["output"])
        self.assertTrue("PermissionError" in checked["output"] and checked["output"].count("Error") >= 2)

    @needs_landlock
    def test_without_landlock_nothing_runs_and_nothing_ships(self):
        from unittest import mock
        from teeny_hands.build import verify

        self.me()
        self.finished(repo="greeter")
        with mock.patch.object(verify, "landlock_abi", return_value=0):
            out = jobs.ship()
        self.assertFalse(out["results"][0]["verified"]["jailed"])
        self.assertEqual(self.gh.writes(), [])

    # --- the mind hears where a build shipped --------------------------------------------

    def test_the_mind_hears_the_exact_address_a_build_shipped_to_once(self):
        self.me()
        self.finished(repo="greeter")
        self.gh.route("POST", "/user/repos", {"full_name": "teenydroid/greeter"}, status=201)
        # The first try fails (no remote to push to yet) and is retried next run.
        first = jobs.ship()
        self.assertEqual(first["shipped"], [], first)
        self.assertEqual([s for s in self.stimuli_now() if s["kind"] == "shipped"], [])
        self.remote("teenydroid/greeter")
        out = jobs.ship()
        self.assertEqual(len(out["shipped"]), 1, out)
        self.assertEqual(jobs.ship()["results"], [])
        (told,) = [s for s in self.stimuli_now() if s["kind"] == "shipped"]
        job = jobs.listing("shipped")["jobs"][0]
        self.assertEqual((told["surface"], told["ref"]), ("builder", f"build:{job['id']}"))
        self.assertIn(f"your build greeter (build {job['id']}, a tiny greeter) shipped", told["text"])
        self.assertIn("https://github.com/teenydroid/greeter", told["text"])
        self.assertIn("never a link you guess from its name", told["text"])
        self.assertEqual(told["payload"]["url"], "https://github.com/teenydroid/greeter")
        self.assertEqual(told["payload"]["repo_url"], "https://github.com/teenydroid/greeter")
        self.assertEqual(told["payload"]["commit_url"], job["commit_url"])
        # Told again (a retried side channel) is still told once.
        jobs._tell_shipped(job, {"repo_url": job["repo_url"], "commit_url": job["commit_url"]})
        self.assertEqual(len([s for s in self.stimuli_now() if s["kind"] == "shipped"]), 1)

    def test_a_dry_run_tells_the_mind_nothing_shipped(self):
        self.configure(DRY_RUN="1")
        self.finished(repo="greeter")
        jobs.ship()
        self.assertEqual([s for s in self.stimuli_now() if s["kind"] == "shipped"], [])

    def test_a_builder_that_gives_up_is_not_failed(self):
        rid = request("build me a quantum computer")
        job = jobs.next_job()["job"]
        out = jobs.done({"job_id": job["job_id"], "status": "gave_up", "summary": "needs hardware I don't have",
                         "tests": "none"})
        self.assertEqual(out["status"], "gave_up")
        self.assertEqual(jobs.listing("gave_up")["jobs"][0]["id"], job["job_id"])
        self.assertEqual(jobs.listing("failed")["jobs"], [])
        self.assertEqual((request_row(rid)["status"], request_row(rid)["note"]),
                         ("skipped", "the builder gave up: needs hardware I don't have"))

    def test_the_cli_ships_with_the_contract(self):
        self.configure(DRY_RUN="1")
        self.finished(repo="greeter")
        done = subprocess.run([sys.executable, str(BUILD), "ship"], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(json.loads(done.stdout)["dry_run"])
        listed = subprocess.run([sys.executable, str(BUILD), "jobs", "--status", "ok"], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL)
        self.assertEqual(len(json.loads(listed.stdout)["jobs"]), 1)
        refused = subprocess.run([sys.executable, str(BUILD), "done"], input='{"job_id": 99, "status": "ok", '
                                 '"summary": "s", "tests": "t"}', capture_output=True, text=True)
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(json.loads(refused.stdout)["refused"], "unknown job")


class DeskTest(Scratch):
    """The builder's desk: the job laid out as a file, the report it leaves
    collected through `done` with every rule, a bad report set aside."""

    def desk(self, name):
        return self.root / "state" / "workbench" / "desk" / name

    def test_lays_out_the_next_job_and_where_to_report(self):
        rid = request("build me a coin flipper")
        out = jobs.desk()
        self.assertEqual(out["offer"], "state/workbench/desk/build.json")
        offer = json.loads(self.desk("build.json").read_text())
        self.assertEqual(offer["job"]["request"], "build me a coin flipper")
        # Relative to the workbench, where the builder is rooted.
        self.assertEqual(offer["report_to"], "desk/build-done.json")
        self.assertIn("job_id", offer["report_shape"])
        self.assertIn("Work only inside workdir", offer["rules"])
        # The rules name the one path the builder is shown: report_to.
        self.assertIn("write your report to report_to, once", offer["rules"])
        self.assertNotIn("report_to_path", offer["rules"])
        # Laying it out again before a report is the same claim, resumed.
        jobs.desk()
        again = json.loads(self.desk("build.json").read_text())
        self.assertEqual((again["job"]["job_id"], again["job"]["resumed"]), (offer["job"]["job_id"], True))
        self.assertEqual(request_row(rid)["status"], "picked")

    def test_collects_the_report_then_lays_out_the_next(self):
        request("build me a coin flipper")
        jobs.desk()
        job = json.loads(self.desk("build.json").read_text())["job"]
        (self.workbench() / job["workdir"] / "flip.py").write_text("import random\n")
        self.desk("build-done.json").write_text(json.dumps(
            {"job_id": job["job_id"], "status": "ok", "summary": "a coin flipper", "tests": "2 passed"}))
        out = jobs.desk()
        self.assertEqual(out["settled"], {"job_id": job["job_id"], "status": "ok", "summary": "a coin flipper", "tests": "2 passed"})
        self.assertFalse(self.desk("build-done.json").exists())
        self.assertEqual(jobs.listing("ok")["jobs"][0]["id"], job["job_id"])
        self.assertEqual(json.loads(self.desk("build.json").read_text())["job"]["kind"], "daily")

    def test_the_mind_hears_why_a_build_failed(self):
        # A report that reaches the mind as "build 3: failed" and nothing
        # else can't be learned from; the summary is the reason.
        from teeny_hands import stimuli

        request("build me a coin flipper")
        jobs.desk()
        job = json.loads(self.desk("build.json").read_text())["job"]
        self.desk("build-done.json").write_text(json.dumps(
            {"job_id": job["job_id"], "status": "failed",
             "summary": "cannot ship: the brief lives in a frozen file", "tests": "none ran"}))
        jobs.desk()
        told = [s for s in stimuli.take(max_items=10, max_chars=10_000)["stimuli"] if s["kind"] == "report"]
        self.assertEqual(len(told), 1)
        self.assertIn("cannot ship: the brief lives in a frozen file", told[0]["text"])
        self.assertIn("none ran", told[0]["text"])

    def test_a_refused_report_is_set_aside_with_the_reason(self):
        request("build me a coin flipper")
        jobs.desk()
        job = json.loads(self.desk("build.json").read_text())["job"]
        # ok with nothing in the folder is refused by `done` itself.
        self.desk("build-done.json").write_text(json.dumps(
            {"job_id": job["job_id"], "status": "ok", "summary": "s", "tests": "t"}))
        out = jobs.desk()
        self.assertIn("workdir is empty", out["rejected"])
        self.assertFalse(self.desk("build-done.json").exists())
        self.assertTrue(self.desk("build-done.rejected.json").exists())
        offer = json.loads(self.desk("build.json").read_text())
        self.assertIn("workdir is empty", offer["last_report_rejected"])
        self.assertEqual(offer["job"]["job_id"], job["job_id"])  # still the builder's
        self.desk("build-done.json").write_text("{not json")
        self.assertIsNotNone(jobs.desk()["rejected"])

    def idle_report(self):
        self.desk("build-done.json").write_text(json.dumps(
            {"job_id": None, "status": "ok", "summary": "nothing to build", "tests": "none"}))

    def test_a_nothing_to_do_report_while_no_job_waits_is_discarded_quietly(self):
        from teeny_hands import stimuli

        daily = jobs.next_job()["job"]
        jobs.done({"job_id": daily["job_id"], "status": "failed", "summary": "no idea", "tests": "none ran"})
        jobs.desk()
        self.assertIsNone(json.loads(self.desk("build.json").read_text())["job"])
        self.idle_report()
        out = jobs.desk()
        self.assertEqual((out["settled"], out["rejected"]), (None, None))
        self.assertFalse(self.desk("build-done.json").exists())
        self.assertFalse(self.desk("build-done.rejected.json").exists())
        self.assertNotIn("last_report_rejected", json.loads(self.desk("build.json").read_text()))
        self.assertEqual(stimuli.take(max_items=10, max_chars=10_000)["stimuli"], [])

    def test_a_report_without_a_job_id_while_a_job_is_claimed_is_still_rejected(self):
        request("build me a coin flipper")
        jobs.desk()
        self.idle_report()
        out = jobs.desk()
        self.assertIn('"job_id" must be the number', out["rejected"])
        self.assertIn('"job_id" must be the number',
                      json.loads(self.desk("build.json").read_text())["last_report_rejected"])

    # --- the paths the builder is shown -------------------------------------------------
    # The builder's shell and file tools are rooted at state/workbench/
    # (brain/agents/builder.toml, scheduled_workspace). A checkout-relative
    # "state/workbench/brain" there resolves to state/workbench/state/workbench/brain,
    # which is where self job 7 wrote its files on the live box.

    def workbench(self):
        return self.root / "state" / "workbench"

    def claim_self_job(self, slug="self-2-build-dripsplan", claimed_at=None, status="claimed"):
        conn = jobs.db()
        try:
            with hand.transaction(conn):
                cur = conn.execute(
                    "INSERT INTO build_jobs (kind, slug, repo, text, workdir, claimed_at, branch, status)"
                    " VALUES ('self', ?, 'brain', 'build dripsplan', 'state/workbench/brain', ?, ?, ?)",
                    (slug, hand.now() if claimed_at is None else claimed_at, "teeny/" + slug, status))
                return cur.lastrowid
        finally:
            conn.close()

    def assert_builder_paths(self, offer, workdir):
        job = offer["job"]
        self.assertEqual(job["workdir"], workdir)
        self.assertTrue((self.workbench() / workdir).is_dir())
        self.assertNotIn("workdir_path", job)
        self.assertEqual(offer["report_to"], "desk/build-done.json")
        self.assertNotIn("report_to_path", offer)
        # Not as a key, and not as text anywhere the builder reads (the rules too).
        dumped = json.dumps(offer)
        self.assertNotIn("report_to_path", dumped)
        self.assertNotIn("workdir_path", dumped)
        self.assertIn("relative to state/workbench/", offer["paths"])
        shown = {k: v for k, v in job.items() if k not in ("request", "instructions")}
        shown.update({k: v for k, v in offer.items() if k != "job"})
        for key, value in shown.items():
            if isinstance(value, str):
                self.assertFalse(value.startswith("state/workbench/"), f"{key}: {value}")
                self.assertNotIn(str(self.root), value, key)

    def test_a_daily_job_is_shown_its_workdir_relative_to_the_workbench(self):
        jobs.desk()
        offer = json.loads(self.desk("build.json").read_text())
        self.assertEqual(offer["job"]["kind"], "daily")
        self.assert_builder_paths(offer, offer["job"]["workdir"])
        self.assertRegex(offer["job"]["workdir"], r"^daily-\d{4}-\d{2}-\d{2}$")
        # Internally the job keeps its checkout-relative folder.
        row = jobs.listing()["jobs"][0]
        self.assertEqual(row["workdir"], "state/workbench/" + offer["job"]["workdir"])

    def test_a_self_job_is_shown_its_clone_as_brain(self):
        job_id = self.claim_self_job()
        jobs.desk()
        offer = json.loads(self.desk("build.json").read_text())
        self.assertEqual((offer["job"]["job_id"], offer["job"]["resumed"]), (job_id, True))
        self.assert_builder_paths(offer, "brain")
        self.assertNotIn("state/workbench/brain", offer["job"].get("instructions") or "")

    def test_a_resumed_job_is_shown_the_same_workdir(self):
        jobs.desk()
        first = json.loads(self.desk("build.json").read_text())
        jobs.desk()
        again = json.loads(self.desk("build.json").read_text())
        self.assertTrue(again["job"]["resumed"])
        self.assert_builder_paths(again, first["job"]["workdir"])

    def test_files_left_in_the_nested_mirror_are_named_on_the_resumed_job(self):
        self.claim_self_job()
        stray = self.workbench() / "state" / "workbench" / "brain" / "hands" / "lib" / "teeny_hands" / "dripsplan"
        stray.mkdir(parents=True)
        (stray / "__init__.py").write_text("x = 1\n")
        jobs.desk()
        offer = json.loads(self.desk("build.json").read_text())
        note = offer["job"]["note"]
        self.assertIn("state/workbench/brain/hands/lib/teeny_hands/dripsplan/__init__.py", note)
        self.assertIn("your workdir is brain", note)
        # Nothing is moved for it.
        self.assertTrue((stray / "__init__.py").exists())
        self.assertFalse((self.workbench() / "brain" / "hands").exists())

    def test_a_stray_file_from_an_earlier_job_is_not_named_on_a_later_one(self):
        # Self jobs share the workdir brain. Job 7 left files in the mirror
        # before job 8 was claimed; job 8 must not be told to rewrite them.
        mirror = self.workbench() / "state" / "workbench" / "brain" / "hands" / "lib" / "teeny_hands" / "dripsplan"
        mirror.mkdir(parents=True)
        old = mirror / "__init__.py"
        old.write_text("x = 1\n")
        os.utime(old, (T0 - 3000, T0 - 3000))
        self.claim_self_job("self-7-build-dripsplan", claimed_at=T0 - 3600, status="shipped")
        self.claim_self_job("self-8-build-dripsplan", claimed_at=T0)
        jobs.desk()
        self.assertNotIn("note", json.loads(self.desk("build.json").read_text())["job"])
        # A file the current job wrote into the mirror is still named, and
        # only it: job 7's file is not.
        fresh = mirror / "cli.py"
        fresh.write_text("y = 2\n")
        os.utime(fresh, (T0 + 10, T0 + 10))
        jobs.desk()
        note = json.loads(self.desk("build.json").read_text())["job"]["note"]
        self.assertIn("state/workbench/brain/hands/lib/teeny_hands/dripsplan/cli.py", note)
        self.assertNotIn("__init__.py", note)

    def test_no_note_without_a_nested_mirror(self):
        self.claim_self_job()
        jobs.desk()
        self.assertNotIn("note", json.loads(self.desk("build.json").read_text())["job"])

    def test_a_report_on_an_empty_workdir_with_files_in_the_mirror_says_where_they_went(self):
        jobs.desk()
        job = json.loads(self.desk("build.json").read_text())["job"]
        stray = self.workbench() / "state" / "workbench" / job["workdir"]
        stray.mkdir(parents=True)
        (stray / "tool.py").write_text("x = 1\n")
        self.desk("build-done.json").write_text(json.dumps(
            {"job_id": job["job_id"], "status": "ok", "summary": "s", "tests": "t"}))
        out = jobs.desk()
        self.assertIn("workdir is empty", out["rejected"])
        self.assertIn(f"state/workbench/{job['workdir']}/tool.py", out["rejected"])
        self.assertIn(f"your workdir is {job['workdir']}", out["rejected"])
        offer = json.loads(self.desk("build.json").read_text())
        self.assertIn(f"state/workbench/{job['workdir']}/tool.py", offer["job"]["note"])

    def test_an_unknown_report_field_names_the_allowed_ones(self):
        jobs.desk()
        job = json.loads(self.desk("build.json").read_text())["job"]
        self.desk("build-done.json").write_text(json.dumps(
            {"job_id": job["job_id"], "status": "failed", "summary": "s", "tests": "t", "details": "more"}))
        reason = jobs.desk()["rejected"]
        self.assertIn("details", reason)
        self.assertIn("job_id, status, summary, tests, repo", reason)

    def test_bin_build_ship_runs_the_desk_then_ships(self):
        self.configure(DRY_RUN="1")
        script = Path(__file__).resolve().parents[3] / "bin" / "build-ship"
        env = {**os.environ, "TEENY_ROOT": str(self.root), "TEENY_HANDS": str(BUILD.parent)}
        done = subprocess.run([str(script)], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(json.loads(done.stdout)["dry_run"])
        self.assertEqual(json.loads(self.desk("build.json").read_text())["job"]["kind"], "daily")
