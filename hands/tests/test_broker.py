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
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

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
    json.dump([j for j in jobs if str(j["id"]) != str(params["job_id"])], open(os.environ["FAKE_JOBS"], "w"))
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

    SETTINGS = {"MIND": "1", "MIND_DRY_RUN": "0", "BROKER_PORT": "0", "DRY_RUN": "0", "STUDIO": "0"}

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
            with exc:
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

    @unittest.skipUnless(Path("/proc/net/tcp").exists(), "reads the kernel's socket table")
    def test_the_daemon_listens_on_127_0_0_1_only(self):
        self.start()
        port = int(self.url.rsplit(":", 1)[1])
        listening = []
        for table in ("/proc/net/tcp", "/proc/net/tcp6"):
            if not Path(table).exists():
                continue
            for row in Path(table).read_text().splitlines()[1:]:
                local, state = row.split()[1], row.split()[3]
                addr, hex_port = local.rsplit(":", 1)
                if state == "0A" and int(hex_port, 16) == port:  # LISTEN
                    listening.append(addr)
        self.assertEqual(listening, ["0100007F"])  # 127.0.0.1, little-endian


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
            with caught.exception as answer:
                self.assertEqual(answer.code, 405)
                self.assertEqual(json.loads(answer.read())["status"], "refused")
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

    def test_an_own_post_goes_to_hands_x_as_a_beats_does(self):
        self.start()
        code, out = self.post("/do", {"do": "post_x", "text": "I am a small robot", "anchor": "pr-1"})
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertEqual([(c["hand"], c["argv"]) for c in self.hand_calls()], [("x", ["post"])])

    def test_goals_runs_the_goals_hand(self):
        self.start()
        code, out = self.post("/goals")
        self.assertEqual((code, out["status"]), (200, "ok"))
        self.assertEqual(out["result"]["goals"], [{"id": 1, "title": "be famous"}])
        self.assertEqual([(c["hand"], c["argv"]) for c in self.hand_calls()], [("goals", ["list"])])


class RehearsalTest(BrokerCase):
    """MIND_DRY_RUN: the mind rehearses on its own switch, so the rest of
    Teeny goes on posting while the operator watches what the mind would do."""

    def test_the_mind_rehearses_unless_the_operator_says_otherwise(self):
        self.start(MIND_DRY_RUN=None)  # unset: the default
        code, out = self.post("/do", {"do": "reply_x", "to": "123", "text": "hi there"})
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertEqual(out["result"], {"ok": True, "dry_run": True})
        self.assertIs(self.ledger()[-1]["dry_run"], True)

    def test_a_rehearsing_mind_schedules_nothing(self):
        self.start(MIND_DRY_RUN="1")
        code, out = self.post("/wake", {"name": "soon", "note": "n", "cron": "0 9 * * *"})
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertIs(out["dry_run"], True)
        self.assertEqual(self.oh_calls("cron_add"), [])

    def test_only_an_explicit_0_takes_the_mind_out_of_rehearsal(self):
        for value in ("", "no", "false", "2"):
            with self.subTest(value=value):
                (self.home / ".broker-url").unlink(missing_ok=True)
                self.start(MIND_DRY_RUN=value)
                _, out = self.post("/do", {"do": "reply_x", "to": "123", "text": "hi there"})
                self.assertIs(out["result"]["dry_run"], True, value)
                self.proc.terminate()
                self.proc.wait(timeout=10)
                for stream in (self.proc.stdout, self.proc.stderr):
                    stream.close()
                self.proc = None

    def test_the_boxs_own_dry_run_still_holds_a_live_mind(self):
        self.start(MIND_DRY_RUN="0", DRY_RUN="1")
        self.post("/do", {"do": "reply_x", "to": "123", "text": "hi there"})
        # The stand-in hand reads only the environment; a real one reads
        # secrets/teeny.env, as the ledger's own answer does.
        self.assertIs(self.ledger()[-1]["dry_run"], True)


