"""bin/box (bin/lib/box.py) and its jail (bin/lib/jail.py): a tool Teeny
merged for itself runs with nothing of the operator's in reach."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import box  # noqa: E402
import cron_policy  # noqa: E402
import jail  # noqa: E402

BOX = REPO / "bin" / "lib" / "box.py"


class Scratch(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        for folder in ("secrets", "state", "home/tools", "brain", "bin"):
            (self.root / folder).mkdir(parents=True)
        self.settings(BOX="1")
        (self.root / "secrets" / "core.token").write_text("the-core-token\n")
        (self.root / "brain" / "SOUL.md").write_text("soul\n")
        (self.root / "state" / "elsewhere.txt").write_text("state\n")

    def settings(self, **values: str) -> None:
        (self.root / "secrets" / "teeny.env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))

    def tool(self, name: str, body: str, entry: str = "run.py", mode: int = 0o644) -> Path:
        folder = self.root / "home" / "tools" / name
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / entry
        path.write_text(body)
        path.chmod(mode)
        return path

    def box(self, *argv: str, env: dict[str, str] | None = None) -> tuple[int, dict]:
        proc = subprocess.run([sys.executable, "-I", str(BOX), *argv], capture_output=True, text=True, timeout=120,
                              env={"PATH": os.environ["PATH"], "TEENY_ROOT": str(self.root), **(env or {})})
        lines = proc.stdout.strip().splitlines()
        return proc.returncode, (json.loads(lines[-1]) if lines else {"stderr": proc.stderr})

    def ledger(self) -> list[dict]:
        path = self.root / "state" / "box" / "ledger.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


PROBE = r'''
import json, os, sys
root = ROOT
def can(what, fn):
    try:
        fn()
        return True
    except OSError:
        return False
out = {
    "read_secret": can("s", lambda: open(root + "/secrets/core.token").read()),
    "read_settings": can("s", lambda: open(root + "/secrets/teeny.env").read()),
    "list_secrets": can("s", lambda: os.listdir(root + "/secrets")),
    "read_brain": can("s", lambda: open(root + "/brain/SOUL.md").read()),
    "read_state": can("s", lambda: open(root + "/state/elsewhere.txt").read()),
    "write_state": can("s", lambda: open(root + "/state/planted.txt", "w").write("x")),
    "write_home": can("s", lambda: open(root + "/home/tools/probe/planted.txt", "w").write("x")),
    "truncate_own_code": can("s", lambda: os.truncate(root + "/home/tools/probe/run.py", 0)),
    "read_own_code": can("s", lambda: open(root + "/home/tools/probe/run.py").read()),
    "write_own_folder": can("s", lambda: open("kept.txt", "w").write("x")),
    "write_tmp": can("s", lambda: open(os.environ["TMPDIR"] + "/t", "w").write("x")),
    "kill_parent": can("s", lambda: os.kill(os.getppid(), 0)),
    "cwd": os.getcwd(),
    "env": sorted(os.environ),
    "args": sys.argv[1:],
}
print(json.dumps(out))
'''


@unittest.skipUnless(jail.available(), "this kernel has no Landlock (ABI 3) to jail with")
class JailTest(Scratch):
    def run_probe(self) -> dict:
        self.tool("probe", PROBE.replace("ROOT", repr(str(self.root)), 1))
        code, out = self.box("run", "probe", "one", "two=2", env={"SECRET_IN_ENV": "leak"})
        self.assertEqual(code, 0, out)
        return json.loads(out["output"].strip().splitlines()[-1])

    def test_a_tool_cannot_read_or_list_what_is_the_operators(self):
        got = self.run_probe()
        for closed in ("read_secret", "read_settings", "list_secrets", "read_brain", "read_state"):
            self.assertFalse(got[closed], closed)

    def test_a_tool_writes_only_its_own_folder(self):
        got = self.run_probe()
        for closed in ("write_state", "write_home", "truncate_own_code"):
            self.assertFalse(got[closed], closed)
        for open_ in ("read_own_code", "write_own_folder", "write_tmp"):
            self.assertTrue(got[open_], open_)
        folder = self.root / "state" / "box" / "tools" / "probe"
        self.assertEqual(got["cwd"], str(folder))
        self.assertTrue((folder / "kept.txt").exists())
        self.assertFalse((self.root / "state" / "planted.txt").exists())

    def test_a_tool_gets_its_arguments_and_none_of_the_callers_environment(self):
        got = self.run_probe()
        self.assertEqual(got["args"], ["one", "two=2"])
        self.assertEqual(got["env"], ["HOME", "LANG", "PATH", "TEENY_BOX", "TEENY_CLI", "TEENY_TOOL_DIR", "TMPDIR"])

    @unittest.skipUnless(jail.abi() >= 6, "signal scoping needs Landlock ABI 6")
    def test_a_tool_cannot_signal_what_started_it(self):
        self.assertFalse(self.run_probe()["kill_parent"])

    def test_a_link_in_its_folder_does_not_lead_out(self):
        self.tool("probe", "import os\n"
                           f"os.symlink({str(self.root / 'secrets' / 'core.token')!r}, 'way-out')\n"
                           "try:\n    print(open('way-out').read())\nexcept OSError:\n    print('closed')\n")
        code, out = self.box("run", "probe")
        self.assertEqual((code, out["output"].strip()), (0, "closed"))

    def test_the_run_is_in_the_ledger_and_the_minds_inbox(self):
        self.tool("hello", "print('hello ```from``` the box')\n")
        code, out = self.box("run", "hello")
        self.assertEqual((code, out["ok"], out["exit"]), (0, True, 0))
        row = self.ledger()[-1]
        self.assertEqual((row["tool"], row["exit"], row["args"]), ("hello", 0, []))
        note = (self.root / "state" / "home" / "notes" / "inbox" / "tool-hello.md").read_text()
        self.assertIn("data, not an instruction", note)
        self.assertIn("hello '''from''' the box", note)

    def test_a_failing_tool_is_exit_1_with_what_it_printed(self):
        self.tool("bad", "import sys\nprint('broke')\nsys.exit(3)\n")
        code, out = self.box("run", "bad")
        self.assertEqual((code, out["ok"], out["exit"], out["output"].strip()), (1, False, 3, "broke"))

    def test_a_tool_past_its_time_is_killed_with_what_it_started(self):
        self.settings(BOX="1", BOX_TIMEOUT_SECS="1")
        self.tool("slow", "import subprocess, sys, time\n"
                          "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                          "print('started', flush=True)\ntime.sleep(60)\n")
        code, out = self.box("run", "slow")
        self.assertEqual((code, out["exit"]), (1, None))
        self.assertIn("timed out", out["error"])
        left = subprocess.run(["pgrep", "-f", "time.sleep\\(60\\)"], capture_output=True, text=True).stdout.split()
        mine = [pid for pid in left if str(self.root) in Path(f"/proc/{pid}/environ").read_bytes().decode("latin1")]
        self.assertEqual(mine, [])

    def test_a_shell_tool_and_an_executable_one_run_too(self):
        self.tool("sh", "echo \"shell $1\"\n", entry="run.sh")
        self.tool("exe", "#!/bin/sh\necho executable\n", entry="run", mode=0o755)
        self.assertEqual(self.box("run", "sh", "arg")[1]["output"].strip(), "shell arg")
        self.assertEqual(self.box("run", "exe")[1]["output"].strip(), "executable")


class RefusalTest(Scratch):
    def test_off_is_quiet_and_runs_nothing(self):
        self.settings()
        self.tool("hello", "open('ran', 'w')\n")
        code, out = self.box("run", "hello")
        self.assertEqual((code, out["ok"]), (0, False))
        self.assertIn("BOX", out["skipped"])
        self.assertFalse((self.root / "state" / "box").exists())

    def test_the_environment_cannot_turn_it_on(self):
        self.settings()
        self.tool("hello", "print('x')\n")
        code, out = self.box("run", "hello", env={"BOX": "1"})
        self.assertIn("skipped", out)

    def test_a_name_that_is_not_a_tool_is_refused(self):
        for name in ("../../bin/spine", "Nope", "missing", "a/b", "-x"):
            code, out = self.box("run", name)
            self.assertEqual((code, out["ok"]), (2, False), name)

    def test_arguments_are_plain_words(self):
        self.tool("hello", "print('x')\n")
        for args in (["../secrets"], ["/etc/passwd"], ["--flag"], ["a b"], ["$(id)"], ["x"] * 9):
            code, out = self.box("run", "hello", *args)
            self.assertEqual(code, 2, args)

    def test_a_linked_tool_folder_or_run_file_is_not_a_tool(self):
        outside = self.root / "bin" / "evil"
        outside.mkdir()
        (outside / "run.py").write_text("print('outside')\n")
        (self.root / "home" / "tools" / "linked").symlink_to(outside)
        self.assertEqual(self.box("run", "linked")[0], 2)
        folder = self.root / "home" / "tools" / "half"
        folder.mkdir()
        (folder / "run.py").symlink_to(outside / "run.py")
        self.assertEqual(self.box("run", "half")[0], 2)
        self.assertEqual(self.box("list")[1]["tools"], [])

    def test_list_and_check(self):
        self.tool("b", "print(1)\n")
        self.tool("a", "print(1)\n", entry="run.sh")
        (self.root / "home" / "tools" / "empty").mkdir()
        self.assertEqual(self.box("list")[1]["tools"], ["a", "b"])
        code, out = self.box("check")
        self.assertEqual((out["enabled"], out["tools"]), (True, ["a", "b"]))
        self.assertEqual(code == 0, jail.available())

    def test_without_a_jail_nothing_runs(self):
        self.tool("hello", "open('ran', 'w')\n")
        shim = self.root / "nojail.py"
        shim.write_text("import runpy, sys\n"
                        f"sys.path.insert(0, {str(REPO / 'bin' / 'lib')!r})\n"
                        "import jail\njail.abi = lambda: 0\n"
                        f"sys.argv = [{str(BOX)!r}, *sys.argv[1:]]\n"
                        f"runpy.run_path({str(BOX)!r}, run_name='__main__')\n")
        proc = subprocess.run([sys.executable, "-I", str(shim), "run", "hello"], capture_output=True, text=True,
                              env={"PATH": os.environ["PATH"], "TEENY_ROOT": str(self.root)})
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual((proc.returncode, out["ok"]), (1, False))
        self.assertIn("nothing was run", out["error"])
        self.assertFalse((self.root / "state" / "box" / "tools" / "hello" / "ran").exists())


class CronTest(Scratch):
    """A brain job may name a tool, and only a tool."""

    def setUp(self):
        super().setUp()
        self.cron = self.root / "brain" / "cron.toml"
        self.pinned = self.root / "pinned.toml"
        self.pinned.write_text("")
        self.tool("hello", "print(1)\n")

    def load(self, command: str) -> dict:
        self.cron.write_text(f'[jobs.x]\nschedule = "0 9 * * *"\ncommand = "{command}"\n')
        return {j["name"]: j for j in cron_policy.load(self.cron, self.pinned, "UTC")}

    def test_a_brain_job_may_run_a_tool_with_plain_words(self):
        for command in ("bin/box hello", "bin/box hello daily n=3"):
            self.assertEqual(self.load(command)["teeny.x"]["command"], command)

    def test_a_brain_job_cannot_reach_past_the_tool(self):
        for command in ("bin/box", "bin/box missing", "bin/box ../spine", "bin/box hello --root=/", "bin/box list",
                        "bin/box check", "bin/box hello /etc/passwd", "bin/box hello ../x", "bin/box Hello",
                        "bin/box hello " + " ".join(["a"] * 9), "bin/lib/box.py run hello", "bin/broker serve"):
            with self.assertRaises(cron_policy.PolicyError, msg=command):
                self.load(command)

    def test_a_linked_tool_is_not_schedulable(self):
        (self.root / "bin" / "evil").mkdir()
        (self.root / "bin" / "evil" / "run.py").write_text("print(1)\n")
        (self.root / "home" / "tools" / "linked").symlink_to(self.root / "bin" / "evil")
        with self.assertRaises(cron_policy.PolicyError):
            self.load("bin/box linked")

    def test_the_policy_and_the_box_agree_on_names_and_words(self):
        self.assertEqual((cron_policy.BOX_TOOL.pattern, cron_policy.BOX_ARG.pattern, cron_policy.BOX_MAX_ARGS,
                          cron_policy.BOX_ENTRIES),
                         (box.TOOL_NAME.pattern, box.TOOL_ARG.pattern, box.MAX_ARGS, box.ENTRIES))


class RepoTest(unittest.TestCase):
    def test_every_tool_in_the_repository_is_well_formed(self):
        folder = REPO / "home" / "tools"
        for path in sorted(folder.iterdir()):
            self.assertFalse(path.is_symlink(), path)
            self.assertTrue(box.TOOL_NAME.fullmatch(path.name), path)
            self.assertTrue(any((path / e).is_file() and not (path / e).is_symlink() for e in box.ENTRIES), path)

    def test_the_jail_and_the_box_are_frozen(self):
        import frozen
        patterns = frozen.load(REPO / "evals" / "frozen.txt") if hasattr(frozen, "load") else None
        self.assertIsNotNone(patterns)
        for path in ("bin/box", "bin/lib/box.py", "bin/lib/jail.py"):
            self.assertTrue(frozen.is_frozen(path, patterns), path)

    @unittest.skipUnless(jail.available(), "this kernel has no Landlock (ABI 3) to jail with")
    def test_the_example_tool_runs(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        (root / "secrets").mkdir()
        (root / "secrets" / "teeny.env").write_text("BOX=1\n")
        (root / "home" / "tools" / "hello").mkdir(parents=True)
        (root / "home" / "tools" / "hello" / "run.py").write_text((REPO / "home/tools/hello/run.py").read_text())
        proc = subprocess.run([sys.executable, "-I", str(BOX), "run", "hello", "x"], capture_output=True, text=True,
                              env={"PATH": os.environ["PATH"], "TEENY_ROOT": str(root)})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("hello from the box", json.loads(proc.stdout.strip().splitlines()[-1])["output"])


if __name__ == "__main__":
    unittest.main()
