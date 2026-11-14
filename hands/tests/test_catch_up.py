"""bin/lib/catch_up.py: a daily cron job whose slot passed without a run is run
once, late, instead of being dropped until tomorrow.

On 2026-11-13 and 11-14 a deploy restarted the spine while
`teeny.self-improve` was running; the boot's bin/seed-cron moved the job to
its next slot and the day's self-improvement was lost, with no alarm. On
11-14 `teeny.moltbook-follow` was cut off the same way at 11:31 UTC and
bin/health re-armed it to the next day. Against a scratch storage.db, a
recording core and a pinned clock."""

import datetime as dt
import json
import os
import sqlite3
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

UTC = dt.timezone.utc


def ts(text: str) -> float:
    return dt.datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def ms(text: str) -> int:
    return int(ts(text) * 1000)


SELF_IMPROVE = {"name": "teeny.self-improve", "kind": "shell", "expr": "10 15 * * *", "tz": "Asia/Kolkata",
                "command": "bin/act self-improve", "enabled": True, "catch_up_secs": 6 * 3600}
FOLLOW = {"name": "teeny.moltbook-follow", "kind": "shell", "expr": "0 17 * * *", "tz": "Asia/Kolkata",
          "command": "bin/act moltbook-follow", "enabled": True, "catch_up_secs": 6 * 3600}
TAKE = {"name": "teeny.take", "kind": "shell", "expr": "0 15 * * *", "tz": "Asia/Kolkata",
        "command": "bin/act take", "enabled": True, "catch_up_secs": 0}
DREAM = {"name": "teeny.dream", "kind": "shell", "expr": "0 2 * * *", "tz": "Asia/Kolkata",
         "command": "bin/dream --detach", "enabled": True, "catch_up_secs": 6 * 3600}