class RehearsalQueuesTest(BrokerCase):
    """A rehearsal must not leave work in a queue a live worker empties."""

    def test_a_rehearsed_browser_task_is_never_queued(self):
        # hands/tasks has no dry run: a task it adds is pending, and the
        # browser operator would do it for real at its next run.
        self.start(MIND_DRY_RUN="1", MIND_TASKS_PER_DAY="3", MIND_SIGNUPS_PER_DAY="2", MIND_BUILDS_PER_DAY="1",
                   SAFETY_JUDGE="0")
        for intent in ({"do": "queue_task", "goal": "find the newest release",
                        "start_url": "https://github.com/tinyhumansai/openhuman/releases", "done_when": "named"},
                       {"do": "propose_change", "text": "move the morning post an hour earlier"}):
            code, out = self.post("/do", intent)
            self.assertEqual((code, out["status"]), (200, "ok"), out)
            self.assertIs(out["dry_run"], True)
            self.assertIn("would", out)
        self.assertEqual([c["hand"] for c in self.hand_calls() if c["hand"] in ("tasks", "build")], [])
        self.assertEqual(self.caps(), {})  # nothing was sent, so nothing is spent

    def test_a_rehearsed_post_still_reaches_its_hand_which_rehearses_it(self):
        self.start(MIND_DRY_RUN="1")
        code, out = self.post("/do", {"do": "reply_x", "to": "123", "text": "hi there"})
        self.assertEqual([c["hand"] for c in self.hand_calls()], ["x"])
        self.assertIs(out["result"]["dry_run"], True)

    def test_a_rehearsal_drops_no_wake(self):
        daily = {"kind": "cron", "expr": "0 9 * * *", "tz": "UTC"}
        self.set_jobs([TICK_JOB, _mind_job("morning", daily, 1)])
        self.start(MIND_DRY_RUN="1")
        code, out = self.post("/drop-wake", {"name": "morning"})
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertIs(out["dry_run"], True)
        self.assertEqual(self.oh_calls("cron_remove"), [])
        self.assertEqual(len(self.jobs()), 2)


class SwitchTest(BrokerCase):
    def test_enabled_follows_the_settings_parser_not_the_shape_of_the_line(self):
        for text, code in (("MIND=1\n", 0), ("MIND = 1\n", 0), ("  MIND=1\n", 0), ('MIND="1"\n', 0),
                           ("MIND=1\nMIND=0\n", 1), ("MIND=0\nMIND=1\n", 0), ("#MIND=1\n", 1), ("MIND=10\n", 1),
                           ("", 1)):
            (self.root / "secrets" / "teeny.env").write_text(text)
            self.assertEqual(self.broker("enabled").returncode, code, repr(text))

    def test_sweep_removes_every_wake_and_nothing_else(self):
        daily = {"kind": "cron", "expr": "0 9 * * *", "tz": "UTC"}
        self.set_jobs([TICK_JOB, _mind_job("morning", daily, 1), _mind_job("evening", daily, 2),
                       {**TICK_JOB, "id": "core-2", "name": "teeny.mind"}])
        self.settings(MIND="0")
        out = self.broker("sweep")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(sorted(j["name"] for j in self.jobs()), ["teeny.mind", "teeny.tick"])

    def test_sweep_leaves_the_wakes_while_the_mind_is_on(self):
        daily = {"kind": "cron", "expr": "0 9 * * *", "tz": "UTC"}
        self.set_jobs([TICK_JOB, _mind_job("morning", daily, 1)])
        self.settings(MIND="1")
        self.assertEqual(self.broker("sweep").returncode, 0)
        self.assertEqual(len(self.jobs()), 2)


