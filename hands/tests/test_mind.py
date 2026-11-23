"""The agentic mind (docs/mind.md): a cron-run agent with a shell, jailed to
state/home/, that acts on the world only through the broker.

These pin its posture: what bin/doctor (agent_check) and the cron rules
(cron_policy) accept, and that the job is off until the operator sets MIND=1.
"""

from __future__ import annotations

import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "lib"))
sys.path.insert(0, str(REPO / "bin" / "lib"))

import agent_check  # noqa: E402
import cron_policy  # noqa: E402

MIND = REPO / "brain" / "agents" / "mind.toml"


def _doc() -> dict:
    with open(MIND, "rb") as fh:
        return tomllib.load(fh)


class MindAgentTest(unittest.TestCase):
    def _variant(self, change) -> Path:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        text = change(MIND.read_text())
        (tmp / "mind.toml").write_text(text)
        (tmp / "mind.md").write_text((MIND.parent / "mind.md").read_text())
        return tmp / "mind.toml"

    def test_the_definition_passes_the_doctor(self):
        self.assertEqual(agent_check.check(MIND), "mind")

    def test_it_is_jailed_to_its_home_with_a_shell_and_its_own_prompt_file(self):
        doc = _doc()
        self.assertIs(doc["scheduled_actions"], True)
        self.assertEqual(doc["sandbox_mode"], "sandboxed")
        self.assertIs(doc["sandbox_filesystem_only"], True)
        self.assertEqual(doc["scheduled_workspace"], "state/home")
        self.assertEqual(doc["system_prompt"], {"file": {"path": "mind.md"}})
        for tool in ("shell", "file_read", "file_write", "edit", "memory"):
            self.assertIn(tool, doc["tools"]["named"])

    def test_tools_that_reach_past_the_jail_are_refused(self):
        for tool in ("browser_open", "cron_add", "cron_remove", "schedule", "wallet_send", "mcp_call", "composio",
                     "spawn_worker", "spawn_agent", "create_skill", "install_workflow_from_url"):
            path = self._variant(lambda t, tool=tool: t.replace('    "shell",', f'    "shell",\n    "{tool}",', 1))
            with self.assertRaises(SystemExit, msg=tool) as caught:
                agent_check.check(path)
            self.assertIn(tool, str(caught.exception))

    def test_a_wildcard_tool_list_is_refused(self):
        path = self._variant(lambda t: t[:t.index("[tools]")] + '[tools]\nnamed = ["*"]\n')
        with self.assertRaises(SystemExit):
            agent_check.check(path)

    def test_it_must_stay_sandboxed_to_files(self):
        for old, new in (('sandbox_mode = "sandboxed"', 'sandbox_mode = "none"'),
                         ("sandbox_filesystem_only = true", "sandbox_filesystem_only = false"),
                         ('scheduled_workspace = "state/home"', 'scheduled_workspace = "."'),
                         ('scheduled_workspace = "state/home"', 'scheduled_workspace = "state"')):
            path = self._variant(lambda t, old=old, new=new: t.replace(old, new, 1))
            self.assertNotEqual(path.read_text(), MIND.read_text(), old)
            with self.assertRaises(SystemExit, msg=new):
                agent_check.check(path)

    def test_the_prompt_tells_it_how_to_act_and_where_its_notes_are(self):
        prompt = (MIND.parent / "mind.md").read_text()
        for needle in ("bin/teeny", "notes/JOURNAL.md", "notes/PROJECTS.md", "notes/inbox"):
            self.assertIn(needle, prompt)


WORKER = REPO / "brain" / "agents" / "worker.toml"


