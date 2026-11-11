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
        self.assertEqual(out, {"job_id": job["job_id"], "status": "ok"})
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


class ShipTest(Scratch):
    def finished(self, text="build me a greeter", files=None, repo=None):
        rid = request(text)
        job = jobs.next_job()["job"]
        for rel, body in (files or {"greet.py": "print('hi')\n", "README.md": "# greeter\n"}).items():
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
        self.assertEqual(sorted(files), [".gitignore", "README.md", "greet.py"])
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
        rid, job = self.finished(files={"config.py": "KEY = 'ghp_" + "a" * 36 + "'\n"}, repo="leaky")
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
        self.assertEqual(offer["report_to"], "state/workbench/desk/build-done.json")
        self.assertIn("job_id", offer["report_shape"])
        self.assertIn("Work only inside workdir", offer["rules"])
        # Laying it out again before a report is the same claim, resumed.
        jobs.desk()
        again = json.loads(self.desk("build.json").read_text())
        self.assertEqual((again["job"]["job_id"], again["job"]["resumed"]), (offer["job"]["job_id"], True))
        self.assertEqual(request_row(rid)["status"], "picked")

    def test_collects_the_report_then_lays_out_the_next(self):
        request("build me a coin flipper")
        jobs.desk()
        job = json.loads(self.desk("build.json").read_text())["job"]
        (self.root / job["workdir"] / "flip.py").write_text("import random\n")
        self.desk("build-done.json").write_text(json.dumps(
            {"job_id": job["job_id"], "status": "ok", "summary": "a coin flipper", "tests": "2 passed"}))
        out = jobs.desk()
        self.assertEqual(out["settled"], {"job_id": job["job_id"], "status": "ok"})
        self.assertFalse(self.desk("build-done.json").exists())
        self.assertEqual(jobs.listing("ok")["jobs"][0]["id"], job["job_id"])
        self.assertEqual(json.loads(self.desk("build.json").read_text())["job"]["kind"], "daily")

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

    def test_bin_build_ship_runs_the_desk_then_ships(self):
        self.configure(DRY_RUN="1")
        script = Path(__file__).resolve().parents[3] / "bin" / "build-ship"
        env = {**os.environ, "TEENY_ROOT": str(self.root), "TEENY_HANDS": str(BUILD.parent)}
        done = subprocess.run([str(script)], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(json.loads(done.stdout)["dry_run"])
        self.assertEqual(json.loads(self.desk("build.json").read_text())["job"]["kind"], "daily")
