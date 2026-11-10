"""The shared contract: outcomes and exit codes, secrets, dry run, state, the core."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))

from teeny_hands import hand  # noqa: E402


class Scratch(unittest.TestCase):
    """A scratch TEENY_ROOT per test, with the env cache cleared."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        for key in ("DRY_RUN", "OPENHUMAN_CORE_URL", "OPENHUMAN_CORE_TOKEN", "TEENY_NOW"):
            os.environ.pop(key, None)
        hand._ENV_CACHE = None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def secrets(self, text):
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None


class SecretsTest(Scratch):
    def test_reads_keys_quotes_and_comments(self):
        self.secrets('# keys\nX_API_KEY=abc\nexport X_API_SECRET="s e c"\nEMPTY=\n')
        self.assertEqual(hand.setting("X_API_KEY"), "abc")
        self.assertEqual(hand.setting("X_API_SECRET"), "s e c")
        self.assertEqual(hand.setting("EMPTY", "fallback"), "fallback")

    def test_environment_does_not_override_a_secret(self):
        self.secrets("X_API_KEY=from-file\n")
        os.environ["X_API_KEY"] = "from-env"
        self.assertEqual(hand.setting("X_API_KEY"), "from-file")

    def test_require_names_the_missing_key(self):
        with self.assertRaisesRegex(hand.HandError, "X_BEARER_TOKEN"):
            hand.require("X_BEARER_TOKEN")


class DryRunTest(Scratch):
    def test_on_by_default(self):
        self.assertTrue(hand.dry_run())

    def test_off_only_from_the_file(self):
        self.secrets("DRY_RUN=0\n")
        self.assertFalse(hand.dry_run())

    def test_environment_can_force_it_on_but_never_off(self):
        self.secrets("DRY_RUN=0\n")
        os.environ["DRY_RUN"] = "1"
        self.assertTrue(hand.dry_run())
        self.secrets("DRY_RUN=1\n")
        os.environ["DRY_RUN"] = "0"
        self.assertTrue(hand.dry_run())


class StateTest(Scratch):
    def setUp(self):
        super().setUp()
        (self.root / "state").mkdir(exist_ok=True)

    def test_transaction_rolls_back_on_error(self):
        conn = hand.db()
        conn.execute("CREATE TABLE t (v INTEGER)")
        with self.assertRaises(RuntimeError):
            with hand.transaction(conn):
                conn.execute("INSERT INTO t VALUES (1)")
                raise RuntimeError("boom")
        self.assertEqual(conn.execute("SELECT count(*) FROM t").fetchone()[0], 0)

    def test_lock_times_out_while_held(self):
        with hand.lock("posting"):
            done = {}

            def other():
                try:
                    with hand.lock("posting", timeout=0.5):
                        done["got"] = True
                except hand.HandError:
                    done["timed_out"] = True

            # flock is per open file description: a second open in another
            # thread contends like another process would.
            t = threading.Thread(target=other)
            t.start()
            t.join()
        self.assertEqual(done, {"timed_out": True})

    def test_many_processes_can_open_a_fresh_database_at_once(self):
        # Switching a brand-new file to WAL takes an exclusive lock the busy
        # timeout does not cover; without a retry the losers fail outright.
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "from teeny_hands import hand\n"
            "c = hand.db(); c.execute('CREATE TABLE IF NOT EXISTS t (v)'); print('ok')\n"
        ) % str(HERE.parent / "lib")
        for round_ in range(5):
            (self.root / "state" / "hands.db").unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                (self.root / "state" / f"hands.db{suffix}").unlink(missing_ok=True)
            procs = [
                subprocess.Popen([sys.executable, "-I", "-c", script], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env=os.environ)
                for _ in range(12)
            ]
            outs = [p.communicate() for p in procs]
            failed = [err for (out, err), p in zip(outs, procs) if p.returncode != 0]
            self.assertEqual(failed, [], f"round {round_}: {failed[:1]}")

    def test_now_can_be_pinned(self):
        os.environ["TEENY_NOW"] = "1000"
        self.assertEqual(hand.now(), 1000.0)


