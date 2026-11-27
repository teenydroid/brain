"""hands/trend on the command line: the shim, one JSON line, the exit codes."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

from .support import Scratch, cluster, post, row
from teeny_hands.trend import cli, digg

TREND = Path(__file__).resolve().parents[2] / "trend"


class CliTest(Scratch):
    def run_hand(self, *argv):
        done = subprocess.run([sys.executable, "-I", "-B", str(TREND), *argv], input="", capture_output=True,
                              text=True, env=os.environ, timeout=60)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, f"one JSON line, got {done.stdout!r} / {done.stderr!r}")
        return done.returncode, json.loads(lines[0])

    def run_main(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        lines = buf.getvalue().splitlines()
        self.assertEqual(len(lines), 1, f"one JSON line, got {buf.getvalue()!r}")
        return code, json.loads(lines[0])

    def test_is_an_executable_python_shim(self):
        self.assertTrue(os.access(TREND, os.X_OK))
        self.assertTrue(TREND.read_text().startswith("#!/usr/bin/env -S python3 -I\n"))

    def test_top_and_story(self):
        self.board("ai", row(1, "ai", 50))
        self.board("tech", row(2, "tech", 40))
        self.sources(1, post("Ana", "hello"))
        code, out = self.run_main("top", "--slot", "2026-11-18:noon", "--leading", "1")
        self.assertEqual((code, out["ok"], [s["id"] for s in out["stories"]]), (0, True, ["s1", "s2"]))
        self.assertEqual(out["stories"][0]["posts"][0]["text"], "hello")
        code, out = self.run_main("top", "--slot", "2026-11-18:noon")
        self.assertEqual((code, out["cached"]), (0, True))
        code, out = self.run_main("story", cluster(1))
        self.assertEqual((code, out["posts"][0]["by"]), (0, "Ana"))

    def test_a_failure_is_ok_false_with_a_reason_exit_1(self):
        self.behave_keys({f"top:{t}": "rate" for t in digg.TOPICS})
        code, out = self.run_main("top")
        self.assertEqual((code, out["ok"], out["reason"]), (1, False, "rate_limited"))
        self.assertEqual(self.run_hand("top", "--limit", "99")[0], 1)
        self.assertEqual(self.run_hand()[0], 1)

    def test_a_base_that_is_not_https_is_refused_before_any_request(self):
        # In the real shim, so no mock is in play: a refused base makes no request at all.
        self.configure(TREND_DIGG_BASE="http://digg.example")
        code, out = self.run_hand("top")
        self.assertEqual((code, out["ok"], out["reason"]), (1, False, "failed"))
        self.assertIn("https", out["error"])
