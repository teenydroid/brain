"""bin/health: the pinned check that notices Teeny's model is not answering
(the 2026-11-11 outage: a revoked provider key, seven hours of failed turns
and judge holds, and nobody told). It asks the model once through the core
every run; two failures in a row raise one alarm, as a `health` stimulus
and one issue on teenydroid/brain, and the first answer after that closes
both. Against a fake core, a fake GitHub and a pinned clock."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "lib"))
sys.path.insert(0, str(REPO / "bin" / "lib"))

from guard.support import FakeCore, result  # noqa: E402
from x.support import FakeX  # noqa: E402
from teeny_hands import hand, stimuli  # noqa: E402
from teeny_hands.guard import holds  # noqa: E402
import health  # noqa: E402

T0 = 1_794_398_400.0  # 2026-11-11T12:00:00Z
QUARTER = 900
TOKEN = "ghp_FAKEtokenFAKEtokenFAKEtoken0000"
DEAD = {"jsonrpc": "2.0", "id": 1, "error": {"message": "local-openai returned HTTP 401 (invalid_api_key)"}}
ALIVE = result({"response": "ok"})
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

    def test_no_storage_yet_is_no_cron_errors(self):
        self.assertEqual(health.check()["cron_errors"], {})

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
