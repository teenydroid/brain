"""bin/lib/cron_store.py on its core backend: every reader of the core's cron jobs and runs
(bin/crons, health, pulse, catch_up, evolve, desk) answers from the core's `cron_list` and
`cron_runs` RPCs (never the openclaw CLI, which needs the Gateway's token), here a fake bin/oh
that serves two JSON files in the shapes core/src/cron/map.ts gives."""

import datetime as dt
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "lib"))
sys.path.insert(0, str(REPO / "bin" / "lib"))

from teeny_hands import hand  # noqa: E402
import catch_up  # noqa: E402
import cron_store  # noqa: E402
import health  # noqa: E402

UTC = dt.timezone.utc
FAKE = """#!/usr/bin/env python3
import json, os, sys
method, params = sys.argv[1], json.loads(sys.argv[2] if len(sys.argv) > 2 else "{}")
if os.environ.get("FAKE_DOWN"):
    sys.stderr.write("core unreachable"); sys.exit(1)
data = json.load(open(os.environ["FAKE_STORE"]))
if method == "cron_list":
    print(json.dumps(data["jobs"]))
elif method == "cron_runs":
    print(json.dumps(data["runs"]))
else:
    sys.exit(2)
"""


def ts(text: str) -> float:
    return dt.datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def ms(text: str) -> int:
    return int(ts(text) * 1000)


def shell_job(job_id, name, command, expr="10 15 * * *", tz="Asia/Kolkata", next_run=None, enabled=True, running=None,
              last=None, created="2026-11-01T00:00"):
    """A job as the core's cron_list answers it (core/src/cron/map.ts view)."""
    job = {"id": job_id, "name": name, "job_type": "shell", "enabled": enabled, "command": command,
           "schedule": {"kind": "cron", "expr": expr, "tz": tz}, "delivery": {"mode": "none"},
           "created_ms": ms(created)}
    for key, value in (("next_run_ms", next_run), ("running_ms", running), ("last_run_ms", last)):
        if value:
            job[key] = ms(value)
    return job


def entry(job_id, started, status="ok", seconds=5, summary="", error=None):
    """A run as the core's cron_runs answers it (core/src/cron/map.ts runView)."""
    return {"job_id": job_id, "status": status, "started_ms": ms(started), "finished_ms": ms(started) + seconds * 1000,
            "seq": ms(started), "duration_ms": seconds * 1000, "output": "\n".join(x for x in (summary, error) if x)}


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "state").mkdir()
        self._env = dict(os.environ)
        for key in ("TEENY_STORAGE", "OPENHUMAN_WORKSPACE", "TEENY_STATE", "CALENDAR_TZ", "FAKE_DOWN"):
            os.environ.pop(key, None)
        fake = self.root / "oh"
        fake.write_text(FAKE)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.store = self.root / "store.json"
        os.environ.update(TEENY_CRON_BACKEND="core", TEENY_OH=str(fake), FAKE_STORE=str(self.store),
                          TEENY_ROOT=str(self.root), TEENY_QUIET_HOURS="off")
        hand._ENV_CACHE = None
        self.jobs: list[dict] = []
        self.runs: list[dict] = []

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def write(self):
        self.store.write_text(json.dumps({"jobs": self.jobs, "runs": self.runs}))