class Core:
    """Records the calls; `refuse` names methods that fail."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.refuse: set[str] = set()

    def __call__(self, method: str, params: dict):
        self.calls.append((method, params))
        if method in self.refuse:
            raise hand.HandError(f"{method}: cron job '{params['job_id']}' was not queued: Busy")
        return {"job_id": params["job_id"], "status": "queued"}

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]


class CatchUpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "state").mkdir()
        self._env = dict(os.environ)
        for key in ("TEENY_STORAGE", "OPENHUMAN_WORKSPACE", "TEENY_STATE", "CALENDAR_TZ"):
            os.environ.pop(key, None)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_QUIET_HOURS"] = "off"
        hand._ENV_CACHE = None
        self.core = Core()
        self.jobs: list[tuple] = []
        self.runs: list[tuple] = []
        self.leases: list[tuple] = []

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    # --- the core's storage.db --------------------------------------------------------------

    def job(self, job_id, spec, next_run, enabled=True, created="2026-11-01T00:00"):
        self.jobs.append((job_id, spec, ms(next_run), enabled, ms(created)))

    def run_of(self, job_id, started, status="ok"):
        self.runs.append((job_id, ms(started), status))

    def lease(self, job_id, occurrence, renewed, released=False):
        self.leases.append((job_id, ms(occurrence), ms(renewed), released))

    def write(self):
        path = self.root / "brain" / "storage.db"
        path.parent.mkdir(exist_ok=True)
        path.unlink(missing_ok=True)
        db = sqlite3.connect(path)
        db.execute("create table docs (scope text default '', coll text, id text, created_ms integer default 0,"
                   " updated_ms integer default 0, deleted integer default 0, body text)")
        for job_id, spec, next_ms, enabled, created_ms in self.jobs:
            body = {"name": spec["name"], "enabled": enabled, "next_run_ms": next_ms, "expression": spec["expr"],
                    "schedule": {"kind": "cron", "expr": spec["expr"], "tz": spec["tz"]}}
            db.execute("insert into docs (coll, id, created_ms, body) values ('cron.jobs', ?, ?, ?)",
                       (job_id, created_ms, json.dumps(body)))
        for n, (job_id, started_ms, status) in enumerate(self.runs):
            body = {"job_id": job_id, "started_ms": started_ms, "finished_ms": started_ms + 5000,
                    "status": status, "seq": n + 1}
            db.execute("insert into docs (coll, id, body) values ('cron.runs', ?, ?)", (f"{n:08}", json.dumps(body)))
        for job_id, occurrence_ms, renewed_ms, released in self.leases:
            body = {"holder": "token", "epoch": 1, "expires_ms": 0 if released else renewed_ms + 7_200_000}
            db.execute("insert into docs (coll, id, updated_ms, body) values ('tsd.leases', ?, ?, ?)",
                       (f"cron/{job_id}%2F{occurrence_ms}", renewed_ms, json.dumps(body)))
        db.commit()
        db.close()

    def catch_up(self, at: str, desired, **kw) -> dict:
        os.environ["TEENY_NOW"] = str(ts(at))
        self.write()
        return catch_up.run(desired, self.core, **kw)

    # --- the slot arithmetic -------------------------------------------------------------------

    def test_the_last_slot_is_found_in_the_jobs_own_time_zone(self):
        now = ts("2026-11-14T09:48:38")
        self.assertEqual(catch_up.last_slot("10 15 * * *", "Asia/Kolkata", now, now - 6 * 3600),
                         ts("2026-11-14T09:40"))
        self.assertEqual(catch_up.last_slot("15 0 * * *", "UTC", now, now - 9 * 3600), None)
        self.assertEqual(catch_up.last_slot("15 0 * * *", "UTC", now, now - 10 * 3600), ts("2026-11-14T00:15"))
        # Weekly: Monday 11:00 India time is 05:30 UTC on 2026-11-16.
        monday = ts("2026-11-16T08:00")
        self.assertEqual(catch_up.last_slot("0 11 * * 1", "Asia/Kolkata", monday, monday - 6 * 3600),
                         ts("2026-11-16T05:30"))
        self.assertIsNone(catch_up.last_slot("0 11 * * 1", "Asia/Kolkata", monday + 86400, monday))

    # --- the two misses of 2026-11-14 ----------------------------------------------------------

    def test_a_slot_seed_cron_already_moved_on_is_run_now_and_only_once(self):
        # self-improve, 11-14: claimed at 09:40, killed by the 09:48:33 restart
        # mid-run; the boot's re-arm moved next_run to tomorrow and fenced the claim.
        self.job("j1", SELF_IMPROVE, "2026-11-15T09:40")
        self.lease("j1", "2026-11-14T09:40", "2026-11-14T09:48:38", released=True)
        out = self.catch_up("2026-11-14T09:48:40", [SELF_IMPROVE])
        self.assertEqual(out["caught_up"], [{"name": "teeny.self-improve", "slot": "2026-11-14T09:40:00Z"}])
        # Its next run is already tomorrow: no re-arm, just the run.
        self.assertEqual(self.core.calls, [("cron_run", {"job_id": "j1"})])
        # Every later boot and check leaves that slot alone.
        for at in ("2026-11-14T09:52:00", "2026-11-14T10:00:00", "2026-11-14T15:00:00"):
            self.assertEqual(self.catch_up(at, [SELF_IMPROVE])["caught_up"], [])
        self.assertEqual(self.core.methods(), ["cron_run"])

    def test_a_slot_held_by_a_dead_claim_is_rearmed_first_then_run(self):
        # moltbook-follow, 11-14: claimed at 11:30, the restart at 11:31:43 cut
        # it off; its next_run still points at the slot the dead claim holds.
        self.job("j2", FOLLOW, "2026-11-14T11:30")
        self.lease("j2", "2026-11-14T11:30", "2026-11-14T11:31:40")
        out = self.catch_up("2026-11-14T11:45:07", [FOLLOW])
        self.assertEqual([c["name"] for c in out["caught_up"]], ["teeny.moltbook-follow"])
        self.assertEqual(self.core.calls, [
            ("cron_update", {"job_id": "j2", "patch": {"schedule": {"kind": "cron", "expr": "0 17 * * *",
                                                                    "tz": "Asia/Kolkata"}}}),
            ("cron_run", {"job_id": "j2"})])

    def test_a_slot_whose_run_failed_is_not_run_again(self):
        # take, 11-14: it ran at 09:30 and failed (a model outage). That is an
        # error to see, not a missed slot.
        take = dict(TAKE, catch_up_secs=6 * 3600)
        self.job("j3", take, "2026-11-15T09:30")
        self.run_of("j3", "2026-11-14T09:30:03", status="error")
        self.assertEqual(self.catch_up("2026-11-14T10:00", [take])["caught_up"], [])
        self.assertEqual(self.core.calls, [])

    def test_a_job_without_catch_up_waits_for_its_next_slot(self):
        self.job("j3", TAKE, "2026-11-15T09:30")
        out = self.catch_up("2026-11-14T10:00", [TAKE])
        self.assertEqual((out["caught_up"], self.core.calls), ([], []))

    def test_a_slot_older_than_the_window_is_dropped(self):
        self.job("j1", SELF_IMPROVE, "2026-11-15T09:40")
        self.assertEqual(self.catch_up("2026-11-14T15:41", [SELF_IMPROVE])["caught_up"], [])
        self.assertEqual(self.core.calls, [])

    def test_a_run_still_holding_its_slot_is_left_alone(self):
        self.job("j1", SELF_IMPROVE, "2026-11-14T09:40")
        self.lease("j1", "2026-11-14T09:40", "2026-11-14T09:59:40")
        self.assertEqual(self.catch_up("2026-11-14T10:00", [SELF_IMPROVE])["caught_up"], [])
        self.assertEqual(self.core.calls, [])

    def test_a_slot_just_past_is_left_to_the_core(self):
        # Next run still on the slot, nothing holding it, five minutes past:
        # a core that just booted takes it itself on its next poll.
        self.job("j1", SELF_IMPROVE, "2026-11-14T09:40")
        self.assertEqual(self.catch_up("2026-11-14T09:45", [SELF_IMPROVE])["caught_up"], [])
        self.assertEqual(self.catch_up("2026-11-14T09:51", [SELF_IMPROVE])["caught_up"][0]["name"],
                         "teeny.self-improve")

    def test_several_misses_are_caught_up_one_at_a_time_oldest_first(self):
        self.job("j1", SELF_IMPROVE, "2026-11-15T09:40")
        self.job("j2", FOLLOW, "2026-11-15T11:30")
        first = self.catch_up("2026-11-14T12:00", [FOLLOW, SELF_IMPROVE])
        self.assertEqual([c["name"] for c in first["caught_up"]], ["teeny.self-improve"])
        self.assertEqual(first["pending"], ["teeny.moltbook-follow"])
        second = self.catch_up("2026-11-14T12:15", [FOLLOW, SELF_IMPROVE])
        self.assertEqual([c["name"] for c in second["caught_up"]], ["teeny.moltbook-follow"])
        self.assertEqual(second["pending"], [])
        self.assertEqual(self.core.calls, [("cron_run", {"job_id": "j1"}), ("cron_run", {"job_id": "j2"})])

    def test_a_playbook_waits_out_the_quiet_hours_and_a_dream_does_not(self):
        os.environ["TEENY_QUIET_HOURS"] = "1-7"
        os.environ["CALENDAR_TZ"] = "Asia/Kolkata"
        late = dict(SELF_IMPROVE, expr="30 0 * * *")  # 00:30 India time, 19:00 UTC
        self.job("j1", late, "2026-11-15T19:00")
        self.job("j4", DREAM, "2026-11-15T20:30")
        # 20:45 UTC is 02:15 India time: quiet. The dream runs; the playbook waits.
        out = self.catch_up("2026-11-14T20:45", [late, DREAM])
        self.assertEqual([c["name"] for c in out["caught_up"]], ["teeny.dream"])
        self.assertEqual(out["deferred"], ["teeny.self-improve"])
        self.assertEqual(self.catch_up("2026-11-14T21:00", [late, DREAM])["caught_up"], [])
        # 01:31 UTC is 07:01 India time, but 6.5 hours after the slot: too late.
        self.assertEqual(self.catch_up("2026-11-15T01:31", [late, DREAM])["caught_up"], [])
        os.environ["TEENY_QUIET_HOURS"] = "1-1"
        self.assertEqual([c["name"] for c in self.catch_up("2026-11-14T21:15", [late, DREAM])["caught_up"]],
                         ["teeny.self-improve"])

    def test_a_job_defined_after_its_slot_has_nothing_to_catch_up(self):
        # Added at 10:00, after the day's 09:40.
        self.job("j1", SELF_IMPROVE, "2026-11-15T09:40", created="2026-11-14T10:00")
        self.assertEqual(self.catch_up("2026-11-14T10:01", [SELF_IMPROVE])["caught_up"], [])
        # Rescheduled (seed-cron updates it in place, keeping its creation time).
        self.jobs.clear()
        self.job("j1", SELF_IMPROVE, "2026-11-15T09:40")
        self.assertEqual(self.catch_up("2026-11-14T10:02", [SELF_IMPROVE], fresh=["teeny.self-improve"])["caught_up"], [])
        self.assertEqual(self.catch_up("2026-11-14T10:30", [SELF_IMPROVE])["caught_up"], [])
        self.assertEqual(self.core.calls, [])

    def test_a_disabled_job_is_not_caught_up(self):
        self.job("j1", SELF_IMPROVE, "2026-11-15T09:40", enabled=False)
        self.assertEqual(self.catch_up("2026-11-14T10:00", [SELF_IMPROVE])["caught_up"], [])
        self.job("j2", FOLLOW, "2026-11-15T11:30")
        self.assertEqual(self.catch_up("2026-11-14T12:00", [dict(FOLLOW, enabled=False)])["caught_up"], [])
        self.assertEqual(self.core.calls, [])

    def test_a_run_the_core_refused_is_reported_and_tried_again(self):
        self.job("j1", SELF_IMPROVE, "2026-11-15T09:40")
        self.core.refuse = {"cron_run"}
        out = self.catch_up("2026-11-14T10:00", [SELF_IMPROVE])
        self.assertEqual(out["caught_up"], [])
        self.assertIn("not queued", out["errors"]["teeny.self-improve"])
        self.core.refuse = set()
        self.assertEqual(self.catch_up("2026-11-14T10:15", [SELF_IMPROVE])["caught_up"][0]["name"],
                         "teeny.self-improve")

    def test_a_dry_run_says_what_it_would_do_and_does_nothing(self):
        self.job("j1", SELF_IMPROVE, "2026-11-15T09:40")
        out = self.catch_up("2026-11-14T10:00", [SELF_IMPROVE], dry_run=True)
        self.assertEqual(out["caught_up"], [{"name": "teeny.self-improve", "slot": "2026-11-14T09:40:00Z"}])
        self.assertEqual(self.core.calls, [])
        self.assertEqual(self.catch_up("2026-11-14T10:05", [SELF_IMPROVE])["caught_up"][0]["name"],
                         "teeny.self-improve")

    def test_no_storage_is_nothing_to_do(self):
        os.environ["TEENY_NOW"] = str(ts("2026-11-14T10:00"))
        self.assertEqual(catch_up.run([SELF_IMPROVE], self.core)["caught_up"], [])


if __name__ == "__main__":
    unittest.main()
