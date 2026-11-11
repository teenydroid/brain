"""hands/slopcash on the command line: the shim, one JSON line, the exit codes,
the sign-in link on stderr, and no sign-in secret in anything it prints."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .support import AUTHORIZATION_URL, PROJECT, Scratch, manifest

SLOPCASH = Path(__file__).resolve().parents[2] / "slopcash"


class CliTest(Scratch):
    def run_hand(self, *argv):
        done = subprocess.run(
            [sys.executable, "-I", "-B", str(SLOPCASH), *argv],
            input="", capture_output=True, text=True, env=os.environ, timeout=60,
        )  # fmt: skip
        self.assert_no_secret(done.stdout, done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, f"one JSON line, got {done.stdout!r} / {done.stderr!r}")
        return done.returncode, json.loads(lines[0]), done.stderr

    def edit(self, change: dict) -> str:
        path = self.root / "edit.json"
        path.write_text(json.dumps(change))
        return str(path)

    def test_is_an_executable_python_shim(self):
        self.assertTrue(os.access(SLOPCASH, os.X_OK))
        self.assertTrue(SLOPCASH.read_text().startswith("#!/usr/bin/env -S python3 -I\n"))

    def test_reads_exit_0(self):
        self.a_project()
        code, out, _ = self.run_hand("project")
        self.assertEqual((code, out["ok"], out["status"]), (0, True, "paused"))
        code, out, _ = self.run_hand("job", PROJECT)
        self.assertEqual((code, out["headline"]), (0, manifest()["headline"]))

    def test_post_job_confirm_shows_only_the_link_and_prints_one_answer(self):
        self.signin()
        self.a_project()
        self.slop.route("PUT", f"/api/v1/projects/{PROJECT}", {"project": {"revision": 4, "status": "paused"},
                        "changes": {"presentation": ["headline"], "executable": [], "financial": []}, "setup": {}})  # fmt: skip
        code, out, err = self.run_hand("post-job", "--file", self.edit({"headline": "x"}), "--reason", "shorter", "--confirm")
        self.assertEqual((code, out["posted"], out["revision"]), (0, PROJECT, 4), err)
        self.assertIn(AUTHORIZATION_URL, err)
        self.assertEqual(self.leaked(), [])

    def test_the_preview_and_check_say_what_they_would_do(self):
        self.a_project()
        code, out, _ = self.run_hand("post-job", "--file", self.edit({"headline": "x"}))
        self.assertEqual((code, out["preview"]), (0, True))
        code, out, _ = self.run_hand("check", "--scan")
        self.assertEqual((code, out["would_check"], out["request_scan"]), (0, PROJECT, True))
        self.assertEqual(self.idp.seen, [])

    def test_a_refusal_exits_2_and_errors_exit_1(self):
        code, out, _ = self.run_hand("post-job", "--file", self.edit({"status": "active"}), "--confirm")
        self.assertEqual((code, out["refused"], out["fields"]), (2, "owner_field_restricted", ["status"]))
        self.assertEqual(self.run_hand()[0], 1)
        self.assertEqual(self.run_hand("submissions", "--state", "merged")[0], 1)
        self.assertEqual(self.run_hand("submission", "0")[0], 1)
        self.signin()
        self.a_project()
        self.slop.route("PUT", f"/api/v1/projects/{PROJECT}", {"error": "stale_revision", "message": "changed"}, status=409)
        code, out, _ = self.run_hand("post-job", "--file", self.edit({"headline": "x"}), "--confirm")
        self.assertEqual(code, 1)
        self.assertIn("stale_revision", out["error"])
