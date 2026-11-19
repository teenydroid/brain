"""The feature rubric (teeny_hands.build.feature): what a self job that is a
feature (a want, build/jobs.py) must hold before the desk accepts "ok" -- a
new hand, run through hand.run, with passing tests, a scheduled playbook that
calls it, and a section in docs/hands.md. Pure file reads over a tree; the
test run is handed in, so nothing here needs the jail."""

from __future__ import annotations

import os
import shutil
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from teeny_hands.build import feature, selfclone  # noqa: E402

TESTS_PASS = {"ok": True, "jailed": True, "ran": 2, "output": "Ran 2 tests in 0.01s\n\nOK"}
TESTS_FAIL = {"ok": False, "jailed": True, "ran": 1, "output": "FAIL: test_it\n" + "x" * 600 + "\nAssertionError: 1 != 2"}

PLAYBOOK = '''+++
description = "look at the gizmo"
output = "post"

[[inputs]]
name = "gizmo"
run = "hands/gizmo look --max 3"
+++
Say what the gizmo shows.
'''

CRON = '''# the jobs
[jobs.gizmo-look]
schedule = "0 9 * * *"
tz = "UTC"
command = "bin/act gizmo-look"
'''

ENTRY = """#!/usr/bin/env -S python3 -I
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib"))
from teeny_hands.{name}.cli import main
sys.exit(main(sys.argv[1:]))
"""

ASSERTING_TEST = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_it(self):\n        self.assertEqual(1, 1)\n"


class Tree(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="feature-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tree = Path(self.tmp)

    def write(self, rel: str, text: str = "", mode: int = 0o644) -> None:
        path = self.tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)

    def complete(self, name: str = "gizmo") -> list[tuple[str, str]]:
        """A whole feature in the tree, and the changes that made it."""
        self.write(f"hands/{name}", ENTRY.format(name=name), 0o755)
        self.write(f"hands/lib/teeny_hands/{name}/__init__.py")
        self.write(f"hands/lib/teeny_hands/{name}/cli.py",
                   f"from .. import hand\n\n\ndef main(argv):\n    return hand.run('{name}', parser(), argv)\n")
        self.write(f"hands/tests/test_{name}.py", ASSERTING_TEST)
        self.write("brain/playbooks/gizmo-look.md", PLAYBOOK.replace("hands/gizmo", f"hands/{name}"))
        self.write("brain/cron.toml", CRON)
        self.write("docs/hands.md", f"# Hands\n\n## `{name}`: looks at things\n\nIt looks.\n\n## `other`: another\n")
        return [("A", f"hands/{name}"), ("A", f"hands/lib/teeny_hands/{name}/cli.py"),
                ("A", f"hands/tests/test_{name}.py"), ("A", "brain/playbooks/gizmo-look.md"),
                ("M", "brain/cron.toml"), ("M", "docs/hands.md")]


class NewHandsTest(unittest.TestCase):
    def test_new_hands_are_only_hands_absent_from_main(self):
        changed = [
            ("A", "hands/gizmo"),                                # a new entry point
            ("??", "hands/widget"),                              # an untracked one
            ("M", "hands/build"),                                # an existing hand, edited
            ("D", "hands/old"),
            ("R", "hands/renamed"),
            ("A", "hands/lib/teeny_hands/gizmo/cli.py"),         # its package, not an entry
            ("A", "hands/tests/test_gizmo.py"),
            ("A", "hands/README.md"),                            # not a hand's name
            ("A", "docs/hands/gizmo"),
            ("A", "brain/playbooks/hands"),
        ]
        self.assertEqual(feature.new_hands(changed), ["gizmo", "widget"])
        self.assertEqual(feature.new_hands([]), [])