class WorkerAgentTest(unittest.TestCase):
    """The mind's helper (docs/mind.md, "Helpers"): a sub-agent the mind may
    spawn, held to the same jail. Never a cron job, never anyone else's."""

    def _variant(self, source: Path, change, name: str | None = None) -> Path:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        for other in (MIND, WORKER):
            (tmp / other.name).write_text(other.read_text())
            (tmp / other.with_suffix(".md").name).write_text(other.with_suffix(".md").read_text())
        if source.with_suffix(".md").is_file():
            (tmp / source.with_suffix(".md").name).write_text(source.with_suffix(".md").read_text())
        target = tmp / (name or source.name)
        target.write_text(change(source.read_text()))
        return target

    def test_the_definition_passes_the_doctor(self):
        self.assertEqual(agent_check.check(WORKER), "worker")

    def test_the_mind_is_not_a_worker_tier_agent(self):
        # The core's agent_tier defaults to "worker", and it rejects the whole
        # agent registry when a worker-tier agent lists a subagent: every
        # agent turn then fails "AgentDefinitionRegistry is not initialised".
        with open(MIND, "rb") as fh:
            doc = tomllib.load(fh)
        self.assertIn(doc["agent_tier"], ("chat", "reasoning"))
        path = self._variant(MIND, lambda t: t.replace('agent_tier = "reasoning"\n', "", 1))
        self.assertNotIn('agent_tier = ', path.read_text())
        with self.assertRaises(SystemExit):
            agent_check.check(path)

    def test_it_is_jailed_like_the_mind_and_has_no_workspace_of_its_own(self):
        with open(WORKER, "rb") as fh:
            doc = tomllib.load(fh)
        self.assertIs(doc["scheduled_actions"], True)
        self.assertEqual(doc["sandbox_mode"], "sandboxed")
        self.assertIs(doc["sandbox_filesystem_only"], True)
        self.assertNotIn("scheduled_workspace", doc)
        self.assertNotIn("subagents", doc)
        self.assertLessEqual(set(doc["tools"]["named"]), set(_doc()["tools"]["named"]) - {"spawn_subagent", "memory"})

    def test_it_must_stay_jailed(self):
        for old, new in (('sandbox_mode = "sandboxed"', 'sandbox_mode = "none"'),
                         ("sandbox_filesystem_only = true", "sandbox_filesystem_only = false"),
                         ("scheduled_actions = true", 'scheduled_actions = true\nscheduled_workspace = "state"')):
            path = self._variant(WORKER, lambda t, old=old, new=new: t.replace(old, new, 1))
            self.assertNotEqual(path.read_text(), WORKER.read_text(), old)
            with self.assertRaises(SystemExit, msg=new):
                agent_check.check(path)

    def test_tools_that_reach_past_the_jail_are_refused(self):
        for tool in ("browser_open", "cron_add", "schedule", "wallet_send", "mcp_call", "spawn_subagent", "create_skill",
                     "memory", "*"):
            path = self._variant(WORKER, lambda t, tool=tool: t.replace('    "shell",', f'    "shell",\n    "{tool}",', 1))
            with self.assertRaises(SystemExit, msg=tool):
                agent_check.check(path)

    def test_a_tool_the_mind_lacks_is_refused_even_when_its_name_is_not_banned(self):
        for tool in ("http_request", "apply_patch"):
            path = self._variant(WORKER, lambda t, tool=tool: t.replace('    "shell",', f'    "shell",\n    "{tool}",', 1))
            self.assertIn(tool, path.read_text())
            with self.assertRaises(SystemExit, msg=tool):
                agent_check.check(path)

    def test_the_mind_and_its_worker_browse_view_only(self):
        # The core's browser is one profile, signed in as Teeny, with clicks
        # and typing allowed for the operator. `browser_view_only` is what
        # keeps a jailed agent's browser to reading, on a throwaway profile.
        for source in (MIND, WORKER):
            with open(source, "rb") as fh:
                doc = tomllib.load(fh)
            self.assertIn("browser", doc["tools"]["named"], source.name)
            self.assertIs(doc["browser_view_only"], True, source.name)

    def test_a_jailed_agent_without_the_view_only_flag_is_refused(self):
        for source in (MIND, WORKER):
            for new in ("browser_view_only = false\n", ""):
                path = self._variant(source, lambda t, new=new: t.replace("browser_view_only = true\n", new, 1))
                self.assertNotEqual(path.read_text(), source.read_text(), source.name)
                with self.assertRaises(SystemExit, msg=f"{source.name} {new!r}") as caught:
                    agent_check.check(path)
                self.assertIn("browser_view_only", str(caught.exception))

    def test_the_flag_is_required_even_without_the_browser_tool(self):
        # A child inherits it from the mind's run, and a later edit that adds
        # the tool must not be the only thing between Teeny's accounts and it.
        path = self._variant(WORKER, lambda t: t.replace('    "browser",\n', "", 1)
                             .replace("browser_view_only = true\n", "", 1))
        with self.assertRaises(SystemExit):
            agent_check.check(path)

    def test_no_other_agent_may_set_the_view_only_flag(self):
        # Agents that return early (no tools, editors, unscheduled, helpers)
        # must not slip the flag past the doctor either.
        for name in ("evolver", "judge", "editor", "teeny", "builder", "reviewer"):
            source = MIND.parent / f"{name}.toml"
            path = self._variant(source, lambda t: t + "\nbrowser_view_only = true\n")
            self.assertIn("browser_view_only = true", path.read_text())
            with self.assertRaises(SystemExit, msg=name) as caught:
                agent_check.check(path)
            self.assertIn("browser_view_only", str(caught.exception), name)

    def test_the_builder_still_has_no_browser(self):
        builder = MIND.parent / "builder.toml"
        path = self._variant(builder, lambda t: t.replace('    "shell",', '    "shell",\n    "browser",', 1))
        self.assertNotEqual(path.read_text(), builder.read_text())
        with self.assertRaises(SystemExit):
            agent_check.check(path)

    def test_it_must_not_be_shown_the_minds_memory(self):
        for old, new in (("omit_memory_context = true", "omit_memory_context = false"),
                         ("omit_memory_context = true\n", "")):
            path = self._variant(WORKER, lambda t, old=old, new=new: t.replace(old, new, 1))
            self.assertNotIn("omit_memory_context = true", path.read_text())
            with self.assertRaises(SystemExit, msg=new):
                agent_check.check(path)

    def test_it_cannot_have_helpers_of_its_own(self):
        path = self._variant(WORKER, lambda t: t + '\n[subagents]\nallowlist = ["worker"]\n')
        with self.assertRaises(SystemExit):
            agent_check.check(path)

    def test_it_is_never_a_cron_job(self):
        self.assertNotIn("worker", cron_policy.AGENT_JOB_AGENTS)

    def test_the_mind_may_spawn_only_its_worker(self):
        doc = _doc()
        self.assertIn("spawn_subagent", doc["tools"]["named"])
        self.assertEqual(doc["subagents"]["allowlist"], ["worker"])
        for allowlist in ('["worker", "operator"]', '["builder"]', '["*"]', '[]', '"worker"'):
            path = self._variant(MIND, lambda t, a=allowlist: t.replace('allowlist = ["worker"]', f"allowlist = {a}", 1))
            self.assertNotEqual(path.read_text(), MIND.read_text())
            with self.assertRaises(SystemExit, msg=allowlist):
                agent_check.check(path)

    def test_spawning_without_a_list_and_a_list_without_spawning_are_refused(self):
        no_list = self._variant(MIND, lambda t: t[:t.index("\n[subagents]\n")] + "\n")
        with self.assertRaises(SystemExit):
            agent_check.check(no_list)
        no_tool = self._variant(MIND, lambda t: t.replace('    "spawn_subagent",\n', "", 1))
        self.assertNotEqual(no_tool.read_text(), MIND.read_text())
        with self.assertRaises(SystemExit):
            agent_check.check(no_tool)

    def test_no_other_agent_may_have_helpers(self):
        for name in ("builder", "operator", "teeny", "act"):
            source = REPO / "brain" / "agents" / f"{name}.toml"
            path = self._variant(source, lambda t: t + '\n[subagents]\nallowlist = ["worker"]\n')
            with self.assertRaises(SystemExit, msg=name):
                agent_check.check(path)

    def test_every_agent_in_the_brain_passes_and_only_the_mind_has_helpers(self):
        for path in sorted((REPO / "brain" / "agents").glob("*.toml")):
            agent_check.check(path)
            with open(path, "rb") as fh:
                doc = tomllib.load(fh)
            if doc["id"] != "mind":
                self.assertNotIn("subagents", doc, path.name)

    def test_the_helper_and_its_list_are_frozen(self):
        import frozen
        patterns = frozen.parse_patterns((REPO / "evals" / "frozen.txt").read_text())
        for path in ("brain/agents/worker.toml", "brain/agents/mind.toml", "bin/lib/agent_check.py"):
            self.assertIsNotNone(frozen.frozen_pattern(path, patterns), path)
        self.assertIsNone(frozen.frozen_pattern("brain/agents/worker.md", patterns))


class MindCronTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "brain" / "agents").mkdir(parents=True)
        (self.tmp / "secrets").mkdir()
        for name in ("mind.toml", "mind.md"):
            (self.tmp / "brain" / "agents" / name).write_text((MIND.parent / name).read_text())
        self.cron = self.tmp / "brain" / "cron.toml"
        self.pinned = self.tmp / "pinned.toml"
        self.cron.write_text("")
        saved = os.environ.get("TEENY_SECRETS")
        os.environ["TEENY_SECRETS"] = str(self.tmp / "secrets")
        self.addCleanup(lambda: os.environ.pop("TEENY_SECRETS") if saved is None
                        else os.environ.__setitem__("TEENY_SECRETS", saved))

    def _load(self):
        return {j["name"]: j for j in cron_policy.load(self.cron, self.pinned, "UTC")}

    def test_the_mind_may_run_from_cron(self):
        self.assertIn("mind", cron_policy.AGENT_JOB_AGENTS)
        (self.tmp / "secrets" / "teeny.env").write_text("MIND=1\n")
        self.pinned.write_text('[jobs.mind]\nschedule = "*/30 * * * *"\nagent = "mind"\nprompt = "wake"\n')
        job = self._load()["teeny.mind"]
        self.assertEqual((job["kind"], job["agent"], job["enabled"]), ("agent", "mind", True))

    def test_a_switched_pinned_job_is_off_until_the_operator_sets_it(self):
        self.pinned.write_text('[jobs.mind]\nschedule = "*/30 * * * *"\nagent = "mind"\nprompt = "wake"\n'
                               'switch = "MIND"\n')
        self.assertIs(self._load()["teeny.mind"]["enabled"], False)
        (self.tmp / "secrets" / "teeny.env").write_text("MIND=0\n")
        self.assertIs(self._load()["teeny.mind"]["enabled"], False)
        (self.tmp / "secrets" / "teeny.env").write_text("OTHER=1\nMIND=1\n")
        self.assertIs(self._load()["teeny.mind"]["enabled"], True)

    def test_a_switch_cannot_turn_on_a_job_the_file_disables(self):
        (self.tmp / "secrets" / "teeny.env").write_text("MIND=1\n")
        self.pinned.write_text('[jobs.mind]\nschedule = "*/30 * * * *"\nagent = "mind"\nprompt = "wake"\n'
                               'switch = "MIND"\nenabled = false\n')
        self.assertIs(self._load()["teeny.mind"]["enabled"], False)

    def test_the_environment_cannot_flip_the_switch(self):
        self.pinned.write_text('[jobs.mind]\nschedule = "*/30 * * * *"\nagent = "mind"\nprompt = "wake"\n'
                               'switch = "MIND"\n')
        os.environ["MIND"] = "1"
        self.addCleanup(lambda: os.environ.pop("MIND", None))
        self.assertIs(self._load()["teeny.mind"]["enabled"], False)

    def test_the_brain_cannot_use_a_switch(self):
        self.pinned.write_text("")
        self.cron.write_text('[jobs.x]\nschedule = "0 * * * *"\nagent = "mind"\nprompt = "p"\nswitch = "MIND"\n')
        with self.assertRaises(cron_policy.PolicyError):
            self._load()

    def test_the_brain_may_schedule_the_mind_itself(self):
        self.pinned.write_text("")
        self.cron.write_text('[jobs.research]\nschedule = "0 6 * * *"\nagent = "mind"\nprompt = "read the papers"\n')
        job = self._load()["teeny.research"]
        self.assertEqual(job["agent"], "mind")
        # And every mind job follows the one switch, wherever it is written.
        self.assertIs(job["enabled"], False)
        (self.tmp / "secrets" / "teeny.env").write_text("MIND=1\n")
        self.assertIs(self._load()["teeny.research"]["enabled"], True)

    def test_the_brain_schedules_its_own_dreams_and_prune_as_written_and_no_other_script(self):
        # The pipelines are Teeny's to move since 2026-11-22: it may run
        # these operator scripts with exactly these arguments, and nothing
        # else under bin/ (a cron shell job runs outside any jail).
        self.pinned.write_text("")
        for command in ("bin/dream --detach", "bin/dream --light --detach", "bin/moltbook-prune"):
            self.cron.write_text(f'[jobs.x]\nschedule = "0 2 * * *"\ncommand = "{command}"\n')
            self.assertEqual(self._load()["teeny.x"]["command"], command)
        for command in ("bin/dream", "bin/dream --detach --root /tmp/x", "bin/dream --light", "bin/moltbook-prune now",
                        "bin/evolve run", "bin/spine", "bin/broker serve", "bin/pay-bills", "bin/babysit"):
            self.cron.write_text(f'[jobs.x]\nschedule = "0 2 * * *"\ncommand = "{command}"\n')
            with self.assertRaises(cron_policy.PolicyError, msg=command):
                self._load()

    def test_the_first_pipelines_are_in_teenys_own_cron_file(self):
        with open(REPO / "brain" / "cron.toml", "rb") as fh:
            brain = tomllib.load(fh)["jobs"]
        with open(REPO / "evals" / "cron.pinned.toml", "rb") as fh:
            pinned = tomllib.load(fh)["jobs"]
        for name in ("dream", "dream-midday", "builder", "episode", "moltbook-prune"):
            self.assertNotIn(name, pinned)
        # Teeny may reschedule, pause or drop these; check what is there.
        if "builder" in brain:
            self.assertEqual(brain["builder"]["agent"], "builder")
        # What watches Teeny, and what moves money, stays pinned.
        for name in ("tick", "mind", "babysit", "health", "audit", "guard-review", "evolve-watch", "goals-check",
                     "pay-bills", "surplus-sweep"):
            self.assertIn(name, pinned)

    def test_the_caps_leave_room_for_the_mind(self):
        # builder 12 a day, operator 72, the mind 48, and room for the jobs
        # the mind schedules for itself in brain/cron.toml.
        self.assertGreaterEqual(cron_policy.MAX_AGENT_RUNS_PER_DAY, 12 + 72 + 48 + 48)
        self.assertGreaterEqual(cron_policy.MAX_JOBS, 60)

    def test_the_repository_pins_the_mind_job_behind_its_switch(self):
        with open(REPO / "evals" / "cron.pinned.toml", "rb") as fh:
            job = tomllib.load(fh)["jobs"]["mind"]
        self.assertEqual(job["agent"], "mind")
        self.assertEqual(job["switch"], "MIND")
        self.assertEqual(job["schedule"], "*/30 * * * *")


if __name__ == "__main__":
    unittest.main()
