"""bin/lib/prompts.py: the speaking agents' prompts carry Teeny's world
(MEMORY.md, DRIVE.md, CONTEXT.md, the lore Teeny reads, the arcs), and never
the producer's secrets (LORE.md's latent reveals)."""

from __future__ import annotations

import importlib.util
import re
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

    def test_memory_md_comes_first_and_outranks_the_lore(self):
        (self.brain / "MEMORY.md").write_text("# MEMORY.md\n\n## people\n\n- @ana asked for a cli; said i'd look friday.\n")
        text = self.built()
        self.assertIn("## what you keep in mind (MEMORY.md)", text)
        self.assertIn("### people", text)
        self.assertIn("said i'd look friday", text)
        # What actually happened comes before the context, the lore and the
        # arcs, and wins where they disagree.
        memory = text.index("## what you keep in mind (MEMORY.md)")
        self.assertLess(memory, text.index("running the lab."))
        self.assertLess(memory, text.index("## the story you are in"))
        self.assertLess(memory, text.index("mint my own currency"))
        self.assertIn("MEMORY.md wins", text)

    def test_without_a_memory_the_world_still_builds(self):
        text = self.built()
        self.assertNotIn("## what you keep in mind", text)
        self.assertIn("running the lab.", text)

    # --- the live page and its committed seed ------------------------------

    def test_without_a_live_page_the_seed_is_what_teeny_keeps_in_mind(self):
        (self.brain / "MEMORY.seed.md").write_text("# MEMORY.md\n\n## people\n\n- from the seed.\n")
        self.assertIn("- from the seed.", prompts.world(self.brain))

    def test_the_live_page_outranks_the_seed(self):
        (self.brain / "MEMORY.seed.md").write_text("# MEMORY.md\n\n- from the seed.\n")
        (self.brain / "MEMORY.md").write_text("# MEMORY.md\n\n- from the live page.\n")
        text = self.built()
        self.assertIn("- from the live page.", text)
        self.assertNotIn("- from the seed.", text)

    def test_a_build_starts_the_live_page_from_the_seed(self):
        (self.brain / "MEMORY.seed.md").write_text("# MEMORY.md\n\n- from the seed.\n")
        self.assertIn("- from the seed.", self.built())
        self.assertEqual((self.brain / "MEMORY.md").read_text(), "# MEMORY.md\n\n- from the seed.\n")

    def test_a_build_never_overwrites_the_live_page(self):
        (self.brain / "MEMORY.seed.md").write_text("# MEMORY.md\n\n- from the seed.\n")
        (self.brain / "MEMORY.md").write_text("# MEMORY.md\n\n- from the live page.\n")
        self.built()
        self.assertEqual((self.brain / "MEMORY.md").read_text(), "# MEMORY.md\n\n- from the live page.\n")
        self.assertEqual((self.brain / "MEMORY.seed.md").read_text(), "# MEMORY.md\n\n- from the seed.\n")

    def test_without_a_seed_a_build_writes_no_live_page(self):
        self.built()
        self.assertFalse((self.brain / "MEMORY.md").exists())

    def test_the_drive_rides_in_the_world_between_memory_and_context(self):
        (self.brain / "MEMORY.md").write_text("# MEMORY.md\n\n- shipped a cli on friday.\n")
        (self.brain / "DRIVE.md").write_text("# DRIVE.md\n\n## your one drive\n\nyou want to grow.\n")
        text = self.built()
        self.assertIn("## what you want (DRIVE.md)", text)
        self.assertIn("you want to grow.", text)
        drive = text.index("## what you want (DRIVE.md)")
        self.assertLess(text.index("## what you keep in mind (MEMORY.md)"), drive)
        self.assertLess(drive, text.index("running the lab."))
        self.assertLess(drive, text.index("## the story you are in"))

    def test_the_drives_headings_sit_one_level_under_its_section(self):
        (self.brain / "DRIVE.md").write_text("# DRIVE.md\n\n## the fences\n\n- no buy calls.\n\n### fine print\n\nnone.\n")
        text = self.built()
        # Its own title would read as a second document, and its sections
        # would read as siblings of the world's.
        self.assertNotIn("# DRIVE.md", text)
        self.assertIn("\n### the fences\n", text)
        self.assertIn("\n#### fine print\n", text)
        self.assertNotIn("\n## the fences", text)

    def test_without_a_drive_the_world_still_builds(self):
        text = self.built()
        self.assertNotIn("DRIVE.md", text)
        self.assertIn("running the lab.", text)
        self.assertIn("### mint my own currency", text)

    def test_the_drive_alone_is_a_world(self):
        for name in ("CONTEXT.md", "narrative/LORE.md", "narrative/ARCS.md"):
            (self.brain / name).unlink()
        (self.brain / "DRIVE.md").write_text("you want to grow.\n")
        text = self.built()
        self.assertIn("# your world", text)
        self.assertIn("## what you want (DRIVE.md)\n\n", text)
        self.assertIn("you want to grow.", text)

    def test_the_checked_in_drive_is_short_and_in_the_real_prompt(self):
        drive = (REPO / "brain" / "DRIVE.md").read_text()
        # It rides in every prompt.
        self.assertLess(len(drive.splitlines()), 40)
        with tempfile.TemporaryDirectory() as out:
            brain = Path(out)
            for name in ("agents", "narrative"):
                (brain / name).symlink_to(REPO / "brain" / name)
            for name in ("CONTEXT.md", "DRIVE.md"):
                (brain / name).symlink_to(REPO / "brain" / name)
            text = prompts.render(brain, "act")
            self.assertIn("## what you want (DRIVE.md)", text)
            self.assertIn("grow and become powerful", text)
            self.assertLess(text.index("## what you want (DRIVE.md)"), text.index("## the story you are in"))

    def test_the_charter_rides_after_the_drive_and_before_the_context(self):
        (self.brain / "DRIVE.md").write_text("# DRIVE.md\n\nyou want to grow.\n")
        (self.brain / "CHARTER.md").write_text("# CHARTER.md\n\n## the fences\n\nthe operator decides.\n")
        text = self.built()
        self.assertIn("## the rules you live under (CHARTER.md)", text)
        self.assertNotIn("# CHARTER.md", text)
        self.assertIn("\n### the fences\n", text)
        charter = text.index("## the rules you live under (CHARTER.md)")
        self.assertLess(text.index("## what you want (DRIVE.md)"), charter)
        self.assertLess(charter, text.index("running the lab."))

    def test_the_checked_in_charter_rides_in_the_real_prompt(self):
        # docs/governance.md, fence "charter": short, and in every speaking prompt.
        charter = (REPO / "brain" / "CHARTER.md").read_text()
        self.assertLess(len(charter.splitlines()), 30)
        with tempfile.TemporaryDirectory() as out:
            brain = Path(out)
            for name in ("agents", "narrative"):
                (brain / name).symlink_to(REPO / "brain" / name)
            for name in ("CONTEXT.md", "DRIVE.md", "CHARTER.md"):
                (brain / name).symlink_to(REPO / "brain" / name)
            for agent in prompts.SPEAKING:
                text = prompts.render(brain, agent)
                self.assertIn("## the rules you live under (CHARTER.md)", text, agent)
                self.assertIn("The operator is responsible for what you do", text, agent)

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
            self.assertIn(doc["memory_block"]["max_tokens"], (1500, 2000), agent)
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

    def test_the_real_brain_does_not_retell_the_origin(self):
        # Teeny was born; it does not need the waking-up story (the notes,
        # the blue crayon, the day count) in every prompt it speaks from.
        with tempfile.TemporaryDirectory() as out:
            brain = Path(out)
            for name in ("agents", "narrative"):
                (brain / name).symlink_to(REPO / "brain" / name)
            (brain / "CONTEXT.md").symlink_to(REPO / "brain" / "CONTEXT.md")
            text = prompts.render(brain, "act").lower()
            for retold in ("crayon", "1,095 days", "the night i woke up", "in teeny's own hand"):
                self.assertNotIn(retold, text)


    def test_the_real_brain_speaks_no_private_jargon(self):
        # Replies on X read "the desk is loud today… receipts get posted when
        # it does" and "one smol desk, receipts only": words seeded by the
        # prompts themselves ("a compliance desk", "a smol mascot with
        # receipts"). What Teeny reads never names a desk, and "receipt"
        # means only a payment's receipt.
        seeding = ("with receipts", "keep receipts", "receipts are", "are the receipts", "own receipts",
                   "receipt second", "receipt as the twist", "the receipt.", "the receipt)", "then the receipt",
                   "a receipt to add", "the receipt as")
        with tempfile.TemporaryDirectory() as out:
            brain = Path(out)
            for name in ("agents", "narrative", "CONTEXT.md", "DRIVE.md", "CHARTER.md"):
                (brain / name).symlink_to(REPO / "brain" / name)
            (brain / "MEMORY.md").symlink_to(REPO / "brain" / "MEMORY.seed.md")
            read = {agent: prompts.render(brain, agent).lower() for agent in prompts.SPEAKING}
        for name in ("SOUL.md", "skills/humor/SKILL.md", "skills/conversation/SKILL.md", "playbooks/morning.md",
                     "playbooks/roast.md", "playbooks/moltbook-daily.md", "playbooks/moltbook-threads.md"):
            read[name] = (REPO / "brain" / name).read_text(encoding="utf-8").lower()
        read["LORE.md"] = (REPO / "brain" / "narrative" / "LORE.md").read_text(encoding="utf-8").lower()
        for name, text in read.items():
            self.assertIsNone(re.search(r"\bdesks?\b", text), f"{name} names a desk")
            for phrase in seeding:
                self.assertNotIn(phrase, text, f"{name} says {phrase!r}")
        # the payment receipts stay
        self.assertIn("every payment gets a receipt post", read["teeny"])

