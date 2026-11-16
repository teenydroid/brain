"""bin/lib/detach.py: a detached job gets a temp dir of its own under state/,
so it outlives the cron call that started it.

A cron shell job runs in TinyBox's native shell, which points TMPDIR at a
per-call scratch dir and deletes it when the call returns. The detached job
carries on after that, so a TMPDIR inherited from the call dangles and every
mktemp in it fails (evolve gen 10: doctor's `mktemp` failed and the gate
rejected a good candidate)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DETACH = REPO / "bin" / "lib" / "detach.py"

CHILD = r"""
import os, subprocess, sys, tempfile, time, json
time.sleep(0.5)  # the cron call has returned and its scratch dir is gone
out = {"tmpdir": os.environ.get("TMPDIR"), "temp": os.environ.get("TEMP"), "tmp": os.environ.get("TMP")}
try:
    fd, path = tempfile.mkstemp()
    os.close(fd)
    out["mkstemp"] = path
except OSError as exc:
    out["mkstemp_error"] = str(exc)
proc = subprocess.run(["mktemp"], capture_output=True, text=True)
out["mktemp_rc"] = proc.returncode
out["mktemp"] = proc.stdout.strip() or proc.stderr.strip()
with open(sys.argv[1] + ".part", "w") as fh:
    json.dump(out, fh)
os.replace(sys.argv[1] + ".part", sys.argv[1])
"""


class DetachTmpdirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "root"
        self.root.mkdir()
        self.result = base / "result.json"
        self.pids: list[int] = []

    def tearDown(self):
        import signal

        for pid in self.pids:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        self.tmp.cleanup()

    def detach(self, scratch: Path, *flags: str) -> dict:
        """Runs detach.py the way a cron shell job does: with TMPDIR at a
        scratch dir that is deleted as soon as the call returns."""
        scratch.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, TEENY_ROOT=str(self.root), TMPDIR=str(scratch), TEMP=str(scratch), TMP=str(scratch))
        proc = subprocess.run([sys.executable, str(DETACH), "--log", "job/run.log", *flags, "--",
                               sys.executable, "-c", CHILD, str(self.result)],
                              capture_output=True, text=True, env=env, timeout=60)
        shutil.rmtree(scratch)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        if isinstance(out.get("pid"), int):
            self.pids.append(out["pid"])
        return out

    def child_result(self) -> dict:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.result.exists():
                return json.loads(self.result.read_text())
            time.sleep(0.05)
        log = self.root / "state" / "job" / "run.log"
        self.fail(f"the detached job never reported; log:\n{log.read_text() if log.exists() else ''}")

    def test_a_detached_job_can_make_temp_files_after_the_cron_scratch_dir_is_gone(self):
        scratch = Path(self.tmp.name) / "sandbox-scratch" / "shell-abc"
        out = self.detach(scratch)
        self.assertEqual(out["kind"], "started")
        got = self.child_result()
        self.assertNotIn("mkstemp_error", got)
        self.assertEqual(got["mktemp_rc"], 0, got["mktemp"])
        own = self.root / "state" / "tmp" / "job" / "run"
        self.assertEqual(got["tmpdir"], str(own))
        self.assertEqual(got["temp"], str(own))
        self.assertEqual(got["tmp"], str(own))
        self.assertEqual(Path(got["mkstemp"]).parent, own)
        self.assertEqual(Path(got["mktemp"]).parent, own)

    def test_a_locked_job_starts_with_its_temp_dir_emptied(self):
        own = self.root / "state" / "tmp" / "job" / "run"
        own.mkdir(parents=True)
        (own / "left-over").write_text("from a run that died")
        out = self.detach(Path(self.tmp.name) / "scratch", "--lock", "job")
        self.assertEqual(out["kind"], "started")
        self.child_result()
        self.assertFalse((own / "left-over").exists())

    def test_an_unlocked_job_leaves_its_temp_dir_alone(self):
        own = self.root / "state" / "tmp" / "job" / "run"
        own.mkdir(parents=True)
        (own / "in-use").write_text("a concurrent run may own this")
        self.detach(Path(self.tmp.name) / "scratch")
        self.child_result()
        self.assertTrue((own / "in-use").exists())


if __name__ == "__main__":
    unittest.main()