class AdapterTest(StoreCase):
    def test_jobs_come_back_in_the_rows_the_readers_use(self):
        self.jobs = [shell_job("j1", "teeny.tick", "bin/tick --detach", "*/5 * * * *", "UTC", next_run="2026-11-27T10:05",
                               running="2026-11-27T10:00", last="2026-11-27T09:55"),
                     {"id": "j2", "name": "mind.nap", "job_type": "agent", "enabled": False, "agent_id": "mind",
                      "prompt": "rest", "created_ms": 5, "schedule": {"kind": "at", "at": "2026-11-27T12:00:00Z"}}]
        self.write()
        j1, j2 = cron_store.jobs()
        self.assertEqual((j1["name"], j1["job_type"], j1["command"], j1["expression"], j1["enabled"]),
                         ("teeny.tick", "shell", "bin/tick --detach", "*/5 * * * *", True))
        self.assertEqual((j1["schedule"], j1["next_run_ms"], j1["running_ms"], j1["last_run_ms"]),
                         ({"kind": "cron", "expr": "*/5 * * * *", "tz": "UTC"}, ms("2026-11-27T10:05"),
                          ms("2026-11-27T10:00"), ms("2026-11-27T09:55")))
        self.assertEqual((j2["job_type"], j2["agent_id"], j2["prompt"], j2["enabled"], j2["schedule"]),
                         ("agent", "mind", "rest", False, {"kind": "at", "at": "2026-11-27T12:00:00Z"}))
        self.assertIsNone(j2["command"])

    def test_runs_come_back_with_start_end_status_and_output(self):
        self.jobs = [shell_job("j1", "teeny.tick", "true")]
        self.runs = [entry("j1", "2026-11-27T10:00", "error", 7, "out", "exit 3")]
        self.write()
        (run,) = cron_store.runs()
        self.assertEqual((run["job_id"], run["status"], run["started_ms"], run["finished_ms"], run["duration_ms"]),
                         ("j1", "error", ms("2026-11-27T10:00"), ms("2026-11-27T10:00") + 7000, 7000))
        self.assertEqual(run["output"], "out\nexit 3")

    def test_it_never_runs_the_openclaw_cli(self):
        # the readers have no Gateway token: an `openclaw` on the PATH that records being run must stay unrun
        trap = self.root / "bin"
        trap.mkdir()
        (trap / "openclaw").write_text(f"#!/bin/sh\ntouch {self.root}/openclaw-ran\nexit 1\n")
        (trap / "openclaw").chmod(0o755)
        os.environ["PATH"] = f"{trap}:{os.environ['PATH']}"
        self.jobs = [shell_job("j1", "teeny.tick", "true")]
        self.runs = [entry("j1", "2026-11-27T10:00")]
        self.write()
        self.assertEqual([j["name"] for j in cron_store.jobs()], ["teeny.tick"])
        self.assertEqual(len(cron_store.runs()), 1)
        self.assertFalse((self.root / "openclaw-ran").exists())

    def test_a_gateway_that_is_down_is_unavailable_and_readers_say_nothing(self):
        os.environ["FAKE_DOWN"] = "1"
        with self.assertRaises(cron_store.Unavailable):
            cron_store.jobs()
        self.assertEqual(health.cron_errors(0), {})
        self.assertEqual(health.stale_crons(ts("2026-11-27T12:00")), [])
        self.assertEqual(catch_up.live_job_ids(), [])

    def test_an_answer_that_is_not_a_list_is_unavailable_not_an_empty_store(self):
        for bad in (None, {"jobs": []}, "oops"):
            self.jobs, self.runs = bad, bad
            self.write()
            with self.assertRaises(cron_store.Unavailable):
                cron_store.jobs()
            with self.assertRaises(cron_store.Unavailable):
                cron_store.runs()
        self.jobs, self.runs = [], []
        self.write()
        self.assertEqual((cron_store.jobs(), cron_store.runs()), ([], []))  # a real empty list stays valid

    def test_a_run_that_started_just_after_now_was_sampled_is_still_live(self):
        job = shell_job("j1", "teeny.tick", "bin/tick", "*/5 * * * *", "UTC", running="2026-11-27T10:00:05")
        now = ms("2026-11-27T10:00")
        self.assertTrue(cron_store.claim(job, now, now)[1])
        job["running_ms"] = now + 10 * 60 * 1000  # a marker far in the future is not a live claim
        self.assertFalse(cron_store.claim(job, now, now)[1])

    def test_a_deleted_job_is_kept_for_history_lookups_and_left_out_of_the_live_readers(self):
        import sqlite3
        db = self.root / "storage.db"
        conn = sqlite3.connect(db)
        conn.execute("create table docs (coll text, id text, created_ms integer, deleted integer, body text)")
        for job_id, deleted in (("live", 0), ("gone", 1)):
            conn.execute("insert into docs values ('cron.jobs', ?, 1, ?, ?)",
                         (job_id, deleted, json.dumps({"name": job_id, "command": f"bin/{job_id}"})))
        conn.commit()
        conn.close()
        os.environ.update(TEENY_CRON_BACKEND="sqlite", TEENY_STORAGE=str(db))
        self.assertEqual([j["id"] for j in cron_store.jobs()], ["live"])
        self.assertEqual(sorted(j["id"] for j in cron_store.jobs(include_deleted=True)), ["gone", "live"])

    def test_bin_crons_prints_the_table_and_a_jobs_runs(self):
        self.jobs = [shell_job("j1", "teeny.heartbeat", "bin/heartbeat", "*/5 * * * *", "UTC", next_run="2026-11-27T10:05"),
                     shell_job("j2", "teeny.off", "true", enabled=False)]
        self.runs = [entry("j1", "2026-11-27T10:00", "error", 3, "", "boom")]
        self.write()
        env = {**os.environ, "TEENY_ROOT": str(REPO)}
        table = subprocess.run([str(REPO / "bin" / "crons")], env=env, capture_output=True, text=True).stdout
        self.assertRegex(table, r"teeny\.heartbeat\s+shell\s+\*/5 \* \* \* \*\s+UTC\s+yes\s+2026-11-27 10:05:00\s+error")
        self.assertRegex(table, r"teeny\.off\s+shell\s+.*\s+no\b")
        runs = subprocess.run([str(REPO / "bin" / "crons"), "runs", "heartbeat"], env=env, capture_output=True, text=True).stdout
        self.assertIn("error", runs)
        self.assertIn("boom", runs)
        bad = subprocess.run([str(REPO / "bin" / "crons"), "runs", "x'; drop table docs; --"], env=env, capture_output=True)
        self.assertNotEqual(bad.returncode, 0)