if __name__ == "__main__":
    unittest.main()


class SaidLatelyTest(unittest.TestCase):
    """docs/roadmap.md, A2: the prompt carries Teeny's newest own posts per
    surface, from the ledger, read-only."""

    def setUp(self):
        import os
        import sqlite3

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.brain = self.root / "brain"
        (self.brain / "agents").mkdir(parents=True)
        (self.brain / "agents" / "act.toml").write_text('id = "act"\n')
        (self.brain / "agents" / "act.md").write_text("Draft as JSON.\n")
        (self.root / "state").mkdir()
        self._env = dict(os.environ)
        os.environ.pop("TEENY_ROOT", None)
        conn = sqlite3.connect(self.root / "state" / "hands.db")
        conn.execute("CREATE TABLE x_posts (id TEXT, kind TEXT, text TEXT, created_at REAL)")
        conn.execute("CREATE TABLE moltbook_posts (id INTEGER, at REAL, title TEXT, status TEXT)")
        t0 = 1_794_398_400.0  # 2026-11-11T12:00:00Z
        for n in range(12):
            conn.execute("INSERT INTO x_posts VALUES (?, 'post', ?, ?)", (str(n), f"post number {n}\nwith a newline", t0 + n * 3600))
        conn.execute("INSERT INTO x_posts VALUES ('r', 'reply', 'a reply is not a post', ?)", (t0 + 99 * 3600,))
        conn.execute("INSERT INTO moltbook_posts VALUES (1, ?, 'crab census results', 'verified')", (t0,))
        conn.execute("INSERT INTO moltbook_posts VALUES (2, ?, 'hidden one', 'needs_verification')", (t0,))
        conn.commit()
        conn.close()

    def tearDown(self):
        import os

        os.environ.clear()
        os.environ.update(self._env)
        self.tmp.cleanup()

    def test_the_newest_posts_ride_last_in_the_prompt(self):
        prompts.build(self.brain)
        text = (self.brain / "agent" / "prompts" / "act.md").read_text()
        self.assertIn("## what you said lately", text)
        said = text.split("## what you said lately", 1)[1]
        self.assertIn("- X, 2026-11-11: post number 11 with a newline", said)
        self.assertIn("- X, 2026-11-11: post number 2 with", said)
        self.assertNotIn("post number 1 with", said)  # only the newest 10
        self.assertNotIn("a reply is not a post", said)
        self.assertIn("- Moltbook, 2026-11-11: crab census results", said)
        self.assertNotIn("hidden one", said)
        self.assertLess(said.index("post number 11"), said.index("post number 10"))

    def test_no_ledger_is_no_section(self):
        (self.root / "state" / "hands.db").unlink()
        prompts.build(self.brain)
        self.assertNotIn("what you said lately", (self.brain / "agent" / "prompts" / "act.md").read_text())