class CallersTest(Tree):
    def test_callers_read_inputs_and_outputs_from_front_matter(self):
        self.write("brain/playbooks/looks.md", PLAYBOOK)
        self.write("brain/playbooks/speaks.md", '+++\ndescription = "x"\noutput = "hand:gizmo say"\n+++\nSay it.\n')
        self.write("brain/playbooks/prose-only.md", '+++\ndescription = "x"\noutput = "post"\n+++\nUse hands/gizmo to look.\n')
        self.write("brain/playbooks/other-hand.md",
                   '+++\ndescription = "x"\noutput = "hand:gizmos say"\n\n[[inputs]]\nname = "a"\n'
                   'run = "hands/gizmo2 look"\n+++\nno\n')
        self.write("brain/playbooks/broken.md", "+++\nthis is = = not toml\n+++\nbody\n")
        self.write("brain/playbooks/no-front-matter.md", "hands/gizmo look\n")
        self.assertEqual(feature.callers(self.tree, "gizmo"), ["looks", "speaks"])
        self.assertEqual(feature.callers(self.tree, "nobody"), [])
        self.assertEqual(feature.callers(Path(self.tmp) / "missing", "gizmo"), [])

    def test_an_optional_input_alone_is_not_a_caller(self):
        optional = PLAYBOOK.replace('run = "hands/gizmo look --max 3"', 'run = "hands/gizmo look"\noptional = true')
        self.write("brain/playbooks/maybe.md", optional)
        self.assertEqual(feature.callers(self.tree, "gizmo"), [])
        # a required input beside it, or the hand as the output, makes it one
        self.write("brain/playbooks/also-required.md",
                   '+++\ndescription = "x"\noutput = "post"\n\n[[inputs]]\nname = "a"\n'
                   'run = "hands/gizmo look"\noptional = true\n\n[[inputs]]\nname = "b"\n'
                   'run = "hands/gizmo look"\n+++\nSay.\n')
        self.write("brain/playbooks/speaks-optionally.md",
                   '+++\ndescription = "x"\noutput = "hand:gizmo say"\n\n[[inputs]]\nname = "a"\n'
                   'run = "hands/gizmo look"\noptional = true\n+++\nSay.\n')
        self.assertEqual(feature.callers(self.tree, "gizmo"), ["also-required", "speaks-optionally"])


class ScheduledTest(Tree):
    def test_scheduled_needs_bin_act_in_cron(self):
        self.write("brain/cron.toml", CRON + '''
[jobs.by-hand]
schedule = "0 9 * * *"
command = "bin/studio --slot 0930 gizmo-spoken"

[jobs.paused]
schedule = "0 9 * * *"
command = "bin/act gizmo-paused"
enabled = false
''')
        books = ["gizmo-look", "gizmo-spoken", "gizmo-paused", "gizmo-unscheduled"]
        self.assertEqual(feature.scheduled(self.tree, books), ["gizmo-look"])
        self.assertEqual(feature.scheduled(self.tree, []), [])
        self.assertEqual(feature.scheduled(Path(self.tmp) / "missing", books), [])


    def test_a_job_that_is_not_a_table_is_skipped_not_fatal(self):
        self.write("brain/cron.toml", CRON + "\n[other]\nx = 1\n")
        self.assertEqual(feature.scheduled(self.tree, ["gizmo-look"]), ["gizmo-look"])
        self.write("brain/cron.toml", "[jobs]\nfoo = 1\n")
        self.assertEqual(feature.scheduled(self.tree, ["gizmo-look"]), [])
        self.write("brain/cron.toml", '[jobs]\nfoo = 1\nbar = "bin/act gizmo-look"\n\n'
                   '[jobs.gizmo-look]\nschedule = "0 9 * * *"\ncommand = "bin/act gizmo-look"\n')
        self.assertEqual(feature.scheduled(self.tree, ["gizmo-look"]), ["gizmo-look"])


class HandRunTest(Tree):
    def runs(self, source: str, name: str = "gizmo") -> bool:
        self.write(f"hands/lib/teeny_hands/{name}/cli.py", source)
        return feature.runs_through_hand_run(self.tree / "hands" / "lib" / "teeny_hands" / name)[0]

    def test_a_real_call_of_hand_run_counts_in_each_import_form(self):
        for source in (
            "from .. import hand\n\ndef main(a):\n    return hand.run('g', None, a)\n",
            "from teeny_hands import hand\n\ndef main(a):\n    return hand.run('g', None, a)\n",
            "from teeny_hands import hand as h\n\ndef main(a):\n    return h.run('g', None, a)\n",
            "from teeny_hands.hand import run\n\ndef main(a):\n    return run('g', None, a)\n",
            "from ..hand import run as go\n\ndef main(a):\n    return go('g', None, a)\n",
            "import teeny_hands.hand\n\ndef main(a):\n    return teeny_hands.hand.run('g', None, a)\n",
            "import teeny_hands.hand as hand\n\ndef main(a):\n    return hand.run('g', None, a)\n",
        ):
            with self.subTest(source=source):
                self.assertTrue(self.runs(source))

    def test_a_mention_of_hand_run_is_not_a_call(self):
        for source in (
            "# hand.run(\ndef main(a):\n    return 0\n",
            '"""Goes through hand.run(name, parser, argv)."""\ndef main(a):\n    return 0\n',
            "def main(a):\n    s = 'hand.run('\n    return 0\n",
            "from .. import hand\n\ndef main(a):\n    return hand.runner(a)\n",   # another attribute
            "from .. import hand\n\ndef main(a):\n    return hand.run\n",         # not called
            "def main(a, hand):\n    return hand.run('g', None, a)\n",             # a parameter, not the module
            "from other import hand\n\ndef main(a):\n    return hand.run('g', None, a)\n",
            "from other import run\n\ndef main(a):\n    return run('g', None, a)\n",
        ):
            with self.subTest(source=source):
                self.assertFalse(self.runs(source))

    def test_source_that_does_not_parse_fails_and_is_named(self):
        self.write("hands/lib/teeny_hands/gizmo/cli.py", "from .. import hand\nhand.run(\n")
        ok, broken = feature.runs_through_hand_run(self.tree / "hands" / "lib" / "teeny_hands" / "gizmo")
        self.assertFalse(ok)
        self.assertEqual(broken, ["cli.py"])

    def test_a_missing_package_does_not_run_through_it(self):
        self.assertEqual(feature.runs_through_hand_run(self.tree / "nope"), (False, []))


