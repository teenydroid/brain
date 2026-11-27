"""evolve-watch's cron-failure verdict against noise: a failure the merged
change could not have caused (a spine restart, a provider or infrastructure
error, a job the change does not reach) is not counted, and what is counted
must be a real rise (an exact rate test), not a ratio on a handful of runs.

The g9c4 shape is the live one of 2026-11-16: four failed cron runs in the
22.19 hours after a moltbook-curate playbook edit, against thirteen in the
week before, reverted and halted the loop although none of them came from
the change.

The g21c2 shape is the one of 2026-11-20: a one-line rule added to
brain/SOUL.md, reverted 8.3 hours after its merge on "14 cron failures"
(40/day against 0.57/day), most of them bin/surplus-sweep, a job no prompt
reaches. A latched halt then held the loop for two days. Now a failure counts
only in a job that reads what the change touched, and a change every speaking
job reads (or one whose reach is unknown) needs a rise spread across jobs."""
from __future__ import annotations

import datetime as dt
import json
import os

# The cron readers go through bin/lib/cron_store.py; these tests build the old storage.db as their fixture.
os.environ.setdefault("TEENY_CRON_BACKEND", "sqlite")
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin" / "lib"))

from test_evolve import EvolveCase, FakePublisher  # noqa: E402
from test_evolve_lab import WATCH_GUARD  # noqa: E402

import evolve  # noqa: E402

HOUR = 3600.0
DAY = 86400.0


def at(text: str) -> float:
    return dt.datetime.fromisoformat(text).replace(tzinfo=dt.timezone.utc).timestamp()


MERGED = at("2026-11-15T14:08:45")  # PR #288 (g9c4)
CHECKED = at("2026-11-16T12:20:04")  # the watch that reverted it, 22.19 h on

JOBS = {
    "J-threads": ("teeny.moltbook-threads", "bin/act moltbook-threads"),
    "J-improve": ("teeny.self-improve", "bin/act self-improve"),
    "J-mail": ("teeny.mail-inbound", "bin/mail"),
    "J-mentions": ("teeny.mentions-inbound", "bin/mentions"),
    "J-helpdesk": ("teeny.moltbook-helpdesk", "bin/act moltbook-helpdesk"),
    "J-builder": ("teeny.builder", ""),
    "J-general": ("teeny.moltbook-general", "bin/act moltbook-general"),
    "J-take": ("teeny.take", "bin/act take"),
    "J-curate": ("teeny.moltbook-curate", "bin/act moltbook-curate"),
    "J-surplus": ("teeny.surplus-sweep", "bin/surplus-sweep"),
}

PAID = ("status=exit status: 1\nstdout:\n{\"ok\":false,\"error\":\"the draft turn failed\",\"stderr\":\"[oh] "
        "openhuman.inference_agent_chat: driver failed: model error: local-openai returned HTTP 402 "
        "(insufficient_allowance)\"}\nstderr:\n")
BLOCKED = ("status=exit status: 1\nstdout:\n{\"ok\":false,\"error\":\"the draft turn failed\",\"stderr\":\"[oh] "
           "openhuman.inference_agent_chat: Prompt blocked by security policy.\"}\nstderr:\n")
FLAGGED = ("status=exit status: 1\nstdout:\n{\"ok\":false,\"error\":\"the draft turn failed\",\"stderr\":\"[oh] "
           "openhuman.inference_agent_chat: Prompt flagged for security review and was not processed.\"}\nstderr:\n")
DOWN = ("status=exit status: 1\nstdout:\n{\"ok\": true}\nstderr:\ncurl: (7) Failed to connect to 127.0.0.1 port "
        "7788 after 0 ms: Couldn't connect to server\n[oh] core unreachable at http://127.0.0.1:7788: no response\n")
NO_KEY = "status=exit status: 1\nstdout:\n{\"ok\":false,\"error\":\"input new failed\"}\nstderr:\n"
WENT_WRONG = "Something went wrong. Please try again."
BROKEN = "status=exit status: 1\nstdout:\n{\"ok\":false,\"error\":\"the playbook does not parse\"}\nstderr:\n"

