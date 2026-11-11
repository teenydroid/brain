"""bin/lib/prompts.py: the speaking agents' prompts carry Teeny's world
(CONTEXT.md, the lore Teeny reads, the arcs), and never the producer's
secrets (LORE.md's latent reveals)."""

from __future__ import annotations

import importlib.util
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("prompts", REPO / "bin" / "lib" / "prompts.py")
prompts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prompts)  # type: ignore[union-attr]

LORE = """# lore

the serial's bible. the producer reads all of it. teeny reads the cast.

the premise: tiny agents run the lab.

## goals

- secret goal list

## cast

- teeny: smol.

## running bits

- the kettle.

## latent

- the-chair | trigger: x | SPOILER the chair turns.
"""


class PromptsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.brain = Path(self.tmp.name)
        (self.brain / "agents").mkdir()
        (self.brain / "narrative").mkdir()
        (self.brain / "agents" / "act.toml").write_text('id = "act"\n')
        (self.brain / "agents" / "act.md").write_text("Draft as JSON.\n")
        (self.brain / "CONTEXT.md").write_text("# CONTEXT.md\n\n## What you are doing now\n\nrunning the lab.\n")
        (self.brain / "narrative" / "LORE.md").write_text(LORE)
        (self.brain / "narrative" / "ARCS.md").write_text("# arcs\n\n## mint my own currency\n\n- status: started.\n")

    def tearDown(self):
        self.tmp.cleanup()

    def built(self) -> str:
        prompts.build(self.brain)
        return (self.brain / "agent" / "prompts" / "act.md").read_text()

    def test_the_drafting_prompt_carries_context_lore_and_arcs(self):
        text = self.built()
        self.assertTrue(text.startswith("Draft as JSON.\n"))
        self.assertIn("running the lab.", text)
        self.assertIn("the premise: tiny agents run the lab.", text)
        self.assertIn("- teeny: smol.", text)
        self.assertIn("- the kettle.", text)
        self.assertIn("### mint my own currency", text)
        self.assertNotIn("# CONTEXT.md", text)

    def test_the_producers_parts_of_the_lore_stay_in_the_dark(self):
        text = self.built()
        self.assertNotIn("SPOILER", text)
        self.assertNotIn("secret goal list", text)
        self.assertNotIn("the producer reads all of it", text)

    def test_memory_md_rides_last_in_every_speaking_prompt(self):
        (self.brain / "MEMORY.md").write_text("# MEMORY.md\n\n## people\n\n- @ana asked for a cli; said i'd look friday.\n")
        text = self.built()
        self.assertIn("## what you keep in mind (MEMORY.md)", text)
        self.assertIn("### people", text)
        self.assertIn("said i'd look friday", text)
        # The most changeable part last, after the stable world.
        self.assertGreater(text.index("MEMORY.md"), text.index("mint my own currency"))

    def test_an_overlong_memory_is_cut_and_says_so(self):
        (self.brain / "MEMORY.md").write_text("x" * (prompts.MEMORY_MAX_CHARS + 500))
        text = self.built()
        self.assertIn("the rest was cut", text)
        self.assertLess(text.count("x"), prompts.MEMORY_MAX_CHARS + 100)

    def test_an_unchanged_brain_leaves_the_file_untouched(self):
        self.built()
        target = self.brain / "agent" / "prompts" / "act.md"
        before = target.stat().st_mtime_ns
        prompts.build(self.brain)
        self.assertEqual(target.stat().st_mtime_ns, before)

    def test_a_speaking_agent_without_instructions_is_an_error(self):
        (self.brain / "agents" / "act.md").write_text("  \n")
        with self.assertRaises(ValueError):
            prompts.build(self.brain)

    def test_an_absent_agent_is_skipped(self):
        self.assertEqual([p.name for p in prompts.build(self.brain)], ["act.md"])

    def test_the_checked_in_speaking_agents_point_at_their_built_prompt(self):
        for agent in prompts.SPEAKING:
            doc = tomllib.loads((REPO / "brain" / "agents" / f"{agent}.toml").read_text())
            self.assertEqual(doc["system_prompt"], {"file": {"path": f"{agent}.md"}}, agent)
            self.assertTrue((REPO / "brain" / "agents" / f"{agent}.md").read_text().strip(), agent)
            self.assertEqual(doc["memory_block"], {"max_tokens": 1500}, agent)
            self.assertIs(doc["refresh_prompt_on_resume"], True, agent)

    def test_the_real_brain_builds(self):
        with tempfile.TemporaryDirectory() as out:
            brain = Path(out)
            for name in ("agents", "narrative"):
                (brain / name).symlink_to(REPO / "brain" / name)
            (brain / "CONTEXT.md").symlink_to(REPO / "brain" / "CONTEXT.md")
            # Written beside, not into, the checkout's brain.
            text = prompts.render(brain, "act")
            self.assertIn("### running bits", text)
            self.assertNotIn("## latent", text)


if __name__ == "__main__":
    unittest.main()
