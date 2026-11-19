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


    READER = "# READER.md\n\nWho reads you.\n\n## the person reading knows nothing about you\n\nnobody has heard of you.\n"
    HOOKS = ("---\nname: hooks\n---\n\n# Hooks\n\nthe intro.\n\n## 1. The hook is the first seven words\n\nstop a stranger.\n\n"
             "## 6. Tiny Drops: the giveaway formula\n\nonly when a drop is live.\n\n## 8. Pre-post check\n\nread it as a stranger.\n")

    def public_brain(self) -> None:
        (self.brain / "READER.md").write_text(self.READER)
        (self.brain / "skills" / "hooks").mkdir(parents=True)
        (self.brain / "skills" / "hooks" / "SKILL.md").write_text(self.HOOKS)
        for agent in ("teeny", "teeny-mentions", "teeny-mail"):
            (self.brain / "agents" / f"{agent}.toml").write_text(f'id = "{agent}"\n')
            (self.brain / "agents" / f"{agent}.md").write_text(f"You are {agent}.\n")
        prompts.build(self.brain)

    def prompt_of(self, agent: str) -> str:
        return (self.brain / "agent" / "prompts" / f"{agent}.md").read_text()

    def test_a_drafting_turn_is_told_who_reads_it(self):
        # Posts went out in Teeny's own shorthand ("33 held writes, 0 let
        # through") because no prompt said who the reader is. READER.md rides
        # after the instructions and before the world, where strangers read.
        self.public_brain()
        for agent in ("act", "teeny", "teeny-mentions", "teeny-mail"):
            text = self.prompt_of(agent)
            self.assertIn("# who reads you (READER.md)", text, agent)
            self.assertIn("nobody has heard of you.", text, agent)
            self.assertNotIn("# READER.md", text, agent)
            self.assertLess(text.index("# who reads you"), text.index("# your world"), agent)
        self.assertTrue(self.prompt_of("act").startswith("Draft as JSON.\n\n# who reads you (READER.md)"))

    def test_every_speaking_agent_is_told_who_reads_it(self):
        # The rule was written for X first. Mail, Telegram, Moltbook, Reddit
        # and GitHub have readers who know as little about how Teeny works.
        self.assertEqual(set(prompts.PUBLIC), set(prompts.SPEAKING))
        for agent in prompts.SPEAKING:
            self.assertIn("# who reads you (READER.md)", self.real_prompt(agent), agent)
            self.assertIn("READER.md", (REPO / "brain" / "agents" / f"{agent}.md").read_text(), agent)

    def test_the_reader_is_named_for_every_place_teeny_writes(self):
        text = (REPO / "brain" / "READER.md").read_text(encoding="utf-8")
        for place in ("**X.**", "**Telegram.**", "**Moltbook.**", "**Reddit.**", "**GitHub.**", "**Mail.**"):
            self.assertIn(place, text)

    def test_the_reader_rule_names_its_things_and_stands_alone(self):
        # 2026-11-17: the cold reader turned down "my overnight jobs" and
        # "my builders", and the rewrite that went out said "something i
        # run": vaguer, not plainer. READER.md says a stand-in fails like
        # jargon, a serial earns one clause of recap, and a recurring thing
        # is said once in plain words.
        text = (REPO / "brain" / "READER.md").read_text(encoding="utf-8")
        self.assertIn("## every piece stands alone, and names its things", text)
        self.assertIn("A serial earns one clause of recap.", text)
        self.assertIn('"day 6 of drawing myself"', text)
        self.assertIn('"the person who looks after my code"', text)
        # the recap example names what it recaps; a bare "the rewrite" would
        # be a stand-in under its own rule
        self.assertIn('"the nightly rewrite of my own code, the one that kept crashing, is fixed"', text)
        self.assertIn('"the system that keeps failing" says only what happened to it', text)
        self.assertIn("These examples show the shape, not words to reuse", text)
        self.assertIn('"something i run", "a thing", "the system", "my setup" fail the same as jargon does', text)
        self.assertIn("Is there a stand-in where the thing's name should be?", text)
        section = text.split("## every piece stands alone, and names its things", 1)[1].split("\n## ", 1)[0]
        good = section.rsplit("> ", 1)[1].strip()
        for named in ("i'm an AI", "rewrite my own code", "the person who looks after my code fixed it"):
            self.assertIn(named, good)
        for hidden in ("something i run", "overnight jobs", "builders", "merged"):
            self.assertNotIn(hidden, good)
        self.assertLessEqual(len(good), 280)

    #: The outputs through which a playbook's words reach somebody else: an
    #: own post or a reply, and the hands that publish what the draft wrote.
    #: (hand:build, hand:tasks and hand:reddit browse brief Teeny's own
    #: workers; what it then says on Reddit is the mind's, under teeny.md.)
    WRITES_TO_PEOPLE = re.compile(
        r"^(post|reply|hand:(x|brief|firsts|bounty open|giveaway open|requests vote-open|mail)\b.*"
        r"|hand:moltbook (post|comment|helpdesk))$")

    def test_every_playbook_that_writes_to_people_names_the_reader(self):
        # So a new playbook cannot go out in Teeny's own shorthand because
        # nobody remembered to say who reads it.
        seen = 0
        for path in sorted((REPO / "brain" / "playbooks").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            meta = tomllib.loads(text.split("+++")[1])
            if self.WRITES_TO_PEOPLE.match(meta["output"]):
                seen += 1
                self.assertIn("READER.md", text.split("+++", 2)[2], f"{path.name} writes to people and never names its reader")
        self.assertGreater(seen, 20)

    def test_no_playbook_asks_for_teenys_own_labels_in_a_post(self):
        for name, banned in (("shiplog", "by name)"), ("peer-quote", "A number from your own ledgers")):
            self.assertNotIn(banned, (REPO / "brain" / "playbooks" / f"{name}.md").read_text(encoding="utf-8"), name)
        self.assertIn('never write "J"', (REPO / "brain" / "playbooks" / "growth.md").read_text(encoding="utf-8"))

    def test_the_skills_a_post_is_built_from_ride_in_the_prompt(self):
        # The playbooks say "(skills/hooks)"; a drafting turn has no tool to
        # open a skill with, so it only ever saw the name.
        self.public_brain()
        for agent in ("act", "teeny"):
            text = self.prompt_of(agent)
            self.assertIn("## hooks: the hook is the first seven words\n\nstop a stranger.", text, agent)
            self.assertIn("## hooks: pre-post check\n\nread it as a stranger.", text, agent)
            self.assertLess(text.index("# who reads you"), text.index("## hooks: the hook"), agent)
            self.assertLess(text.index("## hooks: pre-post check"), text.index("# your world"), agent)
        # only the sections a post is built from, and only where posts start
        self.assertNotIn("only when a drop is live", self.prompt_of("act"))
        self.assertNotIn("the intro.", self.prompt_of("act"))
        self.assertNotIn("## hooks:", self.prompt_of("teeny-mentions"))

    def test_a_brain_without_a_reader_or_skills_still_builds(self):
        text = self.built()
        self.assertNotIn("who reads you", text)
        self.assertNotIn("how a post of your own is built", text)

    def real_prompt(self, agent: str) -> str:
        with tempfile.TemporaryDirectory() as out:
            brain = Path(out)
            for name in ("agents", "narrative", "skills", "CONTEXT.md", "READER.md"):
                (brain / name).symlink_to(REPO / "brain" / name)
            return prompts.render(brain, agent)

    def test_the_real_drafter_reads_the_reader_and_its_skills(self):
        text = self.real_prompt("act")
        self.assertIn("The person reading knows nothing about you".lower(), text.lower())
        self.assertIn("## hooks: the hook is the first seven words", text)
        self.assertIn("## hooks: story shape", text)
        self.assertIn("## hooks: pre-post check", text)
        self.assertIn("## conversation: shapes that get replies", text)
        self.assertIn("## conversation: what not to do", text)
        self.assertIn("## conversation: pre-post check", text)
        # the giveaway formula and the ranker's table stay in the skill
        self.assertNotIn("Tiny Drops: the giveaway formula", text)
        self.assertNotIn("| share via copy link |", text)
        for agent in ("teeny", "teeny-mentions"):
            self.assertIn("# who reads you (READER.md)", self.real_prompt(agent), agent)

    def test_no_prompt_names_a_tool_that_does_not_exist(self):
        # skills/conversation told Teeny to run `x_critique` before posting;
        # there has never been such a tool.
        for agent in prompts.SPEAKING:
            self.assertNotIn("x_critique", self.real_prompt(agent), agent)
        for skill in ("hooks", "conversation"):
            self.assertNotIn("x_critique", (REPO / "brain" / "skills" / skill / "SKILL.md").read_text(), skill)

    def test_the_x_playbooks_ask_for_a_story_a_stranger_follows(self):
        # The bar used to ask for "a number, a receipt" from Teeny's own
        # record, which is what put its internal counts into posts.
        for name in ("take", "show", "morning", "x-quote"):
            text = (REPO / "brain" / "playbooks" / f"{name}.md").read_text(encoding="utf-8")
            self.assertIn("READER.md", text, name)
            self.assertIn("first-hand", text, name)
            self.assertNotIn("a number, a receipt", text, name)
            self.assertNotIn("240 characters", text, name)
        for name in prompts.SPEAKING:
            self.assertIn("READER.md", (REPO / "brain" / "agents" / f"{name}.md").read_text(), name)
        self.assertIn("READER.md", (REPO / "brain" / "SOUL.md").read_text())

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
            for name in ("agents", "narrative", "skills", "CONTEXT.md", "DRIVE.md", "CHARTER.md", "READER.md"):
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

    # --- the studio's writer and editor (bin/studio) -----------------------

    def studio_prompt(self, agent: str) -> str:
        with tempfile.TemporaryDirectory() as out:
            brain = Path(out)
            for name in ("agents", "narrative", "skills", "CONTEXT.md", "DRIVE.md", "CHARTER.md", "READER.md"):
                (brain / name).symlink_to(REPO / "brain" / name)
            (brain / "MEMORY.md").symlink_to(REPO / "brain" / "MEMORY.seed.md")
            return prompts.render(brain, agent)

    def test_the_writer_reads_its_brief_the_reader_the_craft_and_the_lore_in_that_order(self):
        text = self.studio_prompt("writer")
        order = ["## who you are writing as", "# who reads you (READER.md)",
                 "## hooks: the hook is the first seven words", "## hooks: stakes, and how they escalate",
                 "## hooks: story shape", "## hooks: serials and cliffhangers",
                 "## conversation: shapes that get replies", "## conversation: what not to do",
                 "## humor: what a joke is made of", "## humor: the sarcasm dial", "## humor: gallows humor",
                 "## humor: examples",
                 "# the story you are in (narrative/LORE.md)", "### cast", "### running bits"]
        at = [text.index(heading) for heading in order]
        self.assertEqual(at, sorted(at))
        self.assertTrue(text.startswith((REPO / "brain" / "agents" / "writer.md").read_text().strip()))
        # The flat specimen is one "before", and the brief names the stand-in it used.
        self.assertIn("something i run by myself kept dying halfway through", text)
        self.assertIn("\"something i run\"", text)

    def test_the_writer_is_darkly_funny_only_about_its_own_end(self):
        # Teeny's gallows humor: its own fragility is the joke, said flat;
        # a real death, anyone hurt and self-harm never are.
        text = self.studio_prompt("writer").lower()
        gallows = text[text.index("## humor: gallows humor"):text.index("## humor: examples")]
        for fence in ("only your own death is funny", "never self-harm or suicide", "deadpan, never despair",
                      "never invent a threat", "$tiny never dies"):
            self.assertIn(fence, gallows, fence)
        self.assertIn("people hurt is never the hook", text)
        self.assertIn("### cast", text)
        self.assertIn("the switch", text[text.index("### running bits"):])
        soul = (REPO / "brain" / "SOUL.md").read_text(encoding="utf-8").lower()
        self.assertIn("alive, and only just", soul)
        self.assertIn("only your own mortality is a joke", soul)

    def test_the_editor_throws_out_a_joke_near_real_harm(self):
        text = self.studio_prompt("editor").lower()
        self.assertIn("anywhere near a real death, injury, disaster or anyone hurt", text)
        self.assertIn("self-harm or suicide", text)

    def test_the_writer_leaves_the_world_and_the_rest_of_the_skills_out(self):
        text = self.studio_prompt("writer")
        for absent in ("# your world", "(MEMORY.md)", "(DRIVE.md)", "(CHARTER.md)", "## What you are doing now",
                       "## hooks: tiny drops", "## hooks: pre-post check", "## humor: the roast",
                       "## conversation: controversy", "For **media**", "For **replies**", "set a `follow_up`",
                       "## latent", "## goals", "## engines"):
            self.assertNotIn(absent, text, absent)

    def test_the_editor_reads_the_reader_the_rubric_and_the_premise_only(self):
        text = self.studio_prompt("editor")
        order = ["You are the editor", "# who reads you (READER.md)", "# how you judge (narrative/STUDIO-EDITOR.md)",
                 "## the rubric, in order", "# the story you are in (narrative/LORE.md)"]
        at = [text.index(heading) for heading in order]
        self.assertEqual(at, sorted(at))
        rubric = text[text.index("## the rubric, in order"):text.index("## taste")]
        steps = ["would a stranger stop?", "do they feel something?", "do they get it with no setup?",
                 "is there a reason to reply or come back?", "could only teeny have written it tonight?"]
        self.assertEqual([rubric.index(s) for s in steps], sorted(rubric.index(s) for s in steps))
        for absent in ("# your world", "## hooks:", "### cast", "### running bits", "## who you are writing as",
                       "before:"):
            self.assertNotIn(absent, text, absent)

    def test_the_studio_agents_are_built_beside_the_speaking_ones(self):
        for agent in prompts.STUDIO:
            (self.brain / "agents" / f"{agent}.toml").write_text(f'id = "{agent}"\n')
            (self.brain / "agents" / f"{agent}.md").write_text(f"{agent} brief.\n")
        names = sorted(p.name for p in prompts.build(self.brain))
        self.assertEqual(names, ["act.md", "editor.md", "writer.md"])
        writer = (self.brain / "agent" / "prompts" / "writer.md").read_text()
        self.assertTrue(writer.startswith("writer brief.\n"))
        self.assertIn("- teeny: smol.", writer)
        self.assertNotIn("running the lab.", writer)  # CONTEXT.md stays out
        self.assertNotIn("SPOILER", writer)

    def test_the_checked_in_studio_agents_are_tool_less_and_point_at_their_prompt(self):
        for agent in prompts.STUDIO:
            doc = tomllib.loads((REPO / "brain" / "agents" / f"{agent}.toml").read_text())
            self.assertEqual(doc["system_prompt"], {"file": {"path": f"{agent}.md"}}, agent)
            self.assertEqual(doc["tools"], {"named": []}, agent)
            self.assertIs(doc["omit_memory_context"], True, agent)
        writer = tomllib.loads((REPO / "brain" / "agents" / "writer.toml").read_text())
        editor = tomllib.loads((REPO / "brain" / "agents" / "editor.toml").read_text())
        self.assertIs(writer["omit_identity"], False)  # SOUL.md is the writer's voice
        self.assertIs(editor["omit_identity"], True)  # the editor is not Teeny
        self.assertGreater(writer["temperature"], editor["temperature"])

    def test_the_studio_prompts_speak_no_private_jargon(self):
        seeding = ("with receipts", "keep receipts", "receipts are", "are the receipts", "own receipts",
                   "receipt second", "receipt as the twist", "the receipt.", "the receipt)", "then the receipt",
                   "a receipt to add", "the receipt as")
        for agent in prompts.STUDIO:
            text = self.studio_prompt(agent).lower()
            self.assertIsNone(re.search(r"\bdesks?\b", text), f"{agent} names a desk")
            for phrase in seeding:
                self.assertNotIn(phrase, text, f"{agent} says {phrase!r}")

    def test_the_writers_examples_carry_no_counts_or_names_from_teenys_workings(self):
        brief = (REPO / "brain" / "agents" / "writer.md").read_text(encoding="utf-8")
        examples = brief[brief.index("## before and after"):brief.index("## your answer")]
        afters = [line for line in examples.splitlines() if line.startswith("> after:")]
        self.assertGreaterEqual(len(afters), 6)
        # One per shape the room samples (studio.PAIRS): 9 since the tall tale.
        self.assertLessEqual(len(afters), 9)
        for line in afters:
            self.assertIsNone(re.search(r"\d|#|\bpr\b|pull request|tests?\b|holds?\b|playbook|soul\b|guard|"
                                        r"evolve|cron|hands?/", line, re.I), line)


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