# Every failed cron run of the eight days before the check, as storage.db
# had them: (finished, job, output).
G9C4_FAILURES = [
    ("2026-11-11T10:45:16", "J-helpdesk", NO_KEY),
    ("2026-11-14T06:10:11", "J-builder", WENT_WRONG),
    ("2026-11-14T06:10:26", "J-general", PAID),
    ("2026-11-14T06:15:24", "J-threads", PAID),
    ("2026-11-14T06:45:30", "J-helpdesk", PAID),
    ("2026-11-14T07:10:27", "J-general", PAID),
    ("2026-11-14T08:10:11", "J-builder", WENT_WRONG),
    ("2026-11-14T08:10:27", "J-general", PAID),
    ("2026-11-14T08:15:25", "J-threads", PAID),
    ("2026-11-14T08:45:29", "J-helpdesk", PAID),
    ("2026-11-14T09:10:27", "J-general", PAID),
    ("2026-11-14T09:30:23", "J-take", PAID),
    ("2026-11-15T09:40:05", "J-improve", FLAGGED),
    # after the merge
    ("2026-11-16T06:15:25", "J-threads", BLOCKED),
    ("2026-11-16T09:40:08", "J-improve", FLAGGED),
    ("2026-11-16T11:40:05", "J-mail", DOWN),
    ("2026-11-16T11:40:05", "J-mentions", DOWN),
]
# state/boots.jsonl over the same days.
G9C4_BOOTS = ["2026-11-14T09:29:46", "2026-11-14T09:48:33", "2026-11-14T10:00:44", "2026-11-14T11:31:48",
              "2026-11-14T23:52:15", "2026-11-15T02:56:45", "2026-11-15T07:10:34", "2026-11-15T08:25:44",
              "2026-11-15T13:29:26", "2026-11-15T14:14:00", "2026-11-15T18:23:28", "2026-11-15T19:46:17",
              "2026-11-15T22:40:16", "2026-11-15T22:59:12", "2026-11-16T07:30:16", "2026-11-16T11:40:17"]
CURATE_PATCH = ("diff --git a/brain/playbooks/moltbook-curate.md b/brain/playbooks/moltbook-curate.md\n"
                "--- a/brain/playbooks/moltbook-curate.md\n+++ b/brain/playbooks/moltbook-curate.md\n"
                "@@ -16 +16 @@\n-old line\n+new line\n")


SOUL_PATCH = ("diff --git a/brain/SOUL.md b/brain/SOUL.md\n--- a/brain/SOUL.md\n+++ b/brain/SOUL.md\n"
              "@@ -40,0 +41 @@\n+- a moltbook parent with my reply already under it is answered.\n")
SWEEP_FAILED = ("status=exit status: 1\nstdout:\n{\"ok\":false,\"error\":\"bridge quote refused\"}\nstderr:\n")


class CronCase(EvolveCase):
    """A scratch spine with storage.db at TEENY_STORAGE, checked at CHECKED."""

    def setUp(self):
        super().setUp()
        for name in ("guard", "world"):
            (self.root / "hands" / name).write_text(WATCH_GUARD)
        holds = self.root / "holds.json"
        holds.write_text(json.dumps({"holds": []}))
        self.saved.setdefault("FAKE_HOLDS", os.environ.get("FAKE_HOLDS"))
        os.environ["FAKE_HOLDS"] = str(holds)
        os.environ["EVOLVE_NOW"] = str(CHECKED)
        os.environ["DRY_RUN"] = "1"
        self.storage = self.root / "storage.db"
        os.environ["TEENY_STORAGE"] = str(self.storage)
        self.config(EVOLVE=1, EVOLVE_PROBES=0)

    def merge(self, patch: str | None = CURATE_PATCH, merged: float = MERGED):
        entry = {"id": "g9c4", "gen": 9, "cand": 4, "parent": "main", "verdict": "opened", "gate": {"ok": True},
                 "title": "curate owns the nod", "pr_number": 288, "merged_at": evolve.iso(merged),
                 "merge_sha": "0" * 40}
        if patch is not None:
            (evolve.evolve_dir() / "9").mkdir(parents=True, exist_ok=True)
            (evolve.evolve_dir() / "9" / "cand-4.patch").write_text(patch)
            entry["patch"] = "9/cand-4.patch"
        evolve.record(entry)

    def cron(self, failures):
        conn = sqlite3.connect(self.storage)
        conn.execute("create table if not exists docs (coll text, id text, body text, deleted integer default 0)")
        for job, (name, command) in JOBS.items():
            conn.execute("insert into docs (coll, id, body) values ('cron.jobs', ?, ?)",
                         (job, json.dumps({"name": name, "command": command, "job_type": "shell"})))
        for i, (when, job, output) in enumerate(failures):
            ms = int((at(when) if isinstance(when, str) else when) * 1000)
            conn.execute("insert into docs (coll, id, body) values ('cron.runs', ?, ?)",
                         (f"r{i}", json.dumps({"job_id": job, "status": "error", "started_ms": ms - 3000,
                                               "finished_ms": ms, "output": output})))
        conn.commit()
        conn.close()

    def boots(self, times):
        with (evolve.state_dir() / "boots.jsonl").open("a") as fh:
            for when in times:
                fh.write(json.dumps({"at": when, "commit": "c", "fallback": False}) + "\n")

    def watched(self):
        out = evolve.watch(evolve.load_config())
        state = json.loads((evolve.secrets_dir() / "evolve-watch" / "state.json").read_text())
        return out, state["g9c4"]["checks"]["cron failures"]