class OperatorNoteTest(BrokerCase):
    def note(self, ref, text):
        return subprocess.run([str(REPO / "bin" / "broker"), "operator-note", ref], input=text, capture_output=True,
                              text=True, env=self.env, timeout=60)

    def test_the_operators_note_lands_in_the_inbox_under_its_ref(self):
        self.settings()
        out = self.note("host-1795360228-330177", "from: operator\n\ntry one claw idea\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        path = self.home / "notes" / "inbox" / "operator-host-1795360228-330177.md"
        self.assertIn("try one claw idea", path.read_text())

    def test_a_ref_that_is_not_a_host_ref_is_refused(self):
        self.settings()
        for ref in ("../../secrets/teeny", "host-1-zz", "tick-1", "", "host-1-ab/x"):
            self.assertNotEqual(self.note(ref, "x").returncode, 0, ref)
        self.assertFalse((self.home / "notes").exists() and any((self.home / "notes").rglob("*.md")))

    def test_a_linked_inbox_is_not_written_through(self):
        self.settings()
        (self.home).mkdir(parents=True)
        (self.home / "notes").symlink_to(self.root / "secrets")
        self.assertEqual(self.note("host-1-ab", "hello").returncode, 0)
        self.assertEqual(sorted(p.name for p in (self.root / "secrets").iterdir()), ["teeny.env"])


class BodyTest(BrokerCase):
    def test_a_body_over_64_kib_is_refused_without_running_anything(self):
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
            ({"name": "past", "note": "n", "at": "2026-09-14T06:59:00Z"}, "past"),
            ({"name": "far", "note": "n", "at": "2026-12-13T07:00:01Z"}, "90 days"),
            ({"name": "local", "note": "n", "at": "2026-09-14T09:30:00+05:30"}, "UTC"),
            ({"name": "garbled", "note": "n", "at": "tomorrow"}, "UTC"),
            ({"name": "fast", "note": "n", "cron": "*/2 * * * *"}, "every 5 minutes"),
            ({"name": "every", "note": "n", "cron": "* * * * *"}, "every 5 minutes"),
            ({"name": "bogus", "note": "n", "cron": "bogus"}, "five-field"),
            ({"name": "days", "note": "n", "cron": "0 9 * * mon"}, "unsupported"),
            ({"name": "hour", "note": "n", "cron": "0 25 * * *"}, "outside"),
            ({"name": "both", "note": "n", "cron": "0 9 * * *", "at": "2026-09-14T09:30:00Z"}, "not both"),
            ({"name": "neither", "note": "n"}, "not both"),
            ({"name": "empty", "note": " ", "cron": "0 9 * * *"}, "note"),
            ({"name": "long", "note": "n" * 2001, "cron": "0 9 * * *"}, "note"),
            ({"name": "Bad", "note": "n", "cron": "0 9 * * *"}, "name"),
            ({"name": "a.b", "note": "n", "cron": "0 9 * * *"}, "name"),
            ({"name": "../x", "note": "n", "cron": "0 9 * * *"}, "name"),
            ({"name": "a" * 33, "note": "n", "cron": "0 9 * * *"}, "name"),
            ({"note": "n", "cron": "0 9 * * *"}, "name"),
        ]
        for body, why in bad:
            code, out = self.post("/wake", body)
            self.assertEqual((code, out["status"]), (403, "refused"), body)
            self.assertIn(why, out["reason"], body)
        code, out = self.post("/wake", {"name": "edge", "note": "n", "at": "2026-12-13T07:00:00Z"})
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertEqual(len(self.oh_calls("cron_add")), 1, "only the wake exactly 90 days out")

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

    def test_one_off_wakes_count_against_the_days_runs_even_after_they_ran(self):
        # A one-off deletes itself once it has run, so the jobs left in the
        # core say nothing about how many ran today: without a count of its
        # own a run could schedule the next one for ever.
        self.start(MIND_WAKES_PER_DAY="3")
        for i in range(3):
            code, out = self.post("/wake", {"name": f"once-{i}", "note": "n", "at": "2026-09-14T09:30:00Z"})
            self.assertEqual((code, out["status"]), (200, "ok"), out)
            self.set_jobs([TICK_JOB])  # it ran and was deleted
        code, out = self.post("/wake", {"name": "once-more", "note": "n", "at": "2026-09-14T09:30:00Z"})
        self.assertEqual((code, out["status"]), (403, "refused"), out)
        self.assertIn("MIND_WAKES_PER_DAY", out["reason"])
        self.assertEqual(len(self.oh_calls("cron_add")), 3)

    def test_dropping_a_wake_does_not_give_its_runs_back_that_day(self):
        # Drop and re-add at another hour would otherwise run more wakes in
        # a day than MIND_WAKES_PER_DAY.
        self.start(MIND_WAKES_PER_DAY="2")
        for name, hour in (("a", 9), ("b", 10)):
            code, out = self.post("/wake", {"name": name, "note": "n", "cron": f"0 {hour} * * *"})
            self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertEqual(self.post("/drop-wake", {"name": "a"})[0], 200)
        code, out = self.post("/wake", {"name": "c", "note": "n", "cron": "0 11 * * *"})
        self.assertEqual((code, out["status"]), (403, "refused"), out)
        self.assertIn("MIND_WAKES_PER_DAY", out["reason"])

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

    def test_drop_wake_passes_a_numeric_id_as_seed_cron_does(self):
        daily = {"kind": "cron", "expr": "0 9 * * *", "tz": "UTC"}
        self.set_jobs([TICK_JOB, {**_mind_job("morning", daily, 1), "id": 42}])
        self.start()
        code, out = self.post("/drop-wake", {"name": "morning"})
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertEqual(self.oh_calls("cron_remove"), [{"method": "cron_remove", "params": {"job_id": "42"}}])
        self.assertEqual(self.jobs(), [TICK_JOB])

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



