"""The ship step's jail (teeny_hands.build.verify) with an interpreter that
lives outside the system roots, as on a CI runner (/opt/hostedtoolcache) or
in a virtualenv: the jail still lets that interpreter start, and never opens
more than the interpreter's own install."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from teeny_hands.build import verify  # noqa: E402

PASSING = "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n"
JAILED = verify.landlock_abi() > 0


class InterpreterOutsideTheRootsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="verify-interp-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def venv_python(self) -> str:
        """A copied interpreter under a scratch folder that no READ_ROOT covers."""
        env = self.tmp / "py"
        subprocess.run([sys.executable, "-m", "venv", "--copies", "--without-pip", str(env)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        python = env / "bin" / "python3"
        self.assertTrue(python.exists())
        self.assertFalse(any(str(python).startswith(root + "/") for root in verify.READ_ROOTS))
        return str(python)

    @unittest.skipUnless(JAILED, "this kernel has no Landlock")
    def test_a_job_tests_run_with_an_interpreter_installed_outside_the_system_roots(self):
        python = self.venv_python()
        job = self.tmp / "job"
        job.mkdir()
        (job / "test_ok.py").write_text(PASSING)
        with mock.patch.object(verify.sys, "executable", python), \
                mock.patch.object(verify.sys, "prefix", str(Path(python).parents[1])):
            out = verify.run(job)
        self.assertTrue(out["jailed"], out)
        self.assertEqual(out["ran"], 1, out)
        self.assertTrue(out["ok"], out)

    def test_the_interpreter_roots_never_open_a_parent_of_the_job_or_a_shallow_folder(self):
        job = self.tmp / "install" / "state" / "job"
        job.mkdir(parents=True)
        (self.tmp / "install" / "lib" / "python3.11").mkdir(parents=True)
        (self.tmp / "install" / "bin").mkdir()
        fake = self.tmp / "install" / "bin" / "python3"
        fake.write_text("")
        with mock.patch.object(verify.sys, "executable", str(fake)), \
                mock.patch.object(verify.sys, "prefix", "/"), mock.patch.object(verify.sys, "base_prefix", "/opt"), \
                mock.patch.object(verify.sys, "exec_prefix", "/"), mock.patch.object(verify.sys, "base_exec_prefix", "/"):
            roots = verify.interpreter_roots((str(job),))
        self.assertNotIn("/", roots)
        self.assertNotIn("/opt", roots)
        self.assertNotIn(str(self.tmp / "install"), roots)  # holds the job: never readable whole
        with mock.patch.object(verify.sys, "executable", str(fake)):
            roots = verify.interpreter_roots((str(self.tmp / "elsewhere"),))
        self.assertIn(os.path.realpath(self.tmp / "install"), roots)


if __name__ == "__main__":
    unittest.main()