class WatchNoise(CronCase):

    def test_the_g9c4_failures_are_not_a_spike(self):
        self.merge()
        self.cron(G9C4_FAILURES)
        self.boots(G9C4_BOOTS)
        out, got = self.watched()
        self.assertNotIn("reverted", out)
        self.assertFalse(got["spike"])
        self.assertEqual(got["after"], 0)  # none of the four came from the curate playbook
        self.assertEqual(got["left_out"]["after"], {"boot": 2, "infra": 0, "unreached": 2})

    def test_the_g9c4_failures_are_not_a_spike_even_when_the_reach_is_unknown(self):
        self.merge(patch=None)
        self.cron(G9C4_FAILURES)
        self.boots(G9C4_BOOTS)
        out, got = self.watched()
        self.assertNotIn("reverted", out)
        # the restart's two are left out; the two prompt-guard refusals count,
        # and two in a day against the week's rate is no evidence of a rise
        self.assertEqual(got["after"], 2)
        self.assertEqual(got["left_out"]["after"], {"boot": 2, "infra": 0, "unreached": 0})
        self.assertGreater(got["p"], 0.01)

    def test_even_the_raw_g9c4_counts_are_no_evidence_of_a_rise(self):
        cfg = evolve.load_config()
        times = [at(w) for w, _, _ in G9C4_FAILURES]
        got = evolve.spike(times, MERGED, CHECKED, cfg)
        self.assertEqual((got["after"], got["before"]), (4, 13))
        self.assertFalse(got["spike"])
        self.assertGreater(got["p"], 0.3)

    def test_a_real_spike_still_reverts(self):
        """20 failures of a job the change reaches in the day after the merge,
        against 2 a day the week before."""
        self.merge()
        week = [(MERGED - DAY * 7 + i * DAY / 2 + 600, "J-curate", BROKEN) for i in range(14)]
        day = [(MERGED + HOUR + i * HOUR, "J-curate", BROKEN) for i in range(20)]
        self.cron(week + day)
        out, got = self.watched()
        self.assertTrue(out["reverted"])
        self.assertEqual((got["after"], got["before"]), (20, 14))
        self.assertLess(got["p"], 1e-4)
        private = json.loads((evolve.secrets_dir() / "evolve-watch" / "g9c4.json").read_text())
        self.assertTrue(any(r.startswith("cron failures: 20") for r in private["reasons"]))

    def test_failures_around_a_restart_or_from_the_provider_do_not_count_but_the_same_failures_otherwise_do(self):
        self.merge(patch=None)
        restart = MERGED + 5 * HOUR
        noisy = [(restart - 30, "J-mail", BROKEN), (restart + 60, "J-mentions", BROKEN),
                 (restart + 120, "J-threads", BROKEN)]
        noisy += [(MERGED + 10 * HOUR + i * 600, "J-threads", PAID) for i in range(4)]
        self.cron(noisy)
        self.boots([evolve.iso(restart)])
        out, got = self.watched()
        self.assertNotIn("reverted", out)
        self.assertEqual(got["after"], 0)
        self.assertEqual(got["left_out"]["after"], {"boot": 3, "infra": 4, "unreached": 0})

        os.unlink(self.storage)
        (evolve.secrets_dir() / "evolve-watch" / "state.json").unlink()
        # Every job alike (no patch on record): spread over two jobs, so the
        # rise holds without the biggest one.
        self.cron([(MERGED + 10 * HOUR + i * 600, ("J-threads", "J-general")[i % 2], BROKEN) for i in range(7)])
        out, got = self.watched()
        self.assertTrue(out["reverted"])
        self.assertEqual(got["after"], 7)


