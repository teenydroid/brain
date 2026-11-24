"""The pull-request babysitter's agent (brain/agents/babysitter.toml; the
operator's decision of 2026-11-24): its posture, its job and its folder. What
code does with its report is hands/tests/babysit/test_sitter.py."""

import os
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import agent_check  # noqa: E402
import cron_policy  # noqa: E402
import frozen  # noqa: E402
import prompts  # noqa: E402
from core_sections import automation  # noqa: E402

SITTER = REPO / "brain" / "agents" / "babysitter.toml"


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "agents").mkdir()
        for suffix in ("toml", "md"):
            shutil.copy(SITTER.with_suffix("." + suffix), self.tmp / "agents")
        self.toml = self.tmp / "agents" / "babysitter.toml"
        self.prompt = self.tmp / "agents" / "babysitter.md"


class AgentTest(Tmp):
    def refused(self, old, new, path=None):
        path = path or self.toml
        before = path.read_text()
        self.assertIn(old, before)
        path.write_text(before.replace(old, new, 1))
        with self.assertRaises(SystemExit, msg=new):
            agent_check.check(self.toml)
        path.write_text(before)

    def test_the_repositorys_own_passes(self):
        self.assertEqual(agent_check.check(SITTER), "babysitter")

    def test_tools_that_reach_past_the_jail_are_refused(self):
        for tool in ("file_write", "edit", "apply_patch", "browser", "browser_click", "cron_add", "schedule", "spawn_subagent", "memory", "wallet_send",
                     "mcp_call", "composio", "install_skill", "*"):
            self.refused('    "grep",', f'    "grep",\n    "{tool}",')

    def test_it_must_stay_jailed_to_its_own_folder_with_no_memory(self):
        self.refused("sandbox_filesystem_only = true", "sandbox_filesystem_only = false")
        self.refused('sandbox_mode = "sandboxed"', 'sandbox_mode = "none"')
        for root in ("state/home", "state/sitter", "."):
            self.refused('scheduled_workspace = "state/sitter/home"', f'scheduled_workspace = "{root}"')
        self.refused("omit_memory_log = true", "omit_memory_log = false")
        self.refused('    "shell",\n', "")

    def test_its_prompt_says_how_a_run_with_nothing_laid_out_ends(self):
        self.refused("`nothing to do: no pull request waiting`", "`done`", self.prompt)
        self.refused("write no report", "carry on", self.prompt)

    def test_it_is_pinned_to_a_glm_model_the_core_is_told_of(self):
        doc = tomllib.loads(SITTER.read_text())
        self.assertTrue(doc["model"].startswith("glm-"))
        self.assertEqual(automation.agent_models(REPO / "brain")["babysitter"]["agent_model"], doc["model"])

    def test_its_definition_is_frozen_and_its_prompt_is_teenys(self):
        patterns = frozen.parse_patterns((REPO / "evals" / "frozen.txt").read_text())
        for path in ("brain/agents/babysitter.toml", "hands/lib/teeny_hands/babysit/sitter.py",
                     "hands/lib/teeny_hands/babysit/threads.py", "hands/lib/teeny_hands/build/sitterclone.py"):
            self.assertIsNotNone(frozen.frozen_pattern(path, patterns), path)
        self.assertIsNone(frozen.frozen_pattern("brain/agents/babysitter.md", patterns))

    def test_its_prompt_is_lean_and_built_as_it_is(self):
        text = SITTER.with_suffix(".md").read_text()
        self.assertLess(len(text), 5000)
        self.assertIn("babysitter", prompts.TOOLED)


class CronTest(Tmp):
    JOB = '[jobs.babysitter]\nschedule = "4-59/10 * * * *"\nagent = "babysitter"\nprompt = "p"\n'

    def setUp(self):
        super().setUp()
        self.cron, self.pinned = self.tmp / "cron.toml", self.tmp / "pinned.toml"
        self.cron.write_text("")
        self.pinned.write_text("")
        (self.tmp / "secrets").mkdir()
        saved = os.environ.get("TEENY_SECRETS")
        os.environ["TEENY_SECRETS"] = str(self.tmp / "secrets")
        self.addCleanup(lambda: os.environ.pop("TEENY_SECRETS") if saved is None
                        else os.environ.__setitem__("TEENY_SECRETS", saved))

    def load(self):
        return {j["name"]: j for j in cron_policy.load(self.cron, self.pinned, "UTC")}

    def test_only_a_pinned_job_may_run_it(self):
        self.cron.write_text(self.JOB.replace("jobs.babysitter", "jobs.mine"))
        with self.assertRaisesRegex(cron_policy.PolicyError, "only from a pinned job"):
            self.load()
        self.cron.write_text("")
        self.pinned.write_text(self.JOB)
        self.assertEqual(self.load()["teeny.babysitter"]["agent"], "babysitter")

    def test_it_is_off_until_self_changes_merge_themselves(self):
        with open(REPO / "evals" / "cron.pinned.toml", "rb") as fh:
            jobs = tomllib.load(fh)["jobs"]
        self.assertEqual((jobs["babysitter"]["switch"], jobs["babysit"]["schedule"], jobs["babysitter"]["schedule"]),
                         ("BABYSIT_SELF_MERGE_ALL", "*/10 * * * *", "4-59/10 * * * *"))
        self.pinned.write_text(self.JOB + 'switch = "BABYSIT_SELF_MERGE_ALL"\n')
        self.assertFalse(self.load()["teeny.babysitter"]["enabled"])
        (self.tmp / "secrets" / "teeny.env").write_text("BABYSIT_SELF_MERGE_ALL=1\n")
        self.assertTrue(self.load()["teeny.babysitter"]["enabled"])

    def test_the_whole_schedule_fits_with_every_agent_switched_on(self):
        (self.tmp / "secrets" / "teeny.env").write_text("MIND=1\nBABYSIT_SELF_MERGE_ALL=1\n")
        names = {j["name"] for j in cron_policy.load(REPO / "brain" / "cron.toml", REPO / "evals" / "cron.pinned.toml")
                 if j["enabled"] and j["kind"] == "agent"}
        self.assertLessEqual({"teeny.mind", "teeny.operator", "teeny.babysitter"}, names)


class FolderTest(Tmp):
    def test_the_core_finds_its_folder_there_at_boot(self):
        shutil.copytree(REPO / "deploy", self.tmp / "deploy")
        automation.tables(self.tmp)
        self.assertTrue((self.tmp / "state" / "sitter" / "home").is_dir())


if __name__ == "__main__":
    unittest.main()
