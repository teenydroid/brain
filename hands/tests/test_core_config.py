"""bin/lib/core_config.py and its sections: Teeny's tables written into the
core's config every boot, replacing their old selves (subtables included)
and leaving the core's own tables alone."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "hands" / "lib") not in sys.path:
    sys.path.insert(0, str(REPO / "hands" / "lib"))
spec = importlib.util.spec_from_file_location("core_config", REPO / "bin" / "lib" / "core_config.py")
core_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core_config)  # type: ignore[union-attr]

from teeny_hands.mcp.server import TOOLS  # noqa: E402
from teeny_hands import websites  # noqa: E402


class CoreConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / "config.toml"
        self._env = dict(os.environ)
        os.environ.pop("TEENY_CHROME_PATH", None)
        os.environ.pop("OPENHUMAN_WORKSPACE", None)
        # The sections make state/ folders under the root they are given.
        self.root = Path(self.tmp.name) / "root"
        (self.root / "deploy").mkdir(parents=True)
        (self.root / "deploy" / "browser-allow.txt").write_text((REPO / "deploy" / "browser-allow.txt").read_text())
        (self.root / "hands").symlink_to(REPO / "hands")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self.tmp.cleanup()

    def written(self, before: str = "") -> dict:
        self.config.write_text(before)
        core_config.write(self.config, self.root)
        return tomllib.loads(self.config.read_text())

    def test_the_settings_the_scheduled_agents_need(self):
        doc = self.written()
        self.assertEqual(doc["cron"]["shell_timeout_secs"], 1800)
        autonomy = doc["autonomy"]
        self.assertEqual((autonomy["enabled"], autonomy["level"], autonomy["workspace_only"]), (True, "full", True))
        self.assertEqual(autonomy["trusted_roots"], [{"path": str(self.root / "state" / "workbench"), "access": "readwrite"}])
        brain = self.root / "brain"
        for path in (self.root / "secrets", brain / "storage.db", brain / "agents", brain / "playbooks",
                     brain / "cron.toml", Path.home() / ".ssh"):
            self.assertIn(str(path), autonomy["forbidden_paths"])
        # TinySecurity refuses the whole policy over one relative entry.
        self.assertTrue(all(Path(p).is_absolute() for p in autonomy["forbidden_paths"]))
        browser = doc["browser"]
        self.assertEqual((browser["enabled"], browser["headless"], browser["profile_mode"]), (True, True, "persistent"))
        self.assertEqual(browser["profile_path"], str(self.root / "state" / "browser" / "profile"))
        self.assertTrue(Path(browser["profile_path"]).is_dir())
        self.assertIn("click", browser["unattended_actions"])
        self.assertEqual(doc["http_request"]["allowed_domains"], websites.load(self.root))
        self.assertEqual(doc["modules"]["overrides"],
                         [{"id": "tinycomputer", "source": "linked"}, {"id": "tinywallet", "source": "linked"}])
        [hands] = doc["mcp_client"]["servers"]
        self.assertEqual((hands["name"], hands["command"], hands["operator_trusted"], hands["expose"]),
                         ("hands", str(self.root / "hands" / "mcp"), True, "direct"))
        self.assertEqual(hands["allowed_tools"], list(TOOLS))

    def test_chrome_path_comes_from_the_environment_when_given(self):
        os.environ["TEENY_CHROME_PATH"] = "/opt/chrome/chrome"
        self.assertEqual(self.written()["browser"]["chrome_path"], "/opt/chrome/chrome")

    def test_replaces_its_tables_whole_and_keeps_the_cores(self):
        before = (
            '[agent]\nmax_tool_iterations = 7\n\n'
            '[cron]\nenabled = true\nshell_timeout_secs = 5\n\n'
            '[browser]\nenabled = false\n\n[browser.computer_use]\nendpoint = "http://x"\n\n'
            '[[autonomy.trusted_roots]]\npath = "/"\naccess = "readwrite"\n\n'
            '[inbound.sources."x:mentions"]\nagent_id = "teeny-mentions"\n'
        )
        doc = self.written(before)
        self.assertEqual(doc["agent"], {"max_tool_iterations": 7})
        self.assertEqual(doc["inbound"]["sources"]["x:mentions"]["agent_id"], "teeny-mentions")
        self.assertEqual(doc["cron"], {"shell_timeout_secs": 1800})
        self.assertNotIn("computer_use", doc["browser"])
        self.assertNotIn({"path": "/", "access": "readwrite"}, doc["autonomy"]["trusted_roots"])

    def test_running_twice_changes_nothing(self):
        self.written("[agent]\nx = 1\n")
        once = self.config.read_text()
        core_config.write(self.config, self.root)
        self.assertEqual(self.config.read_text(), once)

    def test_a_bad_allow_list_writes_nothing(self):
        self.config.write_text("[agent]\nx = 1\n")
        (self.root / "deploy" / "browser-allow.txt").write_text("*\n")
        with self.assertRaises(ValueError):
            core_config.write(self.config, self.root)
        self.assertEqual(self.config.read_text(), "[agent]\nx = 1\n")

    def test_drop_takes_subtables_and_arrays_of_tables(self):
        text = "[a]\nx = 1\n[a.b]\ny = 2\n[[a.c]]\nz = 3\n[ab]\nw = 4\n"
        self.assertEqual(core_config.drop(text, "a"), "[ab]\nw = 4\n")

    def test_two_sections_cannot_claim_one_table(self):
        sections = Path(self.tmp.name) / "sections"
        sections.mkdir()
        for name in ("a.py", "b.py"):
            (sections / name).write_text('def tables(root):\n    return {"[cron]": "x = 1\\n"}\n')
        (sections / "_helper.py").write_text("raise SystemExit('a leading underscore is not a section')\n")
        with self.assertRaises(ValueError) as caught:
            core_config.collect(self.root, sections)
        self.assertIn("[cron]", str(caught.exception))

    def test_another_concern_is_its_own_section(self):
        sections = Path(self.tmp.name) / "sections"
        sections.mkdir()
        (sections / "wallet.py").write_text('def tables(root):\n    return {"[wallet_policy]": "enabled = true\\n"}\n')
        self.assertEqual(core_config.collect(self.root, sections), {"[wallet_policy]": "enabled = true\n"})


if __name__ == "__main__":
    unittest.main()
