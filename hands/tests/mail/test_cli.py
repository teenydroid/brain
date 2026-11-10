"""hands/mail on the command line: the shim, one JSON line, the exit codes,
and no key in anything it prints."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .support import KEY, Scratch, message

MAIL = Path(__file__).resolve().parents[2] / "mail"


class CliTest(Scratch):
    def run_hand(self, *argv, body=None):
        done = subprocess.run(
            [sys.executable, "-I", "-B", str(MAIL), *argv],
            input="" if body is None else json.dumps(body),
            capture_output=True, text=True, env=os.environ, timeout=60,
        )  # fmt: skip
        self.assertNotIn(KEY, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, f"one JSON line, got {done.stdout!r} / {done.stderr!r}")
        return done.returncode, json.loads(lines[0])

    def test_is_an_executable_python_shim(self):
        self.assertTrue(os.access(MAIL, os.X_OK))
        self.assertTrue(MAIL.read_text().startswith("#!/usr/bin/env -S python3 -I\n"))

    def test_reads_refusals_and_errors(self):
        self.inbox(message("<m1@x>"))
        code, out = self.run_hand("get", "<m1@x>")
        self.assertEqual((code, out["message"]["subject"]), (0, "hello"))
        code, out = self.run_hand("send", body={"to": "x@y.example", "subject": "s", "body": "b"})
        self.assertEqual((code, out["rule"]), (2, "contacts"))
        self.assertEqual(self.run_hand()[0], 1)
        self.assertEqual(self.run_hand("reply", body={"message_id": "<m1@x>"})[0], 1)

    def test_contacts_and_mark(self):
        code, out = self.run_hand("contacts", "add", "a@b.example", "--kind", "vendor", "--note", "stickers")
        self.assertEqual((code, out["contact"]), (0, "a@b.example"))
        self.assertEqual(self.run_hand("contacts", "add", "a@b.example")[0], 1)
        self.assertEqual(self.run_hand("contacts", "list")[1]["contacts"][0]["email"], "a@b.example")
        self.assertEqual(self.run_hand("mark", "<nope@x>", "--status", "replied")[0], 2)
        self.assertEqual(self.run_hand("requests")[1], {"ok": True, "requests": []})
