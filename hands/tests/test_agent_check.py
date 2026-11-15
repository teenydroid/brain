"""bin/lib/agent_check.py: the scheduled agents (builder, operator) always
end a run with a final message. A cron run with nothing queued that ends on
an empty message is a failed run to the harness (`The model returned an
empty response`, every hour), so each prompt must say what to end with when
there is nothing to do, and still that no report is written."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1].parent
spec = importlib.util.spec_from_file_location("agent_check", REPO / "bin" / "lib" / "agent_check.py")
agent_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_check)  # type: ignore[union-attr]

NOTHING_TO_DO = {
    "builder": "`nothing to do: no job waiting`",
    "operator": "`nothing to do: no task waiting`",
}


class ScheduledAgentIdleRunTest(unittest.TestCase):
    def agent(self, name: str) -> Path:
        return REPO / "brain" / "agents" / f"{name}.toml"

    def test_the_checked_in_scheduled_agents_pass(self):
        for name in NOTHING_TO_DO:
            self.assertEqual(agent_check.check(self.agent(name)), name)

    def test_each_prompt_ends_an_idle_run_with_its_nothing_to_do_line(self):
        for name, line in NOTHING_TO_DO.items():
            text = self.agent(name).read_text()
            self.assertIn(line, text, name)
            self.assertIn("write no report", text, name)
            self.assertIn("Never end a run with an empty message.", text, name)

    def test_a_scheduled_agent_whose_idle_run_can_end_empty_is_refused(self):
        for name, line in NOTHING_TO_DO.items():
            with tempfile.TemporaryDirectory() as out:
                broken = Path(out) / f"{name}.toml"
                broken.write_text(self.agent(name).read_text().replace(line, "nothing"))
                with self.assertRaises(SystemExit) as caught:
                    agent_check.check(broken)
                self.assertIn("nothing to do", str(caught.exception), name)


class EvolverPostureTest(unittest.TestCase):
    """The evolver edits a lab worktree with file tools and nothing else."""

    def variant(self, old: str, new: str) -> Path:
        text = (REPO / "brain" / "agents" / "evolver.toml").read_text()
        self.assertIn(old, text)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "evolver.toml"
        path.write_text(text.replace(old, new, 1))
        return path

    def test_the_checked_in_evolver_passes(self):
        self.assertEqual(agent_check.check(REPO / "brain" / "agents" / "evolver.toml"), "evolver")

    def test_a_shell_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            agent_check.check(self.variant('    "file_read",', '    "shell",\n    "file_read",'))
        self.assertIn("shell", str(caught.exception))

    def test_a_schedule_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            agent_check.check(self.variant('id = "evolver"', 'id = "evolver"\nscheduled_actions = true'))
        self.assertIn("scheduled_actions", str(caught.exception))

    def test_the_wildcard_is_refused(self):
        with self.assertRaises(SystemExit):
            agent_check.check(self.variant('    "file_read",', '    "*",'))


if __name__ == "__main__":
    unittest.main()