class CoreTest(Scratch):
    def serve(self, reply):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                calls.append(
                    (self.headers.get("authorization"), json.loads(self.rfile.read(int(self.headers["content-length"]))))
                )
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        (self.root / "state").mkdir(exist_ok=True)
        (self.root / "state" / "core.url").write_text(f"http://127.0.0.1:{server.server_port}\n")
        (self.root / "secrets" / "core.token").write_text("tok\n")
        return calls

    def test_unwraps_the_controller_envelope_and_sends_the_token(self):
        calls = self.serve({"jsonrpc": "2.0", "id": 1, "result": {"logs": ["x"], "result": {"a": 1}}})
        self.assertEqual(hand.core("cron_list"), {"a": 1})
        auth, body = calls[0]
        self.assertEqual(auth, "Bearer tok")
        self.assertEqual(body["method"], "openhuman.cron_list")

    def test_an_rpc_error_raises(self):
        self.serve({"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "nope"}})
        with self.assertRaisesRegex(hand.CoreError, "nope"):
            hand.core("cron_list")

    def test_unreachable_core_raises(self):
        (self.root / "state").mkdir(exist_ok=True)
        (self.root / "state" / "core.url").write_text("http://127.0.0.1:9\n")
        (self.root / "secrets" / "core.token").write_text("tok")
        with self.assertRaisesRegex(hand.CoreError, "unreachable"):
            hand.core("cron_list", timeout=2)


class RunContractTest(Scratch):
    """The exit codes and JSON a caller relies on, through a real subprocess."""

    def run_hand(self, body, *argv, stdin=None):
        script = self.root / "fake-hand"
        script.write_text(
            "import argparse, sys\n"
            f"sys.path.insert(0, {str(HERE.parent / 'lib')!r})\n"
            "from teeny_hands import hand\n"
            f"{body}\n"
            "p = argparse.ArgumentParser(prog='fake')\n"
            "sub = p.add_subparsers()\n"
            "v = sub.add_parser('go'); v.set_defaults(handler=go, takes_body=True)\n"
            "sys.exit(hand.run('fake', p, sys.argv[1:]))\n"
        )
        # No body is an empty stdin, never the test runner's own: inherited, a
        # stdin that is an open pipe or socket (a CI runner, an agent's shell)
        # blocks the hand's read forever.
        done = subprocess.run(
            [sys.executable, "-I", str(script), *argv],
            input="" if stdin is None else stdin,
            capture_output=True,
            text=True,
            env=os.environ,
            timeout=60,
        )
        return done.returncode, json.loads(done.stdout)

    def test_ok(self):
        code, out = self.run_hand("def go(a, body): return {'echo': body}", "go", stdin='{"x": 1}')
        self.assertEqual((code, out), (0, {"ok": True, "echo": {"x": 1}}))

    def test_refused_is_exit_2_with_the_reason(self):
        code, out = self.run_hand("def go(a, body): raise hand.Refused('gap', next_at=5)", "go")
        self.assertEqual((code, out), (2, {"ok": False, "refused": "gap", "next_at": 5}))

    def test_an_error_is_exit_1_and_one_json_line(self):
        code, out = self.run_hand("def go(a, body): raise ValueError('bad')", "go")
        self.assertEqual(code, 1)
        self.assertEqual(out, {"ok": False, "error": "ValueError: bad"})

    def test_no_verb_is_an_error(self):
        code, out = self.run_hand("def go(a, body): return {}")
        self.assertEqual(code, 1)
        self.assertFalse(out["ok"])

    def test_bad_json_on_stdin_is_an_error(self):
        code, out = self.run_hand("def go(a, body): return {}", "go", stdin="{nope")
        self.assertEqual(code, 1)
        self.assertIn("not JSON", out["error"])


if __name__ == "__main__":
    unittest.main()
