"""bin/lib/studio_switch.py: the one switch between the studio and the old
posts, which fails closed (review of PR #379): only on and off words are
read, a file that is not text is an error, the environment wins when it sets
STUDIO at all, and the command line answers by exit code so a crash is never
read as "no"."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))
import studio_switch  # noqa: E402


class SwitchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.file = self.dir / "teeny.env"

    def write(self, data: bytes) -> None:
        self.file.write_bytes(data)

    def test_only_on_and_off_words_are_read(self):
        for word in ("1", "true", "YES", " on "):
            self.assertTrue(studio_switch.on({"STUDIO": word}, self.file), word)
        for word in ("0", "false", "No", "off", ""):
            self.assertFalse(studio_switch.on({"STUDIO": word}, self.file), word)
        for word in ("maybe", "2", "onn", "of"):
            with self.assertRaises(studio_switch.SwitchError, msg=word):
                studio_switch.on({"STUDIO": word}, self.file)

    def test_a_typo_in_the_file_is_an_error_not_off(self):
        self.write(b"STUDIO=maybe\n")
        with self.assertRaises(studio_switch.SwitchError):
            studio_switch.on({}, self.file)

    def test_a_file_that_is_not_utf8_is_an_error(self):
        self.write(b"STUDIO=1\nX=\xff\xfe\n")
        with self.assertRaises(studio_switch.SwitchError) as caught:
            studio_switch.on({}, self.file)
        self.assertIn("not UTF-8", str(caught.exception))

    def test_unset_is_off_and_a_missing_file_is_off(self):
        self.assertEqual(studio_switch.read({}, self.dir / "none.env"), (False, "unset"))
        self.write(b"OTHER=1\n")
        self.assertFalse(studio_switch.on({}, self.file))

    def test_the_environment_wins_when_it_sets_studio_even_empty(self):
        self.write(b"STUDIO=1\n")
        self.assertEqual(studio_switch.read({"STUDIO": "0"}, self.file), (False, "the environment"))
        self.assertEqual(studio_switch.read({"STUDIO": ""}, self.file), (False, "the environment"))
        self.assertTrue(studio_switch.on({}, self.file))
        self.write(b"STUDIO=0\n")
        self.assertTrue(studio_switch.on({"STUDIO": "1"}, self.file))

    def cli(self, *argv: str, env: dict | None = None) -> subprocess.CompletedProcess:
        environ = {k: v for k, v in os.environ.items() if k != "STUDIO"} | {"TEENY_SECRETS": str(self.dir)} | (env or {})
        return subprocess.run([sys.executable, str(REPO / "bin" / "lib" / "studio_switch.py"), *argv],
                              capture_output=True, text=True, env=environ)

    def test_the_command_line_answers_by_exit_code(self):
        self.write(b"STUDIO=1\n")
        self.assertEqual(self.cli("replaced", "take").returncode, 0)
        self.assertEqual(self.cli("replaced", "brief").returncode, studio_switch.NO)
        self.assertEqual((self.cli("veto", "brief").returncode, self.cli("veto", "brief").stdout.strip()), (0, "brief"))
        self.write(b"STUDIO=0\n")
        self.assertEqual(self.cli("replaced", "take").returncode, studio_switch.NO)
        self.assertEqual(self.cli("veto", "brief").returncode, studio_switch.NO)
        self.assertEqual((self.cli("on").returncode, self.cli("on").stdout.strip()), (studio_switch.NO, "0"))

    def test_a_switch_that_cannot_be_read_is_neither_yes_nor_no(self):
        self.write(b"STUDIO=\xff\n")
        for argv in (("replaced", "take"), ("veto", "brief"), ("on",)):
            got = self.cli(*argv)
            self.assertEqual(got.returncode, studio_switch.ERROR, argv)
            self.assertIn("not UTF-8", got.stderr)
        self.write(b"STUDIO=maybe\n")
        self.assertEqual(self.cli("replaced", "take").returncode, studio_switch.ERROR)

    def test_the_doctor_fails_an_unreadable_switch_and_warns_on_judges_that_are_the_writer(self):
        self.write(b"STUDIO=maybe\n")
        self.assertEqual(studio_switch.doctor({"TEENY_SECRETS": str(self.dir)})[0], "fail")
        self.write(b"STUDIO=1\nSTUDIO_WRITER_MODEL=w\nSAFETY_JUDGE_MODEL=w\n")
        self.assertEqual(studio_switch.doctor({"TEENY_SECRETS": str(self.dir)})[0], "warn")
        self.write(b"STUDIO=1\n")
        self.assertEqual(studio_switch.doctor({"TEENY_SECRETS": str(self.dir)})[0], "warn")
        self.write(b"STUDIO=1\nSTUDIO_WRITER_MODEL=w\nSAFETY_JUDGE_MODEL=j\n")
        self.assertEqual(studio_switch.doctor({"TEENY_SECRETS": str(self.dir)})[0], "ok")
        self.write(b"STUDIO=0\n")
        self.assertEqual(studio_switch.doctor({"TEENY_SECRETS": str(self.dir)})[0], "ok")
        self.assertEqual(self.cli("doctor").stdout.split()[0], "ok")

    def test_models_are_what_the_studio_asks_each_on_its_route(self):
        env = {"TEENY_SECRETS": str(self.dir)}
        self.write(b"STUDIO=0\n")
        self.assertEqual(studio_switch.models(env), [])
        self.write(b"STUDIO_WRITER_MODEL=w\nSTUDIO_EDITOR_MODEL=e\nSAFETY_JUDGE_MODEL=e\n")
        self.assertEqual(studio_switch.models(env), [("agent", "w"), ("agent", "e"), ("simple", "e")])
        # The writer and editor: the environment first; the judges: the file only.
        self.assertEqual(studio_switch.models({**env, "STUDIO_WRITER_MODEL": "e", "SAFETY_JUDGE_MODEL": "x"}),
                         [("agent", "e"), ("simple", "e")])
        self.assertEqual(self.cli("models").stdout.splitlines(), ["agent w", "agent e", "simple e"])


if __name__ == "__main__":
    unittest.main()