class Attribution(CronCase):
    """Only failures in jobs that read a file the change touched count."""

    def test_a_soul_rule_is_not_blamed_for_a_job_that_never_reads_it(self):
        """g21c2: the spike was bin/surplus-sweep's, a hand job with no
        prompt; the one speaking job that failed failed once."""
        self.merge(patch=SOUL_PATCH)
        before = [(MERGED - 3 * DAY, "J-threads", BROKEN)] + [(MERGED - d * DAY, "J-improve", BROKEN) for d in (1, 2, 4)]
        after = [(MERGED + HOUR + i * 1800, "J-surplus", SWEEP_FAILED) for i in range(12)]
        after += [(MERGED + 5 * HOUR, "J-threads", BROKEN)]
        self.cron(before + after)
        out, got = self.watched()
        self.assertNotIn("reverted", out)
        self.assertFalse(got["spike"])
        self.assertEqual(got["after"], 1)
        self.assertEqual(got["left_out"]["after"]["unreached"], 12)
        self.assertEqual(got["reach"], "prompt")

    def test_a_job_that_reads_no_prompt_spiking_alone_is_not_blamed_on_an_unknown_reach(self):
        """No patch on record reaches every job, so one job's runaway is
        that job's own trouble: the rise must survive leaving it out."""
        self.merge(patch=None)
        self.cron([(MERGED + HOUR + i * 1800, "J-surplus", SWEEP_FAILED) for i in range(12)])
        out, got = self.watched()
        self.assertNotIn("reverted", out)
        self.assertEqual(got["after"], 12)
        self.assertFalse(got["spike"])
        self.assertFalse(got["spread"]["spike"])
        self.assertEqual(got["spread"]["without"], "bin/surplus-sweep")

    def test_a_soul_rule_that_breaks_the_speaking_jobs_still_reverts(self):
        self.merge(patch=SOUL_PATCH)
        after = [(MERGED + HOUR + i * 900, job, BROKEN)
                 for i, job in enumerate(["J-threads", "J-general", "J-helpdesk"] * 4)]
        self.cron([(MERGED - 3 * DAY, "J-threads", BROKEN)] + after)
        out, got = self.watched()
        self.assertTrue(out["reverted"])
        self.assertTrue(got["spike"])
        self.assertTrue(got["spread"]["spike"])
        self.assertEqual(got["jobs"]["after"], {"bin/act moltbook-general": 4, "bin/act moltbook-helpdesk": 4,
                                                "bin/act moltbook-threads": 4})

    def test_the_watch_reverts_files_one_public_issue_and_the_next_run_goes(self):
        """After a revert nothing waits for a person but the revert pull
        request itself: the next scheduled run starts a generation, and the
        one public issue carries the numbers that used to stay on the box."""
        os.environ.pop("DRY_RUN", None)
        self.live()
        self.merge()  # the curate playbook: bin/act moltbook-curate alone
        week = [(MERGED - DAY * 7 + i * DAY / 2 + 600, "J-curate", BROKEN) for i in range(14)]
        day = [(MERGED + HOUR + i * HOUR, "J-curate", BROKEN) for i in range(20)]
        self.cron(week + day + [(MERGED + 2 * HOUR, "J-surplus", SWEEP_FAILED)])
        pub = FakePublisher()
        out = evolve.watch(evolve.load_config(), publisher=pub)
        self.assertTrue(out["reverted"])
        (issue,) = pub.issues
        body = issue["body"]
        self.assertIn("g9c4", body)
        self.assertIn("pull/288", body)
        for number in ("20 in 22.2 h", "21.6", "2.0/day", "p "):
            self.assertIn(number, body)
        self.assertIn("bin/act moltbook-curate", body)
        self.assertIn("left out", body)
        self.assertNotIn("bridge quote refused", body)  # job names and counts, never a run's output
        self.assertNotIn("TEENY", body)
        self.assertEqual(pub.labels[0]["labels"], ["help wanted"])
        self.assertIsNone(evolve.watch(evolve.load_config(), publisher=pub)["watching"])
        self.assertEqual(len(pub.issues), 1)
        self.config(EVOLVE=1, EVOLVE_PROBES=0, EVOLVE_EVALUATOR="none", EVOLVE_GENERATIONS_PER_DAY=4)
        self.propose(1, [self.playbook_edit("One.")])
        got = self.run_gen(1, publisher=pub, scheduled=True)
        self.assertIn("gen", got, got)
        self.assertNotIn("skipped", got)
        self.assertEqual(len(pub.issues), 1)


class CronFailuresFeedBoth(CronCase):
    """observe() and watch() both read storage.db's failed cron runs, in two
    shapes: observe a count per job (evolve_evidence.outcomes), watch each
    run with its output. One once shadowed the other and every generation
    crashed in observe (gen 14, 2026-11-17)."""

    def test_observe_lists_a_failing_cron_job_with_its_count(self):
        self.cron([(CHECKED - HOUR, "J-curate", BROKEN), (CHECKED - 2 * HOUR, "J-curate", BROKEN)])
        failing = evolve.observe({})["outcomes"]["failing"]
        self.assertIn({"job": "teeny.moltbook-curate", "failed_runs": 2}, [
            {k: r[k] for k in ("job", "failed_runs")} for r in failing])

    def test_watch_still_reads_each_failed_run(self):
        self.cron([(CHECKED - HOUR, "J-curate", BROKEN)])
        runs = evolve._cron_failure_runs(CHECKED - DAY)
        self.assertEqual(runs, [{"at": CHECKED - HOUR, "job": "bin/act moltbook-curate", "output": BROKEN}])
        self.assertEqual(evolve._cron_failure_counts(CHECKED - DAY), {"teeny.moltbook-curate": 1})


if __name__ == "__main__":
    unittest.main()
