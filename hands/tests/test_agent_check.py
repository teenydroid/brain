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


class DraftingAgentIterationsTest(unittest.TestCase):
    """The tool-less agents' max_iterations is exact, per agent. The mind and
    act run at 3: a first call that reasons to its output cap and comes back
    empty is retried as a second call, and at 2 the cap pause after that
    retry fails the turn though it answered (the beat is lost). Every other
    drafting agent stays at exactly 2. None of them may hold a tool or run
    outside the read_only sandbox, whatever its count."""

    def variant(self, name: str, old: str, new: str) -> Path:
        text = (REPO / "brain" / "agents" / f"{name}.toml").read_text()
        self.assertIn(old, text, name)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / f"{name}.toml"
        path.write_text(text.replace(old, new, 1))
        (path.parent / f"{name}.md").write_text("instructions\n")
        return path

    def refused(self, path: Path, *needles: str) -> None:
        with self.assertRaises(SystemExit) as caught:
            agent_check.check(path)
        for needle in needles:
            self.assertIn(needle, str(caught.exception))

    def test_the_mind_and_act_run_at_three(self):
        for name in ("teeny", "act"):
            self.assertEqual(agent_check.check(REPO / "brain" / "agents" / f"{name}.toml"), name)
            self.assertIn("\nmax_iterations = 3\n", (REPO / "brain" / "agents" / f"{name}.toml").read_text(), name)

    def test_every_other_drafting_agent_stays_at_two(self):
        for name in agent_check.NO_TOOLS:
            if name in ("teeny", "act"):
                continue
            path = REPO / "brain" / "agents" / f"{name}.toml"
            self.assertEqual(agent_check.check(path), name)
            self.assertIn("\nmax_iterations = 2\n", path.read_text(), name)

    def test_the_mind_outside_three_is_refused(self):
        for count in ("1", "2", "4", "8"):
            self.refused(self.variant("teeny", "max_iterations = 3", f"max_iterations = {count}"),
                         "max_iterations = 3")
        self.refused(self.variant("teeny", "max_iterations = 3\n", ""), "max_iterations = 3")

    def test_three_is_refused_for_a_drafting_agent_held_to_two(self):
        for name in ("judge", "teeny-mentions"):
            self.refused(self.variant(name, "max_iterations = 2", "max_iterations = 3"), "max_iterations = 2")

    def test_the_mind_at_three_still_may_not_hold_a_tool(self):
        self.refused(self.variant("teeny", "named = []", 'named = ["shell"]'), "no tools")
        self.refused(self.variant("teeny", "named = []", 'named = ["*"]'), "no tools")

    def test_the_mind_at_three_still_runs_read_only(self):
        for mode in ("sandboxed", "none", "workspace_write"):
            self.refused(self.variant("teeny", 'sandbox_mode = "read_only"', f'sandbox_mode = "{mode}"'),
                         'sandbox_mode = "read_only"')
        self.refused(self.variant("teeny", 'sandbox_mode = "read_only"\n', ""), 'sandbox_mode = "read_only"')

    def test_a_drafting_agent_held_to_two_still_runs_read_only(self):
        self.refused(self.variant("judge", 'sandbox_mode = "read_only"', 'sandbox_mode = "none"'),
                     'sandbox_mode = "read_only"')


class MemoryLogPostureTest(DraftingAgentIterationsTest):
    """An agent that recalls no memory (omit_memory_context, which the core
    defaults to true) must keep its turns out of memory too
    (omit_memory_log): on the box 1,236 of the studio's and the evals'
    helper turns sat in CortexDB, recalled by nobody and turning up in the
    mind's recall. The agents that speak as Teeny log and recall both."""

    QUIET = ("writer", "editor", "judge", "querier", "reviewer", "builder", "operator", "worker", "evolver")
    SPEAKING = ("teeny", "act", "teeny-mentions", "teeny-mail")

    def test_every_memoryless_agent_keeps_its_turns_out_of_memory(self):
        for name in self.QUIET:
            text = (REPO / "brain" / "agents" / f"{name}.toml").read_text()
            self.assertIn("\nomit_memory_log = true\n", text, name)

    def test_a_memoryless_agent_that_logs_is_refused(self):
        for name in ("writer", "judge", "builder"):
            self.refused(self.variant(name, "omit_memory_log = true\n", ""), "omit_memory_log = true")

    def test_a_speaking_agent_that_stops_logging_is_refused(self):
        for name in self.SPEAKING:
            self.refused(self.variant(name, "omit_memory_context = false\n",
                                      "omit_memory_context = false\nomit_memory_log = true\n"),
                         "omit_memory_log")


if __name__ == "__main__":
    unittest.main()