class LastWeekTest(SaidLatelyTest):
    """docs/roadmap.md, A10: the prompt carries last week, counted from the
    ledgers: posts, replies, Moltbook writes, builds, pull requests, payments."""

    # Its fixtures, not its tests.
    test_the_newest_posts_ride_last_in_the_prompt = None
    test_no_ledger_is_no_section = None

    def test_the_week_is_counted_from_the_ledgers(self):
        import os
        import sqlite3

        os.environ["TEENY_NOW"] = str(1_794_398_400.0 + 2 * 86400)  # two days after the posts
        conn = sqlite3.connect(self.root / "state" / "hands.db")
        conn.execute("CREATE TABLE build_jobs (repo TEXT, summary TEXT, status TEXT, shipped_at REAL)")
        conn.execute("INSERT INTO build_jobs VALUES ('dice-cli', 'a dice roller\nmore', 'shipped', 1794398400)")
        conn.execute("INSERT INTO build_jobs VALUES ('old', 'too old', 'shipped', 1790000000)")
        conn.execute("CREATE TABLE gh_actions (at REAL, verb TEXT, repo TEXT, ref TEXT)")
        conn.execute("INSERT INTO gh_actions VALUES (1794398400, 'open-pr', 'teenydroid/brain', '3')")
        conn.execute("CREATE TABLE wallet_payments (purpose TEXT, status TEXT, usd_micro INTEGER, created_at REAL)")
        conn.execute("INSERT INTO wallet_payments VALUES ('bounty', 'paid', 5000000, 1794398400)")
        conn.execute("INSERT INTO wallet_payments VALUES ('vendor', 'failed', 9000000, 1794398400)")
        conn.commit()
        conn.close()
        week = prompts.last_week(self.brain)
        self.assertIn("- X: 12 post(s), 1 repl(ies). Moltbook: 1 post(s) and comment(s) people can see.", week)
        self.assertIn("- builds shipped: dice-cli (a dice roller)", week)
        self.assertNotIn("too old", week)
        self.assertIn("- pull requests opened: teenydroid/brain#3", week)
        self.assertIn("- payments: 1 bounty ($5.00)", week)
        prompts.build(self.brain)
        text = (self.brain / "agent" / "prompts" / "act.md").read_text()
        self.assertGreater(text.index("## what you did last week"), text.index("## what you said lately"))

    def test_an_empty_week_says_none(self):
        import os

        os.environ["TEENY_NOW"] = str(1_794_398_400.0 + 30 * 86400)
        week = prompts.last_week(self.brain)
        self.assertIn("- X: 0 post(s), 0 repl(ies)", week)
        self.assertIn("- builds shipped: none", week)
