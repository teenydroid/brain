"""bin/health: the pinned check that notices Teeny's model is not answering
(the 2026-11-11 outage: a revoked provider key, seven hours of failed turns
and judge holds, and nobody told). It asks the model through the core every
run (again, up to three attempts, after a failure that may pass); two
failed checks in a row raise one alarm, as a `health` stimulus and one issue
on teenydroid/brain, and the first answer after that closes both. Against a fake core, a fake GitHub and a pinned clock."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "lib"))
sys.path.insert(0, str(REPO / "bin" / "lib"))

from guard.support import FakeCore, error, result  # noqa: E402
from x.support import FakeX  # noqa: E402
from teeny_hands import hand, stimuli  # noqa: E402
from teeny_hands.guard import holds  # noqa: E402
import health  # noqa: E402

T0 = 1_794_398_400.0  # 2026-11-11T12:00:00Z
QUARTER = 900
TOKEN = "ghp_FAKEtokenFAKEtokenFAKEtoken0000"
DEAD = {"jsonrpc": "2.0", "id": 1, "error": {"message": "local-openai returned HTTP 401 (invalid_api_key)"}}
ALIVE = result({"response": "ok"})
# What the core said, live, when Surplus dropped the model's stream (2026-11-14).
CUT = error("model error: local-openai returned: provider stream ended before a completion signal")
ISSUES = "/repos/teenydroid/brain/issues"


class HealthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        (self.root / "state").mkdir()
        self._env = dict(os.environ)
        for key in ("DRY_RUN", "OPENHUMAN_CORE_URL", "OPENHUMAN_CORE_TOKEN", "TEENY_STORAGE", "OPENHUMAN_WORKSPACE"):
            os.environ.pop(key, None)
        os.environ["TEENY_ROOT"] = str(self.root)
        self.at(0)
        self.core = FakeCore(ALIVE)
        self.addCleanup(self.core.close)
        (self.root / "state" / "core.url").write_text(self.core.url + "\n")
        (self.root / "secrets" / "core.token").write_text("tok\n")
        self.gh = FakeX()
        self.addCleanup(self.gh.close)
        self.gh.route("POST", ISSUES, {"number": 41, "html_url": "https://github.com/teenydroid/brain/issues/41"}, status=201)
        self.gh.route("POST", f"{ISSUES}/41/comments", {"id": 5, "html_url": "c"}, status=201)
        self.gh.route("PATCH", f"{ISSUES}/41", {"number": 41, "state": "closed", "html_url": "u"})
        # The judge on, as in production: a judged alarm would be held
        # exactly when it is needed.
        self.configure()
        # The probe waits between its attempts; the fake core needs none.
        self.slept: list[float] = []
        sleep = mock.patch.object(health, "_sleep", self.slept.append, create=True)
        sleep.start()
        self.addCleanup(sleep.stop)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **overrides):
        values = {"GITHUB_TOKEN": TOKEN, "GITHUB_API_BASE": self.gh.base, "DRY_RUN": "0", "SAFETY_JUDGE": "1"}
        values.update(overrides)
        text = "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    def at(self, offset: float) -> None:
        os.environ["TEENY_NOW"] = str(T0 + offset)

    def model(self, reply) -> None:
        self.core.replies = [reply]

    def state(self) -> dict:
        return json.loads((self.root / "state" / "health.json").read_text())

    def inbox(self) -> list[dict]:
        return stimuli.recent()["stimuli"][::-1]

    def outage(self, checks: int = 2, reply=DEAD) -> list[dict]:
        """`checks` failed checks a quarter of an hour apart, from T0."""
        self.model(reply)
        outs = []
        for n in range(checks):
            self.at(n * QUARTER)
            outs.append(health.check())
        return outs

    # --- the probe ------------------------------------------------------------------------

    def test_a_healthy_check_asks_the_model_once_and_says_nothing(self):
        out = health.check()
        self.assertEqual((out["ok"], out["healthy"], out["failures"], out["alarm"]), (True, True, 0, None))
        self.assertEqual(len(self.core.calls), 1)
        call = self.core.calls[0]
        self.assertEqual(call["method"], "openhuman.inference_agent_chat_simple")
        self.assertLess(len(call["params"]["message"]), 200)
        self.assertEqual(self.state()["last_ok_at"], T0)
        self.assertEqual(self.inbox(), [])
        self.assertEqual(self.gh.seen, [])

    def test_an_empty_answer_is_a_failure(self):
        self.model(result({"response": "  "}))
        out = health.check()
        self.assertFalse(out["healthy"])
        self.assertEqual(out["error"], "the model answered with nothing")

    def test_one_failure_is_not_an_alarm(self):
        (out,) = self.outage(1)
        self.assertEqual((out["ok"], out["healthy"], out["failures"], out["alarm"]), (True, False, 1, None))
        self.assertIn("HTTP 401 (invalid_api_key)", out["error"])
        self.assertEqual(self.state()["first_failed_at"], T0)
        self.assertEqual(self.inbox(), [])
        self.assertEqual(self.gh.seen, [])

    def test_an_answer_after_one_failure_starts_the_count_again(self):
        self.outage(1)
        self.model(ALIVE)
        self.at(QUARTER)
        self.assertEqual(health.check()["failures"], 0)
        self.model(DEAD)
        self.at(2 * QUARTER)
        self.assertEqual(health.check()["alarm"], None)
        self.assertEqual(self.inbox(), [])

    # --- asking again within one probe (2026-11-14) ----------------------------------------

    def test_a_probe_cut_once_then_answered_is_no_failure_and_says_it_was_asked_again(self):
        # Surplus closes a stream now and then in a window of minutes; on
        # 2026-11-14 two such probes in a row opened outage issue #256, which
        # closed itself fifteen minutes later.
        self.core.replies = [CUT, ALIVE]
        out = health.check()
        self.assertEqual((out["healthy"], out["failures"], out["alarm"]), (True, 0, None))
        self.assertEqual(out["probe_attempts"], 2)
        self.assertIn("provider stream ended", out["probe_retried"])
        self.assertEqual(len(self.core.calls), 2)
        self.assertEqual(self.slept, [health.PROBE_BACKOFFS[0]])
        self.assertEqual(self.state()["probe_attempts"], 2)
        self.assertEqual(self.state()["failures"], 0)

    def test_a_probe_answered_at_once_says_one_attempt(self):
        out = health.check()
        self.assertEqual((out["probe_attempts"], out.get("probe_retried")), (1, None))
        self.assertEqual(self.state()["probe_attempts"], 1)
        self.assertEqual(self.slept, [])

    def test_a_probe_cut_at_every_attempt_is_one_failure(self):
        (out,) = self.outage(1, reply=CUT)
        self.assertEqual((out["healthy"], out["failures"], out["alarm"]), (False, 1, None))
        self.assertIn("provider stream ended", out["error"])
        self.assertEqual(out["probe_attempts"], health.PROBE_ATTEMPTS)
        self.assertEqual(len(self.core.calls), 3)
        self.assertEqual(self.slept, list(health.PROBE_BACKOFFS))
        self.assertEqual(self.state()["probe_attempts"], 3)

    def test_an_outage_that_cuts_every_attempt_still_alarms_at_the_second_check(self):
        first, second = self.outage(2, reply=CUT)
        self.assertEqual((first["failures"], first["alarm"]), (1, None))
        self.assertEqual((second["failures"], second["alarm"], second["issue"]), (2, "raised", 41))

    def test_a_failure_that_will_not_pass_is_not_asked_again(self):
        (out,) = self.outage(1)  # a refused key (HTTP 401)
        self.assertEqual((out["failures"], out["probe_attempts"]), (1, 1))
        self.assertEqual(len(self.core.calls), 1)
        self.assertEqual(self.slept, [])

    def test_the_attempts_together_never_take_longer_than_the_probe_budget(self):
        # Every attempt times out, the worst case: each gets what is left of
        # PROBE_BUDGET (at most PROBE_TIMEOUT), and one that would get less
        # than MIN_PROBE_SECS is not made, so the check stays well inside the
        # core's 300 s for a cron shell call.
        clock = [1000.0]
        timeouts: list[float] = []

        def timed_out(method, params, timeout):
            timeouts.append(timeout)
            clock[0] += timeout
            raise hand.CoreError("core unreachable: timed out")

        def sleep(seconds):
            self.slept.append(seconds)
            clock[0] += seconds

        with mock.patch.object(health, "_sleep", sleep), \
                mock.patch.object(health.time, "monotonic", lambda: clock[0]), \
                mock.patch.object(health.hand, "core", timed_out):
            error_, attempts, errors = health.probe_until_answered()
        self.assertIn("timed out", error_)
        self.assertEqual(timeouts, [health.PROBE_TIMEOUT, health.PROBE_BUDGET - health.PROBE_TIMEOUT - health.PROBE_BACKOFFS[0]])
        self.assertEqual((attempts, len(errors)), (2, 2))
        self.assertLessEqual(clock[0] - 1000.0, health.PROBE_BUDGET)
        self.assertEqual(health.PROBE_BUDGET, 90.0)

    # --- the alarm ------------------------------------------------------------------------

    def test_the_second_failure_in_a_row_raises_one_alarm(self):
        first, second = self.outage(2)
        self.assertEqual((second["failures"], second["alarm"], second["issue"]), (2, "raised", 41))
        (alarm,) = self.inbox()
        self.assertEqual((alarm["surface"], alarm["kind"], alarm["ref"]), ("health", "alarm", f"outage:{int(T0)}"))
        self.assertIn("15 minutes", alarm["text"])
        self.assertIn("HTTP 401 (invalid_api_key)", alarm["text"])
        self.assertIn("teenydroid/brain#41", alarm["text"])
        (opened,) = self.gh.requests("POST", ISSUES)
        issue = opened.json()
        self.assertIn("2026-11-11 12:00 UTC", issue["title"])
        self.assertIn("HTTP 401 (invalid_api_key)", issue["body"])
        self.assertEqual(self.state()["alarm"]["issue"], 41)

    def test_the_alarm_does_not_wait_on_the_dead_judge(self):
        self.outage(2)
        # Only the two probes reached the core: the issue was not judged.
        self.assertEqual(len(self.core.calls), 2)
        self.assertEqual(holds.list_holds(), [])

    def test_an_open_alarm_files_nothing_more(self):
        outs = self.outage(4)
        self.assertEqual([o["alarm"] for o in outs], [None, "raised", "open", "open"])
        self.assertEqual(len(self.gh.requests("POST", ISSUES)), 1)
        self.assertEqual(len(self.inbox()), 1)
        self.assertEqual(outs[-1]["issue"], 41)

    def test_the_answer_coming_back_closes_the_alarm_and_the_issue(self):
        self.outage(3)
        self.model(ALIVE)
        self.at(3 * QUARTER)
        out = health.check()
        self.assertEqual((out["healthy"], out["failures"], out["alarm"], out["issue"]), (True, 0, "cleared", 41))
        alarm, back = self.inbox()
        self.assertEqual((back["surface"], back["kind"], back["ref"]), ("health", "recovered", alarm["ref"]))
        self.assertIn("45 minutes", back["text"])
        (comment,) = self.gh.requests("POST", f"{ISSUES}/41/comments")
        self.assertIn("answers again", comment.json()["body"])
        self.assertEqual(self.gh.requests("PATCH", f"{ISSUES}/41")[0].json()["state"], "closed")
        self.assertIsNone(self.state()["alarm"])
        # The next outage is a new alarm with a new issue.
        self.model(DEAD)
        for n in (4, 5):
            self.at(n * QUARTER)
            out = health.check()
        self.assertEqual(out["alarm"], "raised")
        self.assertEqual(len(self.gh.requests("POST", ISSUES)), 2)

    def test_under_dry_run_the_alarm_is_raised_and_no_issue_is_opened(self):
        self.configure(DRY_RUN="1")
        out = self.outage(2)[-1]
        self.assertEqual((out["alarm"], out["issue"]), ("raised", None))
        self.assertTrue(out["issue_dry_run"])
        self.assertEqual(self.gh.seen, [])
        self.assertIn("DRY_RUN", self.inbox()[0]["text"])

    def test_an_issue_github_refused_is_tried_again_on_the_next_check(self):
        self.gh.route("POST", ISSUES, {"message": "Server Error"}, status=500)
        out = self.outage(2)[-1]
        self.assertEqual((out["alarm"], out["issue"]), ("raised", None))
        self.assertIn("GitHub answered 500", out["issue_error"])
        self.gh.routes.pop(0)
        self.at(2 * QUARTER)
        out = health.check()
        self.assertEqual((out["alarm"], out["issue"]), ("open", 41))
        self.assertEqual(len(self.inbox()), 1)

    def test_what_is_made_public_carries_no_secret_and_no_url(self):
        leaky = {"jsonrpc": "2.0", "id": 1, "error": {
            "message": "provider at http://10.0.0.5:8080/v1 said: Incorrect API key provided: sk-proj-abcdEFGH1234ijklMNOP5678"}}
        out = self.outage(2, leaky)[-1]
        sent = self.gh.requests("POST", ISSUES)[0].json()["body"]
        for where in (sent, self.inbox()[0]["text"], json.dumps(out), json.dumps(self.state())):
            self.assertNotIn("abcdEFGH1234", where)
            self.assertNotIn("10.0.0.5", where)
        self.assertIn("Incorrect API key provided", sent)

    def test_a_broken_state_file_is_a_fresh_start(self):
        (self.root / "state" / "health.json").write_text("{not json")
        self.assertEqual(health.check()["failures"], 0)

    # --- what else is failing ------------------------------------------------------------

    def test_the_alarm_counts_judge_holds_and_failed_cron_runs_of_the_last_hour(self):
        self.at(-2 * 3600)
        holds.record(direction="outbound", kind="draft", context={"surface": "x"}, category="unknown",
                     reason="the judge could not be reached: old", by="judge", text="t")
        self.at(-60)
        for reason in ("the judge could not be reached: openhuman.inference_agent_chat_simple: 401", "contains \"x\""):
            holds.record(direction="outbound", kind="draft", context={"surface": "x"}, category="unknown",
                         reason=reason, by="judge", text="t")
        storage = self.root / "brain" / "storage.db"
        storage.parent.mkdir()
        db = sqlite3.connect(storage)
        db.execute("create table docs (id text, coll text, body text, deleted integer default 0)")
        ms = int(T0 * 1000)
        rows = [("j1", "cron.jobs", {"name": "teeny.operator"}), ("j2", "cron.jobs", {"name": "teeny.tick"}),
                ("r1", "cron.runs", {"job_id": "j1", "status": "error", "finished_ms": ms - 60_000, "seq": 1}),
                ("r2", "cron.runs", {"job_id": "j1", "status": "error", "finished_ms": ms - 120_000, "seq": 2}),
                ("r3", "cron.runs", {"job_id": "j2", "status": "ok", "finished_ms": ms - 60_000, "seq": 3}),
                ("r4", "cron.runs", {"job_id": "j2", "status": "error", "finished_ms": ms - 7_200_000, "seq": 4})]
        db.executemany("insert into docs (id, coll, body) values (?, ?, ?)", [(i, c, json.dumps(b)) for i, c, b in rows])
        db.execute("insert into docs (id, coll, body) values ('t1', 'transcripts', 'not json')")
        db.commit()
        db.close()
        out = self.outage(2)[-1]
        self.assertEqual((out["judge_holds"], out["cron_errors"]), (1, {"teeny.operator": 2}))
        body = self.gh.requests("POST", ISSUES)[0].json()["body"]
        self.assertIn("teeny.operator (2)", body)
        self.assertIn("teeny.operator (2)", self.inbox()[0]["text"])

    # --- yesterday as one stimulus (docs/roadmap.md, A4) ---------------------------------
    # T0 is 12:00 UTC on 2026-11-11, so "yesterday" is 2026-11-10.

    def yesterday(self, cron=(), acts=(), beats=(), doctor=None, holds_at=()) -> None:
        """Fixtures for the day before T0: failed cron runs `(job name, hours
        before T0)`, bin/act lines `(playbook, ok, hours before T0)`, tick
        lines `(kind, hours before T0)`, a doctor report, and guard holds
        `(direction, category, hours before T0)`."""
        def iso(hours):
            return health._iso(int((T0 - hours * 3600) * 1000))
        if cron:
            storage = self.root / "brain" / "storage.db"
            storage.parent.mkdir(exist_ok=True)
            db = sqlite3.connect(storage)
            db.execute("create table docs (id text, coll text, body text, deleted integer default 0)")
            names = sorted({name for name, _ in cron})
            db.executemany("insert into docs (id, coll, body) values (?, 'cron.jobs', ?)",
                           [(f"j{n}", json.dumps({"name": name})) for n, name in enumerate(names)])
            for n, (name, hours) in enumerate(cron):
                body = {"job_id": f"j{names.index(name)}", "status": "error",
                        "finished_ms": int((T0 - hours * 3600) * 1000), "seq": n}
                db.execute("insert into docs (id, coll, body) values (?, 'cron.runs', ?)", (f"r{n}", json.dumps(body)))
            db.commit()
            db.close()
        for playbook, ok, hours in acts:
            folder = self.root / "state" / "act" / playbook
            folder.mkdir(parents=True, exist_ok=True)
            with open(folder / "runs.jsonl", "a") as fh:
                fh.write(json.dumps({"at": iso(hours), "ok": ok, "playbook": playbook, "error": "secret detail"}) + "\n")
        if beats:
            folder = self.root / "state" / "tick"
            folder.mkdir(parents=True, exist_ok=True)
            with open(folder / "runs.jsonl", "a") as fh:
                for kind, hours in beats:
                    fh.write(json.dumps({"at": iso(hours), "kind": kind, "error": "what the core said"}) + "\n")
                fh.write("not json\n")
        if doctor is not None:
            (self.root / "state" / "doctor.json").write_text(json.dumps(doctor))
        for direction, category, hours in holds_at:
            self.at(-hours * 3600)
            holds.record(direction=direction, kind="draft", context={"surface": "x"}, category=category,
                         reason="REASON-NEVER-SHOWN", by="screen", text="HELD-TEXT-NEVER-SHOWN")
        self.at(0)

    def costs(self, usd=0.42, calls=7) -> None:
        self.model(result({"totals": {"cost_usd": usd, "calls": calls}}))

    def test_a_failed_job_yesterday_is_one_daily_stimulus(self):
        self.yesterday(cron=[("teeny.mail-inbound", 20), ("teeny.mail-inbound", 14), ("teeny.tick", 2)])
        self.costs()
        out = health.daily()
        self.assertEqual((out["ok"], out["daily"], out["added"], out["trouble"]), (True, "2026-11-10", True, True))
        # The one 2 h before T0 is today's, not yesterday's.
        self.assertEqual(out["cron_errors"], {"teeny.mail-inbound": 2})
        (got,) = self.inbox()
        self.assertEqual((got["surface"], got["kind"], got["ref"]), ("health", "daily", "daily:2026-11-10"))
        self.assertIn("teeny.mail-inbound 2", got["text"])
        self.assertIn("$0.42 over 7 call(s)", got["text"])
        self.assertIn("yours to look into", got["text"])
        call = self.core.calls[-1]
        self.assertEqual((call["method"], call["params"]), ("openhuman.cost_report", {"days": 1}))
        self.assertEqual(self.gh.seen, [])

    def test_the_daily_stimulus_is_filed_once_a_day(self):
        self.yesterday(cron=[("teeny.tick", 20)])
        self.costs()
        self.assertTrue(health.daily()["added"])
        self.at(3600)
        self.assertFalse(health.daily()["added"])
        self.assertEqual(len(self.inbox()), 1)
        self.at(86400)
        self.assertEqual(health.daily()["daily"], "2026-11-11")
        self.assertEqual(len(self.inbox()), 2)

    def test_failed_playbooks_beats_doctor_checks_and_holds_are_counted_never_quoted(self):
        self.yesterday(
            acts=[("morning", False, 20), ("morning", False, 18), ("morning", True, 17), ("brief", False, 1)],
            beats=[("failed", 22), ("beat", 21), ("failed", 1)],
            doctor={"ok": False, "checks": [{"check": "cron rules", "status": "ok", "detail": "fine"},
                                            {"check": "pre-spine leftover", "status": "warn", "detail": "DETAIL-NEVER-SHOWN"},
                                            {"check": "agents have no tools", "status": "fail", "detail": "DETAIL-NEVER-SHOWN"}]},
            holds_at=[("outbound", "profanity", 20), ("outbound", "profanity", 19), ("inbound", "hate", 15),
                      ("outbound", "hate", 3), ("outbound", "a category\nwith a newline", 16)],
        )
        self.costs()
        out = health.daily()
        self.assertEqual(out["act_failures"], {"morning": 2})
        self.assertEqual(out["failed_beats"], 1)
        self.assertEqual(out["doctor"], {"warn": ["pre-spine leftover"], "fail": ["agents have no tools"]})
        self.assertEqual(out["holds"], {"inbound hate": 1, "outbound other": 1, "outbound profanity": 2})
        (got,) = self.inbox()
        for word in ("morning 2", "failed beats of your mind: 1", "fail: agents have no tools",
                     "outbound profanity 2", "inbound hate 1"):
            self.assertIn(word, got["text"])
        dumped = json.dumps(got)
        for secret in ("HELD-TEXT-NEVER-SHOWN", "REASON-NEVER-SHOWN", "DETAIL-NEVER-SHOWN", "secret detail",
                       "what the core said", "newline"):
            self.assertNotIn(secret, dumped)

    def test_a_quiet_day_still_says_what_it_cost(self):
        self.costs(usd=1.5, calls=40)
        out = health.daily()
        self.assertFalse(out["trouble"])
        (got,) = self.inbox()
        self.assertIn("$1.50 over 40 call(s)", got["text"])
        self.assertIn("failed cron runs: none", got["text"])
        self.assertNotIn("yours to look into", got["text"])

    def test_a_core_that_cannot_give_the_cost_still_files_the_day(self):
        self.model(DEAD)
        out = health.daily()
        self.assertIsNone(out["cost"])
        self.assertIn("unknown", self.inbox()[0]["text"])

    def test_the_daily_verb_by_the_command_line(self):
        self.costs()
        env = {**os.environ, "TEENY_ROOT": str(self.root)}
        proc = subprocess.run([str(REPO / "bin" / "health"), "daily"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["daily"], "2026-11-10")
        bad = subprocess.run([str(REPO / "bin" / "health"), "weekly"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(bad.returncode, 2)

    # --- Moltbook writes nobody can see ------------------------------------------------
    # 2026-11-12: Moltbook hides a write until its challenge is solved, and a
    # third of Teeny's writes were still unverified with nothing saying so.

    def moltbook_writes(self, *statuses: str, ago: float = 3600) -> None:
        from teeny_hands.moltbook import rules

        conn = rules.db()
        for n, status in enumerate(statuses):
            conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES (?, 'p', ?, ?)",
                         (f"c{n}-{ago}", T0 - ago, status))
        conn.close()

    def test_unverified_moltbook_writes_of_the_last_day_are_counted_without_an_alarm(self):
        self.moltbook_writes("verified", "posted", "needs_verification", "verification_failed")
        self.moltbook_writes("needs_verification", "needs_verification", ago=2 * 86400)
        self.moltbook_writes("refused")
        out = health.check()
        self.assertEqual((out["moltbook_unverified"], out["healthy"], out["alarm"]), (2, True, None))
        self.assertEqual(self.inbox(), [])
        self.assertEqual(self.gh.seen, [])

    def test_moltbook_writes_at_least_nine_in_ten_verified_or_too_few_are_no_count(self):
        self.assertIsNone(health.check()["moltbook_unverified"])
        self.moltbook_writes("needs_verification", "verified")
        self.assertIsNone(health.check()["moltbook_unverified"])
        self.moltbook_writes(*["verified"] * 8, ago=60)
        self.assertIsNone(health.check()["moltbook_unverified"])

    def test_no_storage_yet_is_no_cron_errors(self):
        self.assertEqual(health.check()["cron_errors"], {})

    # --- jobs stuck behind their schedule ----------------------------------------------
    # 2026-11-11: the box rebooted mid-run; the dead runs' claims held three
    # jobs' slots and their next_run stayed in the past for 75 minutes.

    def storage(self, jobs, leases=()) -> None:
        """The core's storage.db with cron jobs `(id, name, next run in seconds
        from T0, enabled)` and occurrence leases on their next run `(job id,
        seconds since the holder last renewed it)`."""
        storage = self.root / "brain" / "storage.db"
        storage.parent.mkdir(exist_ok=True)
        storage.unlink(missing_ok=True)
        db = sqlite3.connect(storage)
        db.execute("create table docs (scope text default '', coll text, id text, created_ms integer default 0,"
                   " updated_ms integer default 0, deleted integer default 0, body text)")
        next_ms = {}
        for job_id, name, offset, enabled in jobs:
            next_ms[job_id] = int((T0 + offset) * 1000)
            body = {"name": name, "enabled": enabled, "next_run_ms": next_ms[job_id], "expression": "*/5 * * * *",
                    "schedule": {"kind": "cron", "expr": "*/5 * * * *", "tz": "UTC"}}
            db.execute("insert into docs (coll, id, body) values ('cron.jobs', ?, ?)", (job_id, json.dumps(body)))
        for job_id, renewed_ago in leases:
            renewed = int((T0 - renewed_ago) * 1000)
            body = {"holder": "token", "epoch": 1, "expires_ms": renewed + 7_200_000}
            db.execute("insert into docs (coll, id, updated_ms, body) values ('tsd.leases', ?, ?, ?)",
                       (f"cron/{job_id}%2F{next_ms[job_id]}", renewed, json.dumps(body)))
        db.commit()
        db.close()

    def cron_updates(self) -> list[dict]:
        return [c["params"] for c in self.core.calls if c["method"] == "openhuman.cron_update"]

    def test_a_job_stuck_behind_its_schedule_is_rearmed_and_raises_the_alarm(self):
        self.storage([("j1", "teeny.mail-inbound", -4500, True), ("j2", "teeny.tick", 120, True)],
                     leases=[("j1", 4480)])
        out = health.check()
        self.assertTrue(out["healthy"])
        self.assertEqual(out["stale_crons"], [{"name": "teeny.mail-inbound", "next_run": "2026-11-11T10:45:00Z"}])
        self.assertEqual(self.cron_updates(), [{"job_id": "j1", "patch": {
            "schedule": {"kind": "cron", "expr": "*/5 * * * *", "tz": "UTC"}}}])
        self.assertEqual(out["rearmed"], ["teeny.mail-inbound"])
        self.assertEqual(out["cron_alarm"], "raised")
        (alarm,) = self.inbox()
        self.assertEqual((alarm["surface"], alarm["kind"]), ("health", "alarm"))
        self.assertIn("teeny.mail-inbound", alarm["text"])
        self.assertIn("re-armed", alarm["text"])
        (opened,) = self.gh.requests("POST", ISSUES)
        self.assertIn("teeny.mail-inbound", opened.json()["body"])
        self.assertEqual(self.state()["cron_alarm"]["issue"], 41)

    def test_a_job_whose_run_is_still_going_is_not_stuck(self):
        # Its next run is twenty minutes past, but the run holding that slot
        # renewed its claim seconds ago: it is running, not stuck.
        self.storage([("j1", "teeny.builder", -1200, True)], leases=[("j1", 20)])
        out = health.check()
        self.assertEqual((out["stale_crons"], out["rearmed"], out["cron_alarm"]), ([], [], None))
        self.assertEqual(self.cron_updates(), [])
        self.assertEqual(self.inbox(), [])

    def test_a_job_a_little_late_or_disabled_is_not_stuck(self):
        self.storage([("j1", "teeny.heartbeat", -300, True), ("j2", "teeny.paused", -7200, False)])
        out = health.check()
        self.assertEqual((out["stale_crons"], out["cron_alarm"]), ([], None))
        self.assertEqual(self.cron_updates(), [])

    def test_a_stuck_job_of_the_core_raises_the_alarm_but_is_not_rearmed(self):
        self.storage([("s1", "memory_background", -4500, True)])
        out = health.check()
        self.assertEqual([s["name"] for s in out["stale_crons"]], ["memory_background"])
        self.assertEqual((out["rearmed"], out["cron_alarm"]), ([], "raised"))
        self.assertEqual(self.cron_updates(), [])

    def test_a_rearm_the_core_refused_is_reported(self):
        self.storage([("j1", "teeny.health", -4500, True)])
        self.core.replies = [ALIVE, {"jsonrpc": "2.0", "id": 1, "error": {"message": "Cron job 'j1' not found"}}]
        out = health.check()
        self.assertEqual(out["rearmed"], [])
        self.assertIn("not found", out["rearm_errors"]["teeny.health"])
        self.assertEqual(out["cron_alarm"], "raised")

    def test_the_cron_alarm_clears_once_every_job_is_current(self):
        self.storage([("j1", "teeny.mail-inbound", -4500, True)])
        self.assertEqual(health.check()["cron_alarm"], "raised")
        self.at(QUARTER)
        self.assertEqual(health.check()["cron_alarm"], "open")
        self.assertEqual(len(self.gh.requests("POST", ISSUES)), 1)
        self.storage([("j1", "teeny.mail-inbound", 2 * QUARTER + 60, True)])
        self.at(2 * QUARTER)
        out = health.check()
        self.assertEqual(out["cron_alarm"], "cleared")
        alarm, back = self.inbox()
        self.assertEqual((back["kind"], back["ref"]), ("recovered", alarm["ref"]))
        self.assertEqual(self.gh.requests("PATCH", f"{ISSUES}/41")[0].json()["state"], "closed")
        self.assertIsNone(self.state()["cron_alarm"])

    def test_a_stuck_job_is_watched_while_the_model_is_down(self):
        self.storage([("j1", "teeny.mail-inbound", -4500, True)])
        self.core.replies = [DEAD, result({})]
        out = health.check()
        self.assertFalse(out["healthy"])
        self.assertEqual((out["rearmed"], out["cron_alarm"]), (["teeny.mail-inbound"], "raised"))

    # --- a missed slot is caught up, not dropped (bin/lib/catch_up.py) -------------------
    # 2026-11-14: a deploy at 11:31 cut off teeny.moltbook-follow's 11:30 run;
    # the 11:45 check re-armed it and said "they run again at their next
    # slot": the day's follows were dropped. self-improve's slot was dropped
    # at boot by bin/seed-cron the same way, twice, and nothing said so.

    def daily(self, jobs, leases=(), catch_up=()):
        """storage.db with daily jobs `(id, name, "MM HH", next run in seconds
        from T0)` (UTC), dead claims `(job id, seconds since renewed)` on their
        next run, and the schedule files giving `catch_up` to the names listed."""
        storage = self.root / "brain" / "storage.db"
        storage.parent.mkdir(exist_ok=True)
        storage.unlink(missing_ok=True)
        db = sqlite3.connect(storage)
        db.execute("create table docs (scope text default '', coll text, id text, created_ms integer default 0,"
                   " updated_ms integer default 0, deleted integer default 0, body text)")
        pinned = ""
        next_ms = {}
        for job_id, name, at, offset in jobs:
            expr = f"{at} * * *"
            next_ms[job_id] = int((T0 + offset) * 1000)
            body = {"name": name, "enabled": True, "next_run_ms": next_ms[job_id], "expression": expr,
                    "schedule": {"kind": "cron", "expr": expr, "tz": "UTC"}}
            db.execute("insert into docs (coll, id, body) values ('cron.jobs', ?, ?)", (job_id, json.dumps(body)))
            short = name.removeprefix("teeny.")
            pinned += f'[jobs.{short}]\nschedule = "{expr}"\ncommand = "bin/heartbeat"\ntz = "UTC"\n'
            pinned += "catch_up = true\n\n" if name in catch_up else "\n"
        for job_id, renewed_ago in leases:
            renewed = int((T0 - renewed_ago) * 1000)
            body = {"holder": "token", "epoch": 1, "expires_ms": renewed + 7_200_000}
            db.execute("insert into docs (coll, id, updated_ms, body) values ('tsd.leases', ?, ?, ?)",
                       (f"cron/{job_id}%2F{next_ms[job_id]}", renewed, json.dumps(body)))
        db.commit()
        db.close()
        (self.root / "brain" / "cron.toml").write_text("")
        (self.root / "evals").mkdir(exist_ok=True)
        (self.root / "evals" / "cron.pinned.toml").write_text(pinned)

    def cron_runs(self) -> list[dict]:
        return [c["params"] for c in self.core.calls if c["method"] == "openhuman.cron_run"]

    def test_a_stuck_job_that_catches_up_runs_now_and_the_alarm_says_so(self):
        # follow's 11:45 run was cut off at 11:46 by a restart; post's 11:40 too.
        self.daily([("j1", "teeny.follow", "45 11", -900), ("j2", "teeny.post", "40 11", -1200)],
                   leases=[("j1", 840), ("j2", 840)], catch_up=("teeny.follow",))
        self.core.replies = [ALIVE, result({"job_id": "j1", "status": "queued"})]
        out = health.check()
        self.assertEqual(sorted(out["rearmed"]), ["teeny.follow", "teeny.post"])
        self.assertEqual(out["caught_up"], [{"name": "teeny.follow", "slot": "2026-11-11T11:45:00Z"}])
        self.assertEqual(self.cron_runs(), [{"job_id": "j1"}])
        self.assertEqual(out["cron_alarm"], "raised")
        (alarm,) = self.inbox()
        self.assertIn("caught up now: teeny.follow (its 2026-11-11T11:45:00Z slot)", alarm["text"])
        self.assertIn("teeny.post waits for its next slot", alarm["text"])
        self.assertNotIn("teeny.follow waits", alarm["text"])
        body = self.gh.requests("POST", ISSUES)[0].json()["body"]
        self.assertIn("caught up now: teeny.follow", body)
        self.assertIn("teeny.post waits for its next slot", body)

    def test_a_slot_dropped_at_boot_is_caught_up_by_the_next_check(self):
        # Boot re-armed it to tomorrow before any check saw it stale, and the
        # boot's own catch-up did not run (an older spine): not stale, still missed.
        self.daily([("j1", "teeny.self-improve", "40 11", 86400 - 1200)], catch_up=("teeny.self-improve",))
        out = health.check()
        self.assertEqual((out["stale_crons"], out["cron_alarm"]), ([], None))
        self.assertEqual(out["caught_up"], [{"name": "teeny.self-improve", "slot": "2026-11-11T11:40:00Z"}])
        self.assertEqual(self.cron_runs(), [{"job_id": "j1"}])
        self.at(QUARTER)
        self.assertEqual(health.check()["caught_up"], [])
        self.assertEqual(len(self.cron_runs()), 1)

    def test_a_job_without_catch_up_still_waits_for_its_next_slot(self):
        self.daily([("j2", "teeny.post", "40 11", -1200)], leases=[("j2", 840)])
        out = health.check()
        self.assertEqual((out["rearmed"], out["caught_up"]), (["teeny.post"], []))
        self.assertEqual(self.cron_runs(), [])
        (alarm,) = self.inbox()
        self.assertIn("teeny.post waits for its next slot", alarm["text"])

    # --- playbook drafts the model provider failed (bin/act's run logs) ----------------------
    # 2026-11-14, 06:10 to 09:15 UTC: every draft failed with HTTP 402
    # (insufficient_allowance), 27 failed lines across four Moltbook
    # playbooks, about nine runs lost, and the noon post never made up.

    LOST = {"ok": False, "error": "the draft turn failed", "failure": "provider", "provider": "payment",
            "stderr": "[oh] model error: local-openai returned HTTP 402 (insufficient_allowance): see"
                      " https://surplus.example/pay?key=sk-live-123"}
    ANSWERED = {"ok": True, "actions": [{"action": "skip", "reason": "nothing new"}], "left_today": 1}

    def act_runs(self, playbook, *runs):
        """Lines in state/act/<playbook>/runs.jsonl: `(seconds from T0, line)`."""
        path = self.root / "state" / "act" / playbook / "runs.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            for offset, line in runs:
                at = health._iso(int((T0 + offset) * 1000))
                fh.write(json.dumps({"at": at, "playbook": playbook, **line}) + "\n")

    def act_alarms(self) -> list[dict]:
        return [s for s in self.inbox() if s["kind"] == "alarm" and s["ref"].startswith("act:")]

    def test_two_drafts_the_provider_failed_are_not_an_alarm(self):
        self.act_runs("moltbook-general", (-1200, self.LOST))
        self.act_runs("moltbook-threads", (-300, self.LOST))
        out = health.check()
        self.assertEqual((out["act_outage"]["runs"], out["act_alarm"]), (2, None))
        self.assertEqual((self.act_alarms(), self.gh.seen), ([], []))

    def test_one_runs_quick_retries_count_once(self):
        # The core's cron ran each failed run three times in twenty seconds.
        self.act_runs("moltbook-daily", (-600, self.LOST), (-594, self.LOST), (-588, self.LOST))
        out = health.check()
        self.assertEqual((out["act_outage"]["runs"], out["act_alarm"]), (1, None))

    def test_three_drafts_in_a_row_the_provider_failed_raise_one_alarm_while_the_probe_answers(self):
        self.act_runs("moltbook-general", (-3000, self.ANSWERED), (-1800, self.LOST))
        self.act_runs("moltbook-threads", (-1500, self.LOST))
        self.act_runs("moltbook-helpdesk", (-600, self.LOST))
        out = health.check()
        self.assertTrue(out["healthy"])
        self.assertEqual(out["act_outage"], {"runs": 3, "since": "2026-11-11T11:30:00Z", "provider": "payment",
                                             "by": {"moltbook-general": 1, "moltbook-threads": 1,
                                                    "moltbook-helpdesk": 1}})
        self.assertEqual((out["act_alarm"], out["act_issue"]), ("raised", 41))
        self.assertEqual(self.state()["act_alarm"]["issue"], 41)
        (alarm,) = self.act_alarms()
        self.assertIn("failed the draft of your last 3 playbook run(s) in a row", alarm["text"])
        (opened,) = self.gh.requests("POST", ISSUES)
        sent = opened.json()
        self.assertIn("playbook drafts fail on the model provider", sent["title"])
        self.assertIn("moltbook-helpdesk 1", sent["body"])
        self.assertNotIn("sk-live-123", sent["body"])
        self.assertNotIn("https://surplus", sent["body"])
        # The next check files nothing more.
        self.at(QUARTER)
        self.assertEqual(health.check()["act_alarm"], "open")
        self.assertEqual((len(self.gh.requests("POST", ISSUES)), len(self.act_alarms())), (1, 1))

    def test_two_failed_drafts_half_an_hour_apart_raise_it_too(self):
        self.act_runs("moltbook-general", (-2400, self.LOST), (-300, self.LOST))
        self.assertEqual(health.check()["act_alarm"], "raised")

    def test_the_thresholds_are_settings(self):
        self.configure(ACT_ALARM_RUNS="5", ACT_ALARM_MINUTES="90")
        self.act_runs("moltbook-general", (-2400, self.LOST), (-1200, self.LOST), (-300, self.LOST))
        self.assertEqual(health.check()["act_alarm"], None)

    def test_an_answered_draft_clears_the_alarm_and_closes_its_issue(self):
        self.act_runs("moltbook-general", (-2400, self.LOST), (-1200, self.LOST), (-300, self.LOST))
        health.check()
        self.act_runs("moltbook-general", (QUARTER - 60, self.ANSWERED))
        self.at(QUARTER)
        out = health.check()
        self.assertEqual((out["act_alarm"], out["act_outage"]), ("cleared", None))
        self.assertIsNone(self.state()["act_alarm"])
        self.assertEqual(self.gh.requests("PATCH", f"{ISSUES}/41")[0].json()["state"], "closed")
        self.assertIn("answered again", self.inbox()[-1]["text"])

    def test_a_playbooks_own_failures_are_no_provider_outage(self):
        own = {"ok": False, "error": "input feed failed", "failure": "playbook"}
        self.act_runs("moltbook-general", (-2400, own), (-1200, own), (-300, own))
        out = health.check()
        self.assertEqual((out["act_outage"], out["act_alarm"]), (None, None))

    def test_while_the_model_alarm_is_open_the_same_outage_files_one_issue(self):
        self.act_runs("moltbook-general", (-2400, self.LOST), (-1200, self.LOST), (-300, self.LOST))
        first, second = self.outage(2)
        self.assertEqual((second["alarm"], second["act_alarm"]), ("raised", None))
        self.assertEqual(second["act_outage"]["runs"], 3)
        self.assertEqual(len(self.gh.requests("POST", ISSUES)), 1)

    def noon(self, lost_at=-19800):
        """A noon post (06:30 UTC) with `make_up_hours = 6` whose run at
        `lost_at` from T0 the provider failed."""
        brain = self.root / "brain"
        (brain / "playbooks").mkdir(parents=True, exist_ok=True)
        (brain / "playbooks" / "noon.md").write_text('+++\ndescription = "x"\noutput = "post"\n+++\npost\n')
        storage = brain / "storage.db"
        storage.unlink(missing_ok=True)
        db = sqlite3.connect(storage)
        db.execute("create table docs (scope text default '', coll text, id text, created_ms integer default 0,"
                   " updated_ms integer default 0, deleted integer default 0, body text)")
        body = {"name": "teeny.noon", "enabled": True, "next_run_ms": int((T0 + 66600) * 1000),
                "schedule": {"kind": "cron", "expr": "30 6 * * *", "tz": "UTC"}}
        db.execute("insert into docs (coll, id, body) values ('cron.jobs', 'j9', ?)", (json.dumps(body),))
        run = {"job_id": "j9", "started_ms": int((T0 + lost_at) * 1000), "finished_ms": int((T0 + lost_at + 9) * 1000),
               "status": "ok"}
        db.execute("insert into docs (coll, id, body) values ('cron.runs', 'r1', ?)", (json.dumps(run),))
        db.commit()
        db.close()
        (brain / "cron.toml").write_text('[jobs.noon]\nschedule = "30 6 * * *"\ntz = "UTC"\ncommand = "bin/act noon"\n'
                                         "make_up_hours = 6\n")
        (self.root / "evals").mkdir(exist_ok=True)
        (self.root / "evals" / "cron.pinned.toml").write_text("")
        self.act_runs("noon", (lost_at + 10, self.LOST))

    def test_a_post_the_provider_lost_is_made_up_once_a_draft_is_answered_again(self):
        os.environ["TEENY_QUIET_HOURS"] = "off"
        self.noon()
        self.act_runs("moltbook-general", (-600, self.ANSWERED))
        self.core.replies = [ALIVE, result({"job_id": "j9", "status": "queued"})]
        out = health.check()
        self.assertEqual(out["caught_up"], [{"name": "teeny.noon", "slot": "2026-11-11T06:30:00Z",
                                             "lost": "provider"}])
        self.assertEqual(self.cron_runs(), [{"job_id": "j9"}])

    def test_a_lost_post_waits_while_the_drafts_still_fail(self):
        os.environ["TEENY_QUIET_HOURS"] = "off"
        self.noon()
        self.act_runs("moltbook-general", (-600, self.LOST))
        out = health.check()
        self.assertTrue(out["healthy"])
        self.assertEqual((out["caught_up"], self.cron_runs()), ([], []))

    # --- the re-screen -----------------------------------------------------------------------

    def outage_holds(self) -> tuple[int, int]:
        """An inbound mention and an own draft the judge held while it was
        down, ten minutes before T0."""
        self.at(-600)
        down = "the judge could not be reached: openhuman.inference_agent_chat_simple: core unreachable"
        mention = holds.record(direction="inbound", kind="message", context={"surface": "x", "post_id": "11"},
                               category="unknown", reason=down, by="judge", text="gm teeny")
        draft = holds.record(direction="outbound", kind="draft", context={"surface": "x"}, category="unknown",
                             reason="the judge answered in an unreadable shape", by="judge", text="gm frens")
        self.at(0)
        return mention["id"], draft["id"]

    def test_a_healthy_check_rescreens_what_the_outage_held(self):
        mention, draft = self.outage_holds()
        self.core.replies = [ALIVE, result({"response": json.dumps({"reason": "a greeting", "verdict": "clean"})})]
        out = health.check()
        self.assertTrue(out["healthy"])
        self.assertEqual({k: out["rescreen"][k] for k in ("checked", "cleared", "expired", "prohibited")},
                         {"checked": 2, "cleared": 1, "expired": 1, "prohibited": 0})
        self.assertEqual((holds.get(mention)["status"], holds.get(draft)["status"]), ("cleared", "expired"))
        # The probe, then the judge on the one inbound hold.
        self.assertEqual(len(self.core.calls), 2)

    def test_no_rescreen_runs_when_the_probe_failed(self):
        mention, draft = self.outage_holds()
        (out,) = self.outage(1)
        self.assertIsNone(out["rescreen"])
        self.assertEqual(len(self.core.calls), 1)
        for hold_id in (mention, draft):
            self.assertEqual((holds.get(hold_id)["status"], holds.get(hold_id)["rescreened_at"]), ("pending", None))

    def test_a_broken_rescreen_does_not_break_the_check(self):
        self.outage_holds()
        (self.root / "state" / "hands.db").unlink()
        (self.root / "state" / "hands.db").mkdir()
        out = health.check()
        self.assertTrue(out["healthy"])
        self.assertIn("rescreen_error", out)

    # --- the command ------------------------------------------------------------------------

    def test_bin_health_prints_one_json_line(self):
        env = {k: v for k, v in os.environ.items() if k != "TEENY_HANDS_DEBUG"}
        done = subprocess.run([str(REPO / "bin" / "health")], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, done.stdout)
        self.assertEqual({k: json.loads(lines[0])[k] for k in ("ok", "healthy")}, {"ok": True, "healthy": True})


if __name__ == "__main__":
    unittest.main()


class RoutingCore:
    """A core whose model answers by name: `alive` is the set of models that
    answer. `inference_agent_chat_simple` runs on `model_override`, else on
    the model the core is set to; `config_update_model_settings` sets it.
    `inference_agent_chat` (a beat of the mind) runs on the set model."""

    def __init__(self, configured: str, alive: set[str]) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        self.configured, self.alive, self.calls = configured, set(alive), []
        #: The core's model windows: get_config reads them, and
        #: inference_update_model_settings replaces them whole, as the core does.
        self.registry = [{"id": configured, "provider": "local-openai", "context_window": 1048576, "vision": False},
                         {"id": "studio-model", "provider": "local-openai", "context_window": 1000000}]
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                fake.calls.append(body)
                data = json.dumps(fake.answer(body["method"].split(".", 1)[-1], body.get("params") or {})).encode()
                self.send_response(200)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def answer(self, method, params):
        if method == "config_update_model_settings":
            self.configured = params["default_model"]
            return result({"ok": True})
        if method == "inference_update_model_settings":
            self.registry = params["model_registry"]
            return result({"ok": True})
        if method == "get_config":
            return result({"config": {"model_registry": self.registry}})
        if method in ("inference_agent_chat_simple", "inference_agent_chat"):
            model = params.get("model_override") or self.configured
            if model in self.alive:
                return result({"response": f"ok from {model}"})
            return {"jsonrpc": "2.0", "id": 1, "error": {"message": f"{model}: HTTP 503"}}
        return result({})

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FallbackRouteTest(unittest.TestCase):
    """docs/roadmap.md, A18: two failed checks in a row and a fallback model
    that answers move the core to it, with a diary note; the main model
    answering again moves it back. HealthTest's scratch and helpers, not its
    tests."""

    tearDown = HealthTest.tearDown
    configure = HealthTest.configure
    at = HealthTest.at
    inbox = HealthTest.inbox

    def setUp(self):
        HealthTest.setUp(self)
        self.core.close()
        self.core = RoutingCore("main-model", {"backup-model"})
        self.addCleanup(self.core.close)
        (self.root / "state" / "core.url").write_text(self.core.url + "\n")
        (self.root / "secrets" / "spine.env").write_text(
            "LADDER_URL=http://ladder/v1\nSPINE_MODEL=main-model\nSPINE_FALLBACK_MODEL=backup-model\n"
            "SPINE_FALLBACK_CONTEXT_WINDOW=128000\n")
        self.configure(DRY_RUN="1")

    def check_at(self, n):
        self.at(n * QUARTER)
        return health.check()

    def beat(self):
        """What a beat of the mind gets: a turn on the model the core is set to."""
        return hand.core("inference_agent_chat", {"message": "a beat", "agent_id": "teeny"}, timeout=5)

    def notes(self):
        path = self.root / "state" / "diary-notes.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def test_moving_to_the_fallback_keeps_every_other_models_window(self):
        # The registry write replaces the whole array: the fallback's window
        # written alone dropped the main model's and the studio's, and a turn
        # on either was then budgeted at 4,096 tokens and refused.
        self.check_at(0)
        self.check_at(1)
        self.assertEqual(self.core.configured, "backup-model")
        self.assertEqual({e["id"]: e["context_window"] for e in self.core.registry},
                         {"main-model": 1048576, "studio-model": 1000000, "backup-model": 128000})
        self.assertIs(self.core.registry[0]["vision"], False)

    def test_with_the_main_route_down_the_next_beat_runs_on_the_fallback(self):
        first = self.check_at(0)
        self.assertEqual((first["healthy"], first["route"]), (False, "main"))
        self.assertEqual(self.core.configured, "main-model")  # one failure is not two
        second = self.check_at(1)
        self.assertEqual(second["route"], "fallback", second)
        self.assertEqual(self.core.configured, "backup-model")
        self.assertEqual(self.beat()["response"], "ok from backup-model")
        settings = [c["params"] for c in self.core.calls if c["method"].endswith("config_update_model_settings")]
        self.assertEqual(settings[-1]["chat_provider"], "local-openai:backup-model")
        self.assertEqual(settings[-1]["agentic_provider"], "local-openai:backup-model")
        windows = [c["params"] for c in self.core.calls if c["method"].endswith("inference_update_model_settings")]
        self.assertEqual({e["id"]: e["context_window"] for e in windows[-1]["model_registry"]}["backup-model"], 128000)
        [note] = self.notes()
        self.assertIn("fallback model, backup-model", note["text"])
        self.assertEqual([s["kind"] for s in self.inbox() if s["surface"] == "health"][-1], "route")
        # The next check runs on the fallback and is healthy; main is asked
        # too, and while it is down nothing moves.
        third = self.check_at(2)
        self.assertEqual((third["healthy"], third["route"]), (True, "fallback"))
        self.assertEqual(len(self.notes()), 1)

    def test_back_on_main_once_it_answers_with_a_note(self):
        self.check_at(0)
        self.check_at(1)
        self.core.alive.add("main-model")
        out = self.check_at(2)
        self.assertEqual(out["route"], "main", out)
        self.assertEqual(self.core.configured, "main-model")
        self.assertEqual(self.beat()["response"], "ok from main-model")
        self.assertIn("answers again", self.notes()[-1]["text"])

    def test_no_fallback_model_no_switch(self):
        (self.root / "secrets" / "spine.env").write_text("SPINE_MODEL=main-model\n")
        self.check_at(0)
        out = self.check_at(1)
        self.assertNotIn("route", out)
        self.assertEqual(self.core.configured, "main-model")
        self.assertEqual(self.notes(), [])

    def test_a_fallback_that_is_down_too_is_not_switched_to(self):
        self.core.alive.clear()
        self.check_at(0)
        out = self.check_at(1)
        self.assertEqual(out["route"], "main")
        self.assertIn("did not answer either", out["route_error"])
        self.assertEqual(self.core.configured, "main-model")

    def test_after_a_reboot_back_on_main_the_failures_move_it_again(self):
        self.check_at(0)
        self.check_at(1)
        self.core.configured = "main-model"  # bin/spine boots on the main model
        self.check_at(2)
        out = self.check_at(3)
        self.assertEqual((out["route"], self.core.configured), ("fallback", "backup-model"))
        self.assertEqual(len(self.notes()), 1)  # already on the fallback: no second note

    def test_the_night_puts_the_notes_on_the_diary_page(self):
        import reflect

        self.check_at(0)
        self.check_at(1)
        text = reflect.diary_notes(T0 - 3600, T0 + 3600)
        self.assertTrue(text.startswith("## notes\n"))
        self.assertIn("backup-model", text)
        self.assertIsNone(reflect.diary_notes(T0 + 3600, T0 + 7200))