class ReadersTest(StoreCase):
    def test_health_counts_failed_runs_by_job_and_window(self):
        self.jobs = [shell_job("j1", "teeny.operator", "true"), shell_job("j2", "teeny.tick", "true")]
        self.runs = [entry("j1", "2026-11-27T09:00", "error"), entry("j1", "2026-11-27T09:30", "error"),
                     entry("j2", "2026-11-27T09:30", "ok"), entry("j2", "2026-11-27T05:00", "error")]
        self.write()
        self.assertEqual(health.cron_errors(ts("2026-11-27T08:00")), {"teeny.operator": 2})
        self.assertEqual(health.cron_errors(ts("2026-11-27T04:00"), ts("2026-11-27T06:00")), {"teeny.tick": 1})

    def test_a_job_late_with_no_run_holding_it_is_stale_and_a_running_one_is_not(self):
        os.environ["TEENY_NOW"] = str(ts("2026-11-27T12:00"))
        self.jobs = [shell_job("j1", "teeny.late", "true", next_run="2026-11-27T09:00"),
                     shell_job("j2", "teeny.busy", "true", next_run="2026-11-27T09:00", running="2026-11-27T11:55"),
                     shell_job("j3", "teeny.dead", "true", next_run="2026-11-27T09:00", running="2026-11-27T01:00"),
                     shell_job("j4", "teeny.off", "true", next_run="2026-11-27T09:00", enabled=False),
                     shell_job("j5", "teeny.fine", "true", next_run="2026-11-27T12:05")]
        self.write()
        stale = health.stale_crons(ts("2026-11-27T12:00"))
        self.assertEqual([s["name"] for s in stale], ["teeny.dead", "teeny.late"])
        self.assertEqual(stale[1]["next_run"], "2026-11-27T09:00:00Z")
        self.assertEqual(stale[1]["schedule"]["expr"], "10 15 * * *")

    def test_pulse_lists_jobs_with_last_run_and_failures(self):
        import pulse
        self.jobs = [shell_job("j1", "teeny.dream", "bin/dream"), shell_job("j2", "teeny.mind", "true")]
        self.runs = [entry("j1", "2026-11-27T09:00", "error"), entry("j1", "2026-11-27T10:00", "ok")]
        self.write()
        rows = pulse.jobs(ts("2026-11-27T00:00"))
        self.assertEqual([r["job"] for r in rows], ["dream"])
        self.assertEqual((rows[0]["last"], rows[0]["runs"], rows[0]["failed"]), ("ok", 2, 1))
        self.assertEqual(len(rows[0]["last_failures"]), 1)

    def test_evolve_reads_failure_evidence(self):
        import evolve
        self.jobs = [shell_job("j1", "teeny.tick", "bin/tick --detach")]
        self.runs = [entry("j1", "2026-11-27T09:00", "error", 2, "", "HTTP 402 payment"), entry("j1", "2026-11-27T10:00", "ok")]
        self.write()
        self.assertEqual(evolve._cron_failure_counts(ts("2026-11-27T00:00")), {"teeny.tick": 1})
        (failure,) = evolve._cron_failure_runs(ts("2026-11-27T00:00"))
        self.assertEqual((failure["job"], failure["output"]), ("bin/tick --detach", "HTTP 402 payment"))
        self.assertEqual(len(evolve._cron_runs(("bin/tick --detach",), ts("2026-11-27T00:00"), ts("2026-11-28T00:00"))), 2)

    def test_the_desk_reads_what_the_last_ended_run_said(self):
        from teeny_hands import desk
        self.jobs = [shell_job("j1", "teeny.operator", "true")]
        self.runs = [entry("j1", "2026-11-27T09:00", summary="first"), entry("j1", "2026-11-27T10:00", summary="second")]
        self.write()
        self.assertEqual(desk.last_run("teeny.operator", ts("2026-11-27T00:00")), "second")
        self.assertIsNone(desk.last_run("teeny.operator", ts("2026-11-27T11:00")))
        self.assertIsNone(desk.last_run("teeny.nobody", 0))