class RenamesTest(unittest.TestCase):
    """selfclone.changed_paths feeds new_hands: a new hands/<name> must stay an
    add when the branch also deleted a file, which git would call a rename."""

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                       cwd=self.repo, check=True, capture_output=True)

    def test_a_new_hand_beside_a_deleted_file_is_not_hidden_by_a_rename(self):
        self.tmp = tempfile.mkdtemp(prefix="renames-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = Path(self.tmp)
        self.git("init", "-q", "-b", "main")
        (self.repo / "hands").mkdir()
        body = "#!/usr/bin/env python3\n" + "".join(f"line {i}\n" for i in range(30))
        (self.repo / "hands" / "old").write_text(body)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.git("checkout", "-q", "-b", "work")
        (self.repo / "hands" / "old").unlink()
        (self.repo / "hands" / "gizmo").write_text(body)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "work")

        def git(*args, check=True, token=None):
            return subprocess.run(["git", *args], cwd=self.repo, check=check, capture_output=True)

        with mock.patch.object(selfclone, "_git", git):
            changed = selfclone.changed_paths("main")
        self.assertEqual(changed, [("A", "hands/gizmo"), ("D", "hands/old")])
        self.assertEqual(feature.new_hands(changed), ["gizmo"])


class RubricTest(Tree):
    def rubric(self, changed, tests=TESTS_PASS):
        self.calls = []

        def run_tests(folder, **kw):
            self.calls.append((folder, kw))
            return tests

        return feature.rubric(self.tree, changed, run_tests=run_tests)

    def test_a_complete_feature_is_ok_and_its_tests_ran_only_its_own_file(self):
        out = self.rubric(self.complete())
        self.assertEqual((out["ok"], out["missing"]), (True, []))
        self.assertEqual(out["hands"], [{
            "name": "gizmo", "entry": True, "contract": True, "tests": True,
            "ran": {"ok": True, "ran": 2, "jailed": True, "output": TESTS_PASS["output"]},
            "callers": ["gizmo-look"], "scheduled": ["gizmo-look"], "docs": True}])
        self.assertEqual(self.calls, [(self.tree, {"start": "hands/tests", "top": "hands/tests",
                                                    "pattern": "test_gizmo.py"})])

    def test_no_new_hand_says_what_a_feature_adds(self):
        out = self.rubric([("M", "hands/build"), ("A", "brain/playbooks/x.md")])
        self.assertFalse(out["ok"])
        self.assertEqual(out["hands"], [])
        self.assertEqual(out["missing"], ["no new hand: a feature adds hands/<name>, "
                                          "hands/lib/teeny_hands/<name>/ and hands/tests/test_<name>.py"])

    def test_each_missing_piece_is_named(self):
        changed = self.complete()
        (self.tree / "hands" / "gizmo").chmod(0o644)
        (self.tree / "hands" / "lib" / "teeny_hands" / "gizmo" / "cli.py").write_text("def main(argv):\n    return 0\n")
        (self.tree / "hands" / "tests" / "test_gizmo.py").unlink()
        (self.tree / "brain" / "playbooks" / "gizmo-look.md").unlink()
        (self.tree / "docs" / "hands.md").write_text("# Hands\n\n## `other`: another\n\nmentions `gizmo` in prose\n")
        out = self.rubric(changed)
        self.assertEqual(out["missing"], [
            "hands/gizmo is not an executable entry point",
            "hands/lib/teeny_hands/gizmo/ does not run through hand.run (the hand contract)",
            "no hands/tests/test_gizmo.py that parses",
            "no playbook calls hands/gizmo (an input `run = \"hands/gizmo ...\"` or `output = \"hand:gizmo <verb>\"`)",
            "docs/hands.md has no `## `gizmo`` section",
        ])
        self.assertEqual(self.calls, [])  # no tests to run, so none ran
        self.assertEqual(out["hands"][0]["ran"], {"ok": False, "ran": 0, "jailed": False, "output": ""})

    def test_a_hollow_hand_is_named_gap_by_gap(self):
        changed = self.complete()
        self.write("hands/gizmo", "#!/usr/bin/env python3\nprint('hi')\n", 0o755)
        self.write("hands/lib/teeny_hands/gizmo/cli.py", "# hand.run(\ndef main(argv):\n    return 0\n")
        self.write("hands/tests/test_gizmo.py", "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        pass\n")
        out = self.rubric(changed)
        self.assertEqual(out["missing"], [
            "hands/gizmo does not run its package teeny_hands.gizmo",
            "hands/lib/teeny_hands/gizmo/ does not run through hand.run (the hand contract)",
            "hands/tests/test_gizmo.py asserts nothing (no assert statement or self.assert* call)",
        ])
        self.assertEqual(self.calls, [])  # a test that asserts nothing is not run
        found = out["hands"][0]
        self.assertEqual((found["entry"], found["contract"], found["tests"]), (False, False, False))

    def test_source_that_does_not_parse_is_named_in_the_contract_message(self):
        changed = self.complete()
        self.write("hands/lib/teeny_hands/gizmo/extra.py", "def broken(:\n")
        out = self.rubric(changed)
        self.assertEqual(out["missing"], ["hands/lib/teeny_hands/gizmo/extra.py does not parse"])
        self.assertFalse(out["hands"][0]["contract"])

    def test_every_assertion_form_makes_a_test_file_count(self):
        for body in ("assert 1 == 1", "self.assertTrue(True)", "with self.assertRaises(ValueError):\n            int('x')"):
            with self.subTest(body=body):
                changed = self.complete()
                self.write("hands/tests/test_gizmo.py", "import unittest\n\n\nclass T(unittest.TestCase):\n"
                           f"    def test_x(self):\n        {body}\n")
                self.assertEqual(self.rubric(changed)["missing"], [])

    def test_a_cron_job_that_is_not_a_table_does_not_break_the_rubric(self):
        changed = self.complete()
        self.write("brain/cron.toml", "[jobs]\nfoo = 1\n")
        out = self.rubric(changed)
        self.assertEqual(out["missing"], ["no playbook that calls hands/gizmo is scheduled in brain/cron.toml"])

    def test_tests_that_do_not_parse_are_not_run(self):
        changed = self.complete()
        self.write("hands/tests/test_gizmo.py", "def broken(:\n")
        out = self.rubric(changed)
        self.assertEqual(out["missing"], ["no hands/tests/test_gizmo.py that parses"])
        self.assertEqual(self.calls, [])

    def test_tests_that_fail_in_the_jail_are_quoted_from_their_tail(self):
        out = self.rubric(self.complete(), tests=TESTS_FAIL)
        self.assertFalse(out["ok"])
        (message,) = out["missing"]
        self.assertTrue(message.startswith("the tests of hands/gizmo did not pass when the desk ran them: "))
        self.assertTrue(message.endswith("AssertionError: 1 != 2"))
        self.assertLessEqual(len(message.split(": ", 1)[1]), 400)
        self.assertFalse(out["hands"][0]["ran"]["ok"])

    def test_a_caller_that_is_not_scheduled_is_a_separate_gap(self):
        changed = self.complete()
        self.write("brain/cron.toml", '[jobs.other]\nschedule = "0 9 * * *"\ncommand = "bin/act something-else"\n')
        out = self.rubric(changed)
        self.assertEqual(out["missing"], ["no playbook that calls hands/gizmo is scheduled in brain/cron.toml"])
        self.assertEqual((out["hands"][0]["callers"], out["hands"][0]["scheduled"]), (["gizmo-look"], []))

    def test_every_new_hand_is_held_to_the_rubric(self):
        changed = self.complete("gizmo") + [("A", "hands/widget")]
        self.write("hands/widget", "#!/bin/sh\n", 0o755)
        out = self.rubric(changed)
        self.assertEqual([h["name"] for h in out["hands"]], ["gizmo", "widget"])
        self.assertTrue(all("hands/widget" in m or "widget" in m for m in out["missing"]))
        self.assertIn("hands/lib/teeny_hands/widget/ does not run through hand.run (the hand contract)", out["missing"])
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