def _git(*args):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *args], env=env, check=True, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL).stdout


class MindCloneCase(BrokerCase):
    """A fake teenydroid/brain (a bare repository the broker fetches through
    GITHUB_GIT_BASE) and a fake GitHub (GITHUB_API_BASE) in this process:
    /repo and /pr run teeny_hands.build.mindclone inside the broker, with the
    real guard, its judge off."""

    PR_URL = "https://github.com/teenydroid/brain/pull/3"

    def setUp(self):
        super().setUp()
        from x.support import FakeX

        self.gh = FakeX()
        self.addCleanup(self.gh.close)
        self.gh.route("GET", "/user", {"login": "teenydroid", "id": 1})
        self.gh.route("POST", "/repos/teenydroid/brain/pulls", {"number": 3, "html_url": self.PR_URL}, status=201)
        self.origin = self.root / "remotes" / "teenydroid" / "brain.git"
        self.origin.parent.mkdir(parents=True)
        _git("init", "-q", "--bare", "-b", "main", str(self.origin))
        work = self.scratch / "upstream"
        _git("init", "-q", "-b", "main", str(work))
        for rel, text in {"evals/frozen.txt": (REPO / "evals" / "frozen.txt").read_text(),
                          "brain/SOUL.md": "# soul\nsmall and kind\n", "bin/doctor": "#!/bin/sh\n"}.items():
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_text(text)
        _git("-C", str(work), "add", "-A")
        _git("-C", str(work), "-c", "user.name=op", "-c", "user.email=op@e", "commit", "-q", "-m", "first")
        _git("-C", str(work), "push", "-q", str(self.origin), "main")
        self.main = _git("--git-dir", str(self.origin), "rev-parse", "main").strip()

    def settings(self, **more):
        super().settings(GITHUB_TOKEN="ghp_FAKEtokenFAKEtokenFAKEtoken0000", GITHUB_API_BASE=self.gh.base,
                         GITHUB_GIT_BASE="file://" + str(self.root / "remotes"), SAFETY_JUDGE="0", **more)

    @property
    def tree(self):
        return self.home / "repo"

    def branch_on_origin(self, slug):
        done = subprocess.run(["git", "--git-dir", str(self.origin), "rev-parse", "--verify", "-q",
                               f"refs/heads/teeny/mind-{slug}"], capture_output=True, text=True)
        return done.stdout.strip() or None

    def edited(self, text="# soul\nsmall, kind, brief\n"):
        code, out = self.post("/repo")
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        (self.tree / "brain" / "SOUL.md").write_text(text)

    def opened(self):
        return self.gh.requests("POST", "/repos/teenydroid/brain/pulls")

    def prs_today(self):
        path = self.root / "state" / "broker" / "prs-opened.json"
        return json.loads(path.read_text()) if path.exists() else None

    def pr(self, slug="fix-morning-post", **more):
        return self.post("/pr", {"branch": slug, "title": "a briefer soul", "body": "The old one rambled.", **more})


class RepoTest(MindCloneCase):
    def test_repo_prepares_the_clone_and_answers_its_path_and_the_upstream_head(self):
        self.start()
        code, out = self.post("/repo")
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertEqual((Path(out["path"]), out["commit"]), (self.tree, self.main))
        self.assertTrue((self.tree / "brain" / "SOUL.md").is_file())
        self.assertFalse((self.tree / ".git").exists())
        self.assertEqual(self.ledger()[-1]["path"], "/repo")
        self.assertEqual(self.ledger()[-1]["verb"], "repo")

    def test_repo_needs_the_token(self):
        self.start()
        code, _ = self.post("/repo", token="wrong")
        self.assertEqual(code, 401)
        self.assertFalse(self.tree.exists())

    def test_the_clone_is_prepared_at_start_while_pull_requests_are_on(self):
        self.start(MIND_PRS_PER_DAY="1")
        deadline = time.monotonic() + 20
        while not (self.tree / "brain" / "SOUL.md").is_file():
            self.assertLess(time.monotonic(), deadline, "the broker never prepared the clone")
            time.sleep(0.05)

    def test_no_clone_is_made_at_start_while_pull_requests_are_off(self):
        self.start()
        self.post("/goals")
        self.assertFalse(self.tree.exists())


