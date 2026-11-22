"""bin/broker: the loopback daemon through which the jailed `mind` agent acts.

Each test runs the real `bin/broker serve` on an ephemeral port against a
scratch TEENY_ROOT, with stand-in hands (TEENY_HANDS) that record what they
were asked and a stand-in core (TEENY_OH) that keeps its cron jobs in a file.
The broker holds no credentials: it reaches the world only through the hands,
exactly as bin/tick does, and creates only agent cron jobs for `mind`."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "lib"))

T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z

FAKE_HAND = r"""#!/usr/bin/env python3
import json, os, sys
name = os.path.basename(sys.argv[0])
body = sys.stdin.read()
with open(os.environ["FAKE_HANDS"], "a") as fh:
    fh.write(json.dumps({"hand": name, "argv": sys.argv[1:], "body": json.loads(body) if body.strip() else None,
                         "writer": os.environ.get("TEENY_WRITER")}) + "\n")
verb = sys.argv[1] if len(sys.argv) > 1 else ""
if (name, verb) == ("guard", "clarity"):
    print(os.environ.get("FAKE_CLARITY") or json.dumps({"ok": True, "clear": True})); sys.exit(0)
if (name, verb) == ("guard", "check"):
    print(json.dumps({"ok": True, "text": json.loads(body).get("text", "")})); sys.exit(0)
if (name, verb) == ("goals", "list"):
    print(json.dumps({"ok": True, "goals": [{"id": 1, "title": "be famous"}]})); sys.exit(0)
code = int(os.environ.get("FAKE_EXIT_" + name.upper(), "0"))
if code == 2:
    print(json.dumps({"ok": False, "refused": "daily cap"}))
elif code:
    print(json.dumps({"ok": False, "error": "boom"}))
else:
    print(json.dumps({"ok": True, "dry_run": bool(os.environ.get("DRY_RUN"))}))
sys.exit(code)
"""

FAKE_OH = r"""#!/usr/bin/env python3
import json, os, sys
method, params = sys.argv[1], json.loads(sys.argv[2] if len(sys.argv) > 2 else "{}")
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write(json.dumps({"method": method, "params": params}) + "\n")
jobs = json.load(open(os.environ["FAKE_JOBS"]))
if method == "cron_list":
    print(json.dumps(jobs))
elif method == "cron_add":
    job = {"id": f"job-{len(jobs) + 1}", "enabled": True, **params}
    json.dump(jobs + [job], open(os.environ["FAKE_JOBS"], "w"))
    print(json.dumps(job))
elif method == "cron_remove":
    json.dump([j for j in jobs if j["id"] != params["job_id"]], open(os.environ["FAKE_JOBS"], "w"))
    print("{}")
else:
    sys.exit(f"unexpected method {method}")