class Core:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, method, params):
        self.calls.append((method, params))
        return {"job_id": params["job_id"], "status": "queued"}


SELF_IMPROVE = {"name": "teeny.self-improve", "kind": "shell", "expr": "10 15 * * *", "tz": "Asia/Kolkata",
                "command": "bin/act self-improve", "enabled": True, "catch_up_secs": 6 * 3600}
POST = {"name": "teeny.moltbook-daily", "kind": "shell", "expr": "0 12 * * *", "tz": "Asia/Kolkata",
        "command": "bin/act moltbook-daily", "enabled": True, "catch_up_secs": 0}


class CatchUpOnOpenClawTest(StoreCase):
    """The scheduler skips missed slots (cron.skipMissedJobs), so what comes back late is only what
    catch_up.py asks cron_run for."""

    def run_catch_up(self, at, desired, **kw):
        os.environ["TEENY_NOW"] = str(ts(at))
        self.write()
        core = Core()
        return catch_up.run(desired, core, **kw), core

    def test_a_missed_slot_of_a_catch_up_job_is_run_once_late(self):
        # 15:10 IST is 09:40 UTC; the next run has moved on to tomorrow.
        self.jobs = [shell_job("j1", "teeny.self-improve", "bin/act self-improve", next_run="2026-11-15T09:40")]
        out, core = self.run_catch_up("2026-11-14T11:00", [SELF_IMPROVE])
        self.assertEqual([c["name"] for c in out["caught_up"]], ["teeny.self-improve"])
        self.assertEqual(core.calls, [("cron_run", {"job_id": "j1"})])

    def test_a_late_replay_of_a_posting_job_is_skipped(self):
        # teeny.moltbook-daily posts in public: cron_policy gives it catch_up_secs 0, so a slot the
        # scheduler dropped is never replayed, by the scheduler or by this.
        self.jobs = [shell_job("j2", "teeny.moltbook-daily", "bin/act moltbook-daily", "0 12 * * *", next_run="2026-11-15T06:30")]
        out, core = self.run_catch_up("2026-11-14T08:00", [POST])
        self.assertEqual((out["caught_up"], core.calls), ([], []))

    def test_a_job_with_a_run_in_flight_is_left_alone(self):
        self.jobs = [shell_job("j1", "teeny.self-improve", "bin/act self-improve", next_run="2026-11-14T09:40",
                               running="2026-11-14T10:55")]
        out, core = self.run_catch_up("2026-11-14T11:00", [SELF_IMPROVE])
        self.assertEqual((out["caught_up"], core.calls), ([], []))

    def test_a_slot_that_already_ran_is_not_run_again(self):
        self.jobs = [shell_job("j1", "teeny.self-improve", "bin/act self-improve", next_run="2026-11-15T09:40")]
        self.runs = [entry("j1", "2026-11-14T09:41")]
        out, core = self.run_catch_up("2026-11-14T11:00", [SELF_IMPROVE])
        self.assertEqual(out["caught_up"], [])

    def test_a_re_arm_that_sees_a_run_start_in_between_is_raced(self):
        self.jobs = [shell_job("j1", "teeny.self-improve", "bin/act self-improve", next_run="2026-11-14T08:00")]
        self.write()
        os.environ["TEENY_NOW"] = str(ts("2026-11-14T11:00"))
        stale = [{"id": "j1", "name": "teeny.self-improve", "next_ms": ms("2026-11-14T08:00"),
                  "schedule": {"kind": "cron", "expr": "10 15 * * *", "tz": "Asia/Kolkata"}}]

        def call(method, params):  # the Gateway starts the run while the schedule is patched
            self.jobs[0]["running_ms"] = ms("2026-11-14T11:00")
            self.write()
        out = catch_up.rearm(stale, call, state=self.root / "state")
        self.assertEqual((out["raced"], out["rearmed"]), (["teeny.self-improve"], []))
        quiet = catch_up.rearm(stale[:1], lambda m, p: None, state=self.root / "state")
        self.assertEqual(quiet["running"], ["teeny.self-improve"])  # now it holds the slot


if __name__ == "__main__":
    unittest.main()