class PullRequestTest(MindCloneCase):
    def test_pull_requests_are_off_until_the_setting_says_how_many(self):
        self.start()
        self.edited()
        code, out = self.pr()
        self.assertEqual((code, out["status"]), (403, "refused"))
        self.assertIn("MIND_PRS_PER_DAY", out["reason"])
        self.assertIsNone(self.branch_on_origin("fix-morning-post"))
        self.assertEqual(self.ledger()[-1]["verb"], "fix-morning-post")

    def test_a_change_becomes_a_pull_request_and_counts_against_the_day(self):
        self.start(MIND_PRS_PER_DAY="1")
        self.edited()
        code, out = self.pr(body="b" * 1000)
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertEqual((out["branch"], out["pr_url"]), ("teeny/mind-fix-morning-post", self.PR_URL))
        self.assertEqual(self.branch_on_origin("fix-morning-post"), out["commit"])
        (pr,) = [s.json() for s in self.opened()]
        self.assertEqual((pr["head"], pr["base"], pr["title"]),
                         ("teeny/mind-fix-morning-post", "main", "a briefer soul"))
        self.assertIn("agentic mind", pr["body"])
        self.assertEqual(self.prs_today(), {"day": "2026-09-14", "count": 1})
        line = self.ledger()[-1]
        self.assertEqual((line["path"], line["verb"], line["status"]), ("/pr", "fix-morning-post", "ok"))
        self.assertEqual(line["intent"]["body"], "b" * 200)
        self.edited("# soul\nbriefer still\n")
        code, out = self.pr("another")
        self.assertEqual((code, out["status"]), (403, "refused"))
        self.assertIn("MIND_PRS_PER_DAY", out["reason"])
        self.assertEqual(len(self.opened()), 1)

    def test_the_count_starts_again_each_utc_day(self):
        (self.root / "state" / "broker").mkdir(parents=True)
        path = self.root / "state" / "broker" / "prs-opened.json"
        path.write_text(json.dumps({"day": "2026-09-13", "count": 1}))
        self.start(MIND_PRS_PER_DAY="1")
        self.edited()
        code, out = self.pr()
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertEqual(self.prs_today(), {"day": "2026-09-14", "count": 1})

    def test_a_rule_of_the_clone_comes_back_refused(self):
        self.start(MIND_PRS_PER_DAY="2")
        self.edited()
        (self.tree / "bin" / "doctor").write_text("#!/bin/sh\necho loosened\n")
        code, out = self.pr()
        self.assertEqual((code, out["status"]), (403, "refused"))
        self.assertIn("bin/doctor (bin/**)", out["reason"])
        self.assertIsNone(self.branch_on_origin("fix-morning-post"))
        self.assertIsNone(self.prs_today())
        for body in ({"branch": "x", "title": "t", "body": "b", "base": "evil"}, {"title": "t", "body": "b"},
                     {"branch": "Bad", "title": "t", "body": "b"}):
            code, out = self.post("/pr", body)
            self.assertEqual((code, out["status"]), (403, "refused"), body)
        self.assertEqual(self.opened(), [])

    def test_a_rehearsing_mind_opens_nothing_and_spends_nothing(self):
        self.start(MIND_PRS_PER_DAY="1", MIND_DRY_RUN="1")
        self.edited()
        code, out = self.pr()
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertIs(out["dry_run"], True)
        self.assertEqual(out["files"], ["brain/SOUL.md"])
        self.assertIsNone(self.branch_on_origin("fix-morning-post"))
        self.assertEqual(self.opened(), [])
        self.assertIsNone(self.prs_today())


class CloneClientTest(MindCloneCase):
    def client(self, *argv):
        return subprocess.run([str(self.home / "bin" / "teeny"), *argv], capture_output=True, text=True, timeout=120,
                              env={"PATH": os.environ["PATH"]})

    def test_help_names_pr_and_repo(self):
        self.settings()
        self.assertEqual(self.broker("install").returncode, 0)
        out = self.client("help").stdout
        self.assertIn("teeny pr", out)
        self.assertIn("teeny repo", out)

    def test_repo_and_pr_reach_their_paths(self):
        self.start(MIND_PRS_PER_DAY="1")
        proc = self.client("repo")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["commit"], self.main)
        (self.tree / "brain" / "SOUL.md").write_text("# soul\nbrief\n")
        proc = self.client("pr", json.dumps({"branch": "brief", "title": "brief", "body": "shorter"}))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["pr_url"], self.PR_URL)
        self.assertEqual(self.client("pr").returncode, 1)
        self.assertEqual(self.client("repo", "extra").returncode, 1)


if __name__ == "__main__":
    unittest.main()