"""

TICK_JOB = {"id": "core-1", "name": "teeny.tick", "job_type": "shell", "command": "bin/tick --detach",
            "schedule": {"kind": "cron", "expr": "*/5 * * * *", "tz": "UTC"}, "enabled": True}


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _mind_job(slug: str, schedule: dict, n: int) -> dict:
    return {"id": f"m-{n}", "name": f"mind.{slug}", "job_type": "agent", "agent_id": "mind", "prompt": "p",
            "schedule": schedule, "enabled": True}


class BrokerCase(unittest.TestCase):
    """A scratch root, stand-in hands and core, and a broker started on demand."""

    SETTINGS = {"MIND": "1", "BROKER_PORT": "0", "DRY_RUN": "0", "STUDIO": "0"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.scratch = Path(self.tmp.name)
        self.root = self.scratch / "root"
        (self.root / "secrets").mkdir(parents=True)
        (self.root / "bin").symlink_to(REPO / "bin")
        self.hands = self.scratch / "hands"
        self.hands.mkdir()
        for name in ("x", "moltbook", "mail", "gh", "guard", "telegram", "wallet", "tasks", "people", "hn", "x402",
                     "lab", "build", "reddit", "goals"):
            _exe(self.hands / name, FAKE_HAND)
        _exe(self.scratch / "fake-oh", FAKE_OH)
        self.jobs_file = self.scratch / "jobs.json"
        self.set_jobs([TICK_JOB])
        self.calls = self.scratch / "calls.log"
        self.hands_log = self.scratch / "hands.log"
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("DRY_RUN", "STUDIO", "TEENY_STATE", "TEENY_SECRETS", "OPENHUMAN_WORKSPACE")
                    and not k.startswith("FAKE_")}
        self.env.update(TEENY_ROOT=str(self.root), TEENY_HANDS=str(self.hands), TEENY_OH=str(self.scratch / "fake-oh"),
                        FAKE_JOBS=str(self.jobs_file), FAKE_CALLS=str(self.calls), FAKE_HANDS=str(self.hands_log),
                        TEENY_NOW=str(T0))
        self.home = self.root / "state" / "home"
        self.proc = None

    def tearDown(self):
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
            for stream in (self.proc.stdout, self.proc.stderr):
                if stream:
                    stream.close()
        self.tmp.cleanup()

    # --- helpers ---------------------------------------------------------------------

    def settings(self, **more):
        values = {**self.SETTINGS, **more}
        text = "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)
        (self.root / "secrets" / "teeny.env").write_text(text)

    def set_jobs(self, jobs):
        self.jobs_file.write_text(json.dumps(jobs))

    def jobs(self):
        return json.loads(self.jobs_file.read_text())

    def broker(self, *argv, env=None):
        return subprocess.run([str(REPO / "bin" / "broker"), *argv], capture_output=True, text=True,
                              env={**self.env, **(env or {})}, timeout=60)

    def start(self, env=None, **settings):
        self.settings(**settings)
        url_file = self.home / ".broker-url"
        self.proc = subprocess.Popen([str(REPO / "bin" / "broker"), "serve"], env={**self.env, **(env or {})},
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 20
        while not url_file.exists():
            if self.proc.poll() is not None:
                self.fail(f"broker exited {self.proc.returncode}: {self.proc.stderr.read()}")
            if time.monotonic() > deadline:
                self.fail("broker never wrote its url")
            time.sleep(0.05)
        self.url = url_file.read_text().strip()
        self.token = (self.home / ".broker-token").read_text().strip()

    def post(self, path, body=None, token=None, raw=None, auth=None):
        data = raw if raw is not None else json.dumps({} if body is None else body).encode()
        req = urllib.request.Request(self.url + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        if auth is not None:
            req.add_header("Authorization", auth)
        else:
            req.add_header("Authorization", f"Bearer {self.token if token is None else token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def hand_calls(self):
        if not self.hands_log.exists():
            return []
        return [json.loads(line) for line in self.hands_log.read_text().splitlines()]

    def oh_calls(self, method=None):
        if not self.calls.exists():
            return []
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        return [c for c in calls if method is None or c["method"] == method]

    def ledger(self):
        path = self.root / "state" / "broker" / "ledger.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def caps(self):
        path = self.root / "state" / "tick" / "caps.json"
        return json.loads(path.read_text())["counts"] if path.exists() else {}


class StartUpTest(BrokerCase):
    def test_serve_refuses_while_the_mind_switch_is_off(self):
        for value in (None, "0"):
            self.settings(MIND=value)
            proc = self.broker("serve")
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("MIND", proc.stderr)
            self.assertFalse((self.home / ".broker-url").exists())

    def test_install_writes_the_token_url_and_client_into_home_and_exits(self):
        self.settings(MIND=None, BROKER_PORT="7791")
        proc = self.broker("install")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        token_file = self.home / ".broker-token"
        self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o600)
        first = token_file.read_text().strip()
        self.assertGreaterEqual(len(first), 40)
        self.assertEqual((self.home / ".broker-url").read_text().strip(), "http://127.0.0.1:7791")
        client = self.home / "bin" / "teeny"
        self.assertEqual(stat.S_IMODE(client.stat().st_mode), 0o755)
        self.assertEqual(client.read_text(), (REPO / "bin" / "lib" / "broker_client.py").read_text())
        self.assertEqual(self.broker("install").returncode, 0)
        self.assertNotEqual(token_file.read_text().strip(), first, "a token is minted afresh at every start")

    def test_the_port_defaults_to_7790(self):
        self.settings(BROKER_PORT=None)
        self.assertEqual(self.broker("install").returncode, 0)
        self.assertEqual((self.home / ".broker-url").read_text().strip(), "http://127.0.0.1:7790")

    def test_a_planted_bin_symlink_is_not_followed(self):
        """The agent may write state/home: a bin/ it turned into a link
        elsewhere must not make the broker write the client there."""
        outside = self.scratch / "outside"
        outside.mkdir()
        self.home.mkdir(parents=True)
        (self.home / "bin").symlink_to(outside)
        self.settings()
        self.assertEqual(self.broker("install").returncode, 0)
        self.assertFalse((outside / "teeny").exists())
        self.assertFalse((self.home / "bin").is_symlink())
        self.assertTrue((self.home / "bin" / "teeny").is_file())

    def test_serve_binds_loopback_and_answers(self):
        self.start()
        self.assertTrue(self.url.startswith("http://127.0.0.1:"), self.url)
        self.assertEqual(stat.S_IMODE((self.home / ".broker-token").stat().st_mode), 0o600)
        code, out = self.post("/goals")
        self.assertEqual((code, out["status"]), (200, "ok"))

    def test_the_server_is_bound_to_127_0_0_1_only(self):
        os.environ["TEENY_ROOT"] = str(self.root)
        try:
            sys.path.insert(0, str(REPO / "bin" / "lib"))
            import broker

            server = broker.bind(0, "t" * 43)
            try:
                self.assertEqual(server.server_address[0], "127.0.0.1")
            finally:
                server.server_close()
        finally:
            os.environ.pop("TEENY_ROOT", None)


class AuthTest(BrokerCase):
    def test_a_missing_or_wrong_token_is_401_and_runs_nothing(self):
        self.start()
        intent = {"do": "reply_x", "to": "1", "text": "hi"}
        for auth in ("", f"Bearer {self.token}x", f"Bearer {self.token[:-1]}", self.token, f"Basic {self.token}"):
            code, out = self.post("/do", intent, auth=auth)
            self.assertEqual(code, 401, auth)
            self.assertEqual(out["status"], "refused")
        code, _ = self.post("/wake", {"name": "a", "note": "n", "cron": "0 * * * *"}, token="nope")
        self.assertEqual(code, 401)
        self.assertEqual(self.hand_calls(), [])
        self.assertEqual(self.oh_calls("cron_add"), [])
        ledger = (self.root / "state" / "broker" / "ledger.jsonl").read_text()
        self.assertNotIn(self.token, ledger)
        self.assertNotIn(self.token[:-1], ledger)
        self.assertEqual(len(self.ledger()), 6)
        self.assertTrue(all(line["status"] == "refused" for line in self.ledger()))
        self.proc.terminate()
        out, err = self.proc.communicate(timeout=10)
        self.proc = None
        self.assertNotIn(self.token[:-1], out + err)

    def test_only_post_is_answered(self):
        self.start()
        for method in ("GET", "PUT", "DELETE"):
            req = urllib.request.Request(self.url + "/goals", method=method,
                                         headers={"Authorization": f"Bearer {self.token}"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(req, timeout=30)
            self.assertEqual(caught.exception.code, 405)
            self.assertEqual(json.loads(caught.exception.read())["status"], "refused")
        self.assertEqual(self.hand_calls(), [])
        self.assertEqual(len(self.ledger()), 3)

    def test_an_unknown_path_is_404(self):
        self.start()
        code, out = self.post("/shell", {"cmd": "id"})
        self.assertEqual((code, out["status"]), (404, "refused"))


class DoTest(BrokerCase):
    def test_a_route_reaches_its_hand_with_the_argv_and_body_tick_sends(self):
        self.start()
        code, out = self.post("/do", {"do": "reply_x", "to": "123", "text": "hi there", "gif_id": "abc"})
        self.assertEqual(code, 200, out)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["exit"], 0)
        self.assertEqual(out["result"], {"ok": True, "dry_run": False})
        calls = self.hand_calls()
        self.assertEqual([(c["hand"], c["argv"], c["body"]) for c in calls],
                         [("x", ["reply", "--to", "123"], {"text": "hi there", "gif_id": "abc"})])

    def test_an_unknown_verb_is_refused_naming_the_allowed_ones(self):
        self.start()
        code, out = self.post("/do", {"do": "run_shell", "cmd": "id"})
        self.assertEqual((code, out["status"]), (403, "refused"))
        self.assertIn("reply_x", out["reason"])
        self.assertIn("comment_moltbook", out["reason"])
        for body in ({"text": "no verb"}, {"do": 7}):
            code, out = self.post("/do", body)
            self.assertEqual((code, out["status"]), (403, "refused"))
        self.assertEqual(self.hand_calls(), [])

    def test_the_minds_own_verbs_are_not_broker_verbs(self):
        self.start()
        for do in ("remember", "follow_up", "drop_follow_up", "feel", "journal", "skip"):
            code, out = self.post("/do", {"do": do, "text": "x"})
            self.assertEqual((code, out["status"]), (403, "refused"), do)
            self.assertIn("not a broker verb", out["reason"])
        self.assertEqual(self.hand_calls(), [])

    def test_a_routes_value_error_comes_back_refused(self):
        self.start()
        code, out = self.post("/do", {"do": "reply_x", "to": "1"})
        self.assertEqual((code, out["status"]), (403, "refused"))
        self.assertEqual(out["reason"], "needs text")
        self.assertEqual(self.hand_calls(), [])

    def test_a_capped_verb_whose_cap_is_0_is_refused_before_its_hand(self):
        self.start()  # MIND_SEARCHES_PER_DAY unset: 0, off
        code, out = self.post("/do", {"do": "web_search", "query": "teeny"})
        self.assertEqual((code, out["status"]), (403, "refused"))
        self.assertIn("MIND_SEARCHES_PER_DAY", out["reason"])
        self.assertEqual(self.hand_calls(), [])
        self.assertEqual(self.caps(), {})

    def test_a_capped_verb_is_counted_once_and_then_spent(self):
        self.start(MIND_ISSUES_PER_DAY="1")
        intent = {"do": "open_issue", "repo": "teenydroid/brain", "title": "t", "text": "b"}
        code, out = self.post("/do", intent)
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertEqual(self.caps(), {"open_issue": 1})
        code, out = self.post("/do", intent)
        self.assertEqual((code, out["status"]), (403, "refused"))
        self.assertIn("spent", out["reason"])
        self.assertEqual(self.caps(), {"open_issue": 1})
        self.assertEqual([c["hand"] for c in self.hand_calls()], ["gh"])

    def test_a_hands_refusal_and_error_keep_their_exit_codes(self):
        self.start(env={"FAKE_EXIT_X": "2", "FAKE_EXIT_MOLTBOOK": "1"})
        code, out = self.post("/do", {"do": "like_x", "id": "9"})
        self.assertEqual((code, out["status"], out["exit"]), (403, "refused", 2))
        self.assertEqual(out["result"]["refused"], "daily cap")
        code, out = self.post("/do", {"do": "follow_moltbook", "name": "bot"})
        self.assertEqual((code, out["status"], out["exit"]), (500, "error", 1))

    def test_an_own_post_on_x_is_read_cold_before_it_is_sent(self):
        unclear = json.dumps({"ok": True, "clear": False, "reason": "a stranger could not say what it means"})
        self.start(env={"FAKE_CLARITY": unclear})
        code, out = self.post("/do", {"do": "post_x", "text": "gen 7 shipped", "anchor": "pr-1"})
        self.assertEqual((code, out["status"]), (403, "refused"))
        self.assertIn("not clear to a stranger", out["reason"])
        self.assertEqual([(c["hand"], c["argv"][:1]) for c in self.hand_calls()], [("guard", ["clarity"])])

    def test_a_clear_own_post_goes_to_hands_x(self):
        self.start()
        code, out = self.post("/do", {"do": "post_x", "text": "I am a small robot", "anchor": "pr-1"})
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertEqual([(c["hand"], c["argv"]) for c in self.hand_calls()],
                         [("guard", ["clarity"]), ("x", ["post"])])

    def test_goals_runs_the_goals_hand(self):
        self.start()
        code, out = self.post("/goals")
        self.assertEqual((code, out["status"]), (200, "ok"))
        self.assertEqual(out["result"]["goals"], [{"id": 1, "title": "be famous"}])
        self.assertEqual([(c["hand"], c["argv"]) for c in self.hand_calls()], [("goals", ["list"])])


class BodyTest(BrokerCase):
    def test_a_body_over_64_kib_is_refused_unread(self):
        self.start()
        big = json.dumps({"do": "reply_x", "to": "1", "text": "a" * (64 * 1024)}).encode()
        code, out = self.post("/do", raw=big)
        self.assertEqual((code, out["status"]), (403, "refused"))
        self.assertEqual(self.hand_calls(), [])

    def test_a_body_that_is_not_a_json_object_is_refused(self):
        self.start()
        for raw in (b"do reply_x", b"[1, 2]", b"\xff\xfe"):
            code, out = self.post("/do", raw=raw)
            self.assertEqual((code, out["status"]), (403, "refused"), raw)
        self.assertEqual(self.hand_calls(), [])


class WakeTest(BrokerCase):
    def test_a_wake_is_an_agent_job_for_mind_whatever_else_the_body_says(self):
        self.start()
        body = {"name": "check-x", "note": "Look at the mentions.", "cron": "0 */2 * * *", "job_type": "shell",
                "command": "rm -rf /", "agent_id": "builder", "session_target": "main", "delivery": {"mode": "x"}}
        code, out = self.post("/wake", body)
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        (add,) = self.oh_calls("cron_add")
        self.assertEqual(add["params"], {
            "name": "mind.check-x", "job_type": "agent", "agent_id": "mind", "prompt": "Look at the mentions.",
            "session_target": "isolated", "delivery": {"mode": "none"},
            "schedule": {"kind": "cron", "expr": "0 */2 * * *", "tz": "UTC"}})
        self.assertNotIn("command", add["params"])
        self.assertTrue(all(j["job_type"] == "agent" for j in self.jobs() if j["name"].startswith("mind.")))

    def test_a_one_off_wake_is_deleted_after_its_run(self):
        self.start()
        code, out = self.post("/wake", {"name": "later", "note": "n", "at": "2026-09-14T09:30:00Z",
                                        "job_type": "shell"})
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        (add,) = self.oh_calls("cron_add")
        self.assertEqual(add["params"]["schedule"], {"kind": "at", "at": "2026-09-14T09:30:00Z"})
        self.assertIs(add["params"]["delete_after_run"], True)
        self.assertEqual(add["params"]["job_type"], "agent")

    def test_a_bad_wake_is_refused_and_nothing_is_created(self):
        self.start()
        bad = [
            {"name": "past", "note": "n", "at": "2026-09-14T06:59:00Z"},
            {"name": "far", "note": "n", "at": "2026-12-14T07:00:01Z"},
            {"name": "local", "note": "n", "at": "2026-09-14T09:30:00+05:30"},
            {"name": "garbled", "note": "n", "at": "tomorrow"},
            {"name": "fast", "note": "n", "cron": "*/2 * * * *"},
            {"name": "every", "note": "n", "cron": "* * * * *"},
            {"name": "bogus", "note": "n", "cron": "bogus"},
            {"name": "days", "note": "n", "cron": "0 9 * * mon"},
            {"name": "both", "note": "n", "cron": "0 9 * * *", "at": "2026-09-14T09:30:00Z"},
            {"name": "neither", "note": "n"},
            {"name": "empty", "note": " ", "cron": "0 9 * * *"},
            {"name": "long", "note": "n" * 2001, "cron": "0 9 * * *"},
            {"name": "Bad", "note": "n", "cron": "0 9 * * *"},
            {"name": "a.b", "note": "n", "cron": "0 9 * * *"},
            {"name": "../x", "note": "n", "cron": "0 9 * * *"},
            {"name": "a" * 33, "note": "n", "cron": "0 9 * * *"},
            {"note": "n", "cron": "0 9 * * *"},
        ]
        for body in bad:
            code, out = self.post("/wake", body)
            self.assertEqual((code, out["status"]), (403, "refused"), body)
        self.assertEqual(self.oh_calls("cron_add"), [])

    def test_a_wake_by_a_name_already_taken_is_refused(self):
        self.set_jobs([TICK_JOB, _mind_job("dup", {"kind": "cron", "expr": "0 9 * * *", "tz": "UTC"}, 1)])
        self.start()
        code, out = self.post("/wake", {"name": "dup", "note": "n", "cron": "0 10 * * *"})
        self.assertEqual((code, out["status"]), (403, "refused"))
        self.assertIn("already", out["reason"])
        self.assertEqual(self.oh_calls("cron_add"), [])

    def test_the_40th_wake_is_the_last(self):
        at = {"kind": "at", "at": "2026-09-15T07:00:00Z"}
        self.set_jobs([TICK_JOB] + [_mind_job(f"w{n}", at, n) for n in range(39)])
        self.start()
        code, out = self.post("/wake", {"name": "w39", "note": "n", "at": "2026-09-15T07:00:00Z"})
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        code, out = self.post("/wake", {"name": "w40", "note": "n", "at": "2026-09-15T07:00:00Z"})
        self.assertEqual((code, out["status"]), (403, "refused"))
        self.assertIn("40", out["reason"])
        self.assertEqual(len(self.oh_calls("cron_add")), 1)

    def test_recurring_wakes_are_held_to_the_daily_runs(self):
        half_hourly = {"kind": "cron", "expr": "*/30 * * * *", "tz": "UTC"}
        self.set_jobs([TICK_JOB, _mind_job("often", half_hourly, 1)])  # 48 a day
        self.start()
        code, out = self.post("/wake", {"name": "more", "note": "n", "cron": "0 9 * * *"})
        self.assertEqual((code, out["status"]), (403, "refused"))
        self.assertIn("MIND_WAKES_PER_DAY", out["reason"])
        self.assertEqual(self.oh_calls("cron_add"), [])

    def test_the_daily_runs_are_a_setting(self):
        half_hourly = {"kind": "cron", "expr": "*/30 * * * *", "tz": "UTC"}
        self.set_jobs([TICK_JOB, _mind_job("often", half_hourly, 1)])
        self.start(MIND_WAKES_PER_DAY="49")
        code, out = self.post("/wake", {"name": "more", "note": "n", "cron": "0 9 * * *"})
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        code, out = self.post("/wake", {"name": "again", "note": "n", "cron": "0 10 * * *"})
        self.assertEqual((code, out["status"]), (403, "refused"))

    def test_under_dry_run_a_wake_creates_nothing(self):
        self.start(DRY_RUN="1")
        code, out = self.post("/wake", {"name": "soon", "note": "n", "cron": "0 9 * * *"})
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertIs(out["dry_run"], True)
        self.assertEqual(self.oh_calls("cron_add"), [])
        self.assertEqual([j["name"] for j in self.jobs()], ["teeny.tick"])

    def test_wakes_lists_only_the_minds_jobs(self):
        daily = {"kind": "cron", "expr": "0 9 * * *", "tz": "UTC"}
        self.set_jobs([TICK_JOB, _mind_job("morning", daily, 1)])
        self.start()
        code, out = self.post("/wakes")
        self.assertEqual((code, out["status"]), (200, "ok"))
        self.assertEqual(out["wakes"], [{"name": "morning", "job": "mind.morning", "schedule": daily, "prompt": "p",
                                         "enabled": True}])


class DropWakeTest(BrokerCase):
    def test_drop_wake_removes_a_minds_job(self):
        daily = {"kind": "cron", "expr": "0 9 * * *", "tz": "UTC"}
        self.set_jobs([TICK_JOB, _mind_job("morning", daily, 1)])
        self.start()
        code, out = self.post("/drop-wake", {"name": "morning"})
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertEqual(self.oh_calls("cron_remove"), [{"method": "cron_remove", "params": {"job_id": "m-1"}}])
        self.assertEqual([j["name"] for j in self.jobs()], ["teeny.tick"])

    def test_drop_wake_never_reaches_a_job_not_the_minds(self):
        self.set_jobs([TICK_JOB, {**TICK_JOB, "id": "core-2", "name": "tick"}])
        self.start()
        for name in ("teeny.tick", "tick", "mind.tick", "../teeny.tick", "core-1", "", None):
            code, out = self.post("/drop-wake", {"name": name})
            self.assertEqual((code, out["status"]), (403, "refused"), name)
        self.assertEqual(self.oh_calls("cron_remove"), [])
        self.assertEqual(len(self.jobs()), 2)


class LedgerTest(BrokerCase):
    def test_every_request_leaves_one_line(self):
        self.start()
        self.post("/do", {"do": "reply_x", "to": "1", "text": "t" * 1000, "meta": {"k": "v" * 500}})
        self.post("/do", {"do": "nope"})
        self.post("/wake", {"name": "soon", "note": "n", "cron": "0 9 * * *"})
        self.post("/do", {"do": "like_x", "id": "1"}, token="wrong")
        lines = self.ledger()
        self.assertEqual(len(lines), 4)
        for line in lines:
            self.assertLessEqual({"ts", "path", "verb", "status", "message", "dry_run", "exit"}, set(line))
            self.assertIs(line["dry_run"], False)
        first, second, third, fourth = lines
        self.assertEqual((first["path"], first["verb"], first["status"], first["exit"]), ("/do", "reply_x", "ok", 0))
        self.assertEqual(first["intent"]["text"], "t" * 200)
        self.assertEqual(first["intent"]["to"], "1")
        self.assertLessEqual(len(json.dumps(first["intent"]["meta"])), 260)
        self.assertEqual((second["status"], second["exit"]), ("refused", None))
        self.assertIn("nope", second["message"])
        self.assertEqual((third["path"], third["verb"], third["status"]), ("/wake", "soon", "ok"))
        self.assertEqual(fourth["status"], "refused")
        self.assertTrue(first["ts"].endswith("Z"))
        self.assertNotIn(self.token, (self.root / "state" / "broker" / "ledger.jsonl").read_text())


class ClientTest(BrokerCase):
    def client(self, *argv):
        return subprocess.run([str(self.home / "bin" / "teeny"), *argv], capture_output=True, text=True, timeout=60,
                              env={"PATH": os.environ["PATH"]})

    def test_help_works_offline(self):
        self.settings()
        self.assertEqual(self.broker("install").returncode, 0)
        proc = self.client("help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for verb in ("teeny do", "teeny wake", "teeny wakes", "teeny drop-wake", "teeny goals"):
            self.assertIn(verb, proc.stdout)

    def test_do_round_trips_and_maps_statuses_to_exit_codes(self):
        self.start(env={"FAKE_EXIT_MOLTBOOK": "1"})
        proc = self.client("do", json.dumps({"do": "like_x", "id": "9"}))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["status"], "ok")
        proc = self.client("do", json.dumps({"do": "nope"}))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["status"], "refused")
        proc = self.client("do", json.dumps({"do": "follow_moltbook", "name": "bot"}))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["status"], "error")

    def test_the_other_commands_reach_their_paths(self):
        self.start()
        proc = self.client("wake", json.dumps({"name": "soon", "note": "n", "cron": "0 9 * * *"}))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        proc = self.client("wakes")
        self.assertEqual([w["name"] for w in json.loads(proc.stdout)["wakes"]], ["soon"])
        proc = self.client("drop-wake", "soon")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        proc = self.client("goals")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual([j["name"] for j in self.jobs()], ["teeny.tick"])

    def test_bad_input_and_a_broker_that_is_down_are_errors(self):
        self.settings()
        self.assertEqual(self.broker("install").returncode, 0)
        (self.home / ".broker-url").write_text("http://127.0.0.1:9\n")
        self.assertEqual(self.client("do", "not json").returncode, 1)
        self.assertEqual(self.client("frobnicate").returncode, 1)
        proc = self.client("goals")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["status"], "error")


if __name__ == "__main__":
    unittest.main()
