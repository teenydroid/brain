"""hands/gh on the command line: the shim, one JSON line, the exit codes,
and no token in anything it prints."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .support import TOKEN, Scratch

GH = Path(__file__).resolve().parents[2] / "gh"


class CliTest(Scratch):
    def run_gh(self, *argv, body=None):
        done = subprocess.run(
            [sys.executable, "-I", "-B", str(GH), *argv],
            input="" if body is None else json.dumps(body),
            capture_output=True,
            text=True,
            env=os.environ,
            timeout=60,
        )
        self.assertNotIn(TOKEN, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, f"one JSON line, got {done.stdout!r} / {done.stderr!r}")
        return done.returncode, json.loads(lines[0])

    def test_is_an_executable_python_shim(self):
        self.assertTrue(os.access(GH, os.X_OK))
        self.assertTrue(GH.read_text().startswith("#!/usr/bin/env -S python3 -I\n"))

    def test_a_read(self):
        self.me()
        code, out = self.run_gh("whoami")
        self.assertEqual((code, out["ok"], out["login"], out["matches"]), (0, True, "teenydroid", True))

    def test_a_refusal_exits_2(self):
        code, out = self.run_gh("push", body={"workdir": "secrets", "repo": "teenydroid/x", "branch": "main", "message": "m"})
        self.assertEqual((code, out["ok"], out["rule"]), (2, False, "workdir"))

    def test_a_dry_run(self):
        self.configure(DRY_RUN="1")
        code, out = self.run_gh("comment", body={"repo": "a/b", "number": 1, "body": "nice"})
        self.assertEqual((code, out["dry_run"], out["would_comment"]), (0, True, "a/b"))

    def test_errors_exit_1(self):
        self.assertEqual(self.run_gh("repo", "nope")[0], 1)
        self.assertEqual(self.run_gh("open-pr", body={"repo": "a/b"})[0], 1)
        self.assertEqual(self.run_gh()[0], 1)
        self.gh.route("GET", "/user", {"message": "Bad credentials"}, status=401)
        code, out = self.run_gh("whoami")
        self.assertEqual((code, out["error"]), (1, "GitHub answered 401: Bad credentials"))
