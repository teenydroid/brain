"""hands/call on the command line, and its place on the frozen list."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .support import KEY, Scratch

REPO = Path(__file__).resolve().parents[3]
CALL = REPO / "hands" / "call"
sys.path.insert(0, str(REPO / "bin" / "lib"))

import frozen  # noqa: E402


class ShimTest(Scratch):
    def run_hand(self, *argv, body=None):
        done = subprocess.run([sys.executable, "-I", "-B", str(CALL), *argv],
                              input="" if body is None else json.dumps(body),
                              capture_output=True, text=True, env=os.environ, timeout=60)
        self.assertNotIn(KEY, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, f"one JSON line, got {done.stdout!r} / {done.stderr!r}")
        return done.returncode, json.loads(lines[0])

    def test_is_an_executable_python_shim(self):
        self.assertTrue(os.access(CALL, os.X_OK))
        self.assertTrue(CALL.read_text().startswith("#!/usr/bin/env -S python3 -I\n"))

    def test_the_shim_reads_the_shipped_list_and_refuses_what_it_does_not_name(self):
        # The real evals/services.toml (no test seam in a separate process):
        # a service it does not list, and a Moltbook write, are both refused
        # before any network.
        code, out = self.run_hand("send", body={"service": "github", "method": "GET", "path": "/user"})
        self.assertEqual((code, out["rule"]), (2, "service"), out)
        self.assertIn("moltbook", out["refused"])
        code, out = self.run_hand("send", body={"service": "moltbook", "method": "POST", "path": "/posts"})
        self.assertEqual((code, out["rule"]), (2, "allow"), out)
        code, out = self.run_hand("send")
        self.assertEqual(code, 2, out)
        code, out = self.run_hand()
        self.assertEqual(code, 1, out)


class FrozenTest(Scratch):
    def test_the_proxy_its_list_and_its_tests_are_frozen(self):
        patterns = frozen.parse_patterns((REPO / "evals" / "frozen.txt").read_text())
        for path in ("hands/call", "hands/lib/teeny_hands/call/send.py", "hands/lib/teeny_hands/call/services.py",
                     "hands/lib/teeny_hands/rest.py", "evals/services.toml", "hands/tests/call/test_guard.py",
                     "bin/lib/broker.py"):
            self.assertIsNotNone(frozen.frozen_pattern(path, patterns), path)
