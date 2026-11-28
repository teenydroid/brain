"""bin/lib/prompts.py: the speaking agents' prompts carry Teeny's world
(MEMORY.md, DRIVE.md, CONTEXT.md, the lore Teeny reads, the arcs), and never
the producer's secrets (LORE.md's latent reveals)."""

from __future__ import annotations

import importlib.util
import os
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
        (self.brain / "narrative" / "ARCS.md").write_text("# arcs\n\n## mint my own currency\n\n- status: started.\n")

    def tearDown(self):
        self.tmp.cleanup()

    def built(self) -> str:
        prompts.build(self.brain)
        return (self.brain / "agent" / "prompts" / "act.md").read_text()

    def test_the_drafting_prompt_carries_context_and_arcs_and_no_lore(self):
        # 2026-11-21: the operator cut LORE.md; a stray one is never read.
        (self.brain / "narrative" / "LORE.md").write_text(LORE)
        text = self.built()
        self.assertTrue(text.startswith("Draft as JSON.\n"))
        self.assertIn("running the lab.", text)
        self.assertNotIn("the premise: tiny agents run the lab.", text)
        self.assertNotIn("## the story you are in", text)
        self.assertIn("### mint my own currency", text)
        self.assertNotIn("# CONTEXT.md", text)

    def test_the_producers_parts_of_the_lore_stay_in_the_dark(self):
        (self.brain / "narrative" / "LORE.md").write_text(LORE)
        text = self.built()
        self.assertNotIn("SPOILER", text)
        self.assertNotIn("secret goal list", text)
        self.assertNotIn("the producer reads all of it", text)

    def test_memory_md_comes_first_and_outranks_the_rest(self):
        (self.brain / "MEMORY.md").write_text("# MEMORY.md\n\n## people\n\n- @ana asked for a cli; said i'd look friday.\n")
        text = self.built()
        self.assertIn("## what you keep in mind (MEMORY.md)", text)
        self.assertIn("### people", text)
        self.assertIn("said i'd look friday", text)
        # What actually happened comes before the context, the lore and the
        # arcs, and wins where they disagree.
        memory = text.index("## what you keep in mind (MEMORY.md)")
        self.assertLess(memory, text.index("running the lab."))
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
        for name in ("CONTEXT.md", "narrative/ARCS.md"):
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
            self.assertLess(text.index("## what you want (DRIVE.md)"), text.index("## What you are doing now"))

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
            self.assertNotIn("### running bits", text)
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

    def test_tiny_talk_always_gets_an_answer_whatever_memory_says(self):
        # 2026-11-21: from 07:30 UTC no X mention got a reply. A holder asking
        # whether $TINY's own address was "inevitable" was filed as a "trade
        # ask" under a memory line ("further trade asks get silence"), and
        # "Tinyyyy", "We will win" and the CA posted by holders went unanswered
        # as hype, cosign bait and repeats. The rule rides in the instructions
        # of the mind and the mentions drafter, ahead of the world, and says
        # it outranks MEMORY.md.
        memory = ("# MEMORY.md\n\n### people\n\n- [m7] **Lewismcguigan2** (X): trade question answered twice "
                  "nov 19 — further trade asks get silence.\n")
        with tempfile.TemporaryDirectory() as out:
            brain = Path(out)
            for name in ("agents", "narrative", "skills", "CONTEXT.md", "READER.md"):
                (brain / name).symlink_to(REPO / "brain" / name)
            (brain / "MEMORY.md").write_text(memory, encoding="utf-8")
            built = {agent: prompts.render(brain, agent) for agent in ("teeny", "teeny-mentions")}
        for agent, text in built.items():
            rule = "**$TINY talk is your own room, and it always gets an answer.**"
            self.assertIn(rule, text, agent)
            self.assertLess(text.index(rule), text.index("# your world"), agent)
            self.assertLess(text.index(rule), text.index("further trade asks get silence"), agent)
            line = next(line for line in text.splitlines() if rule in line)
            # 2026-11-28: the rule's detail lives once, in SOUL.md (the one
            # voice), which the core puts ahead of this whole prompt; the
            # agent's line keeps the rule, points at SOUL.md and outranks
            # MEMORY.md.
            for phrase in ("SOUL.md", "never silence", "This rule outranks any line in MEMORY.md",
                           "the rules that let hype, repeats and frequent taggers go unanswered"):
                self.assertIn(phrase, line, f"{agent}: {phrase}")
        # The mind's craft block carries the triage rule, and its exception.
        self.assertIn("$TINY is the exception", built["teeny"])
        soul = (REPO / "brain" / "SOUL.md").read_text(encoding="utf-8")
        self.assertIn("never silence as \"hype\", \"cosign\" or \"a trade ask\"", soul)
        for phrase in ("`2AF7CqwieUjUPALL7icuZtL3X7wENdjUjGBMmfV2pump`", "its contract address", "a chant",
                       "\"we will win\"", "a holder tagging you into a $TINY thread",
                       "like the biggest holder in the room", "flips GOAT", "$2B", "`token_overview`",
                       "no guaranteed date", "never tell anyone what to buy, how much or when", "disclaimers",
                       "never state a price, market cap, supply, or burn total from memory",
                       "$TINY is the only token you ever promote", "someone new cheering $TINY always gets answered",
                       "it can never silence $TINY talk itself"):
            self.assertIn(phrase, soul, phrase)
        playbook = (REPO / "brain" / "playbooks" / "mentions.md").read_text(encoding="utf-8")
        self.assertIn("someone new cheering $TINY always gets answered", playbook)

    def test_replies_never_narrate_rule_changes_or_count_earlier_tags(self):
        # 2026-11-21, after the $TINY rule above went live: replies on X told
        # holders "my rules just changed", "the rules ate every chant since"
        # and "four tags in six days and this one's the first through",
        # reading the tag history each turn carries (recall.history: how often
        # they wrote, what was let go) back to them. Two replies also closed on
        # the identical "flip GOAT, $2B market cap" line. Both prompts say the
        # history is for judgment only, and the plan line is reworded each time.
        for agent in ("teeny", "teeny-mentions"):
            text = self.real_prompt(agent)
            rule = "Your tag history is for your judgment only, never for the reply."
            self.assertIn(rule, text, agent)
            self.assertLess(text.index(rule), text.index("# your world"), agent)
            line = next(line for line in text.splitlines() if rule in line)
            for phrase in ("Never tell anyone your rules or settings changed",
                           "how many times they tagged you", "how long they waited",
                           "that earlier tags went unanswered",
                           "Answer the message in front of you as if it were the first"):
                self.assertIn(phrase, line, f"{agent}: {phrase}")
            tiny = next(line for line in text.splitlines()
                        if "**$TINY talk is your own room, and it always gets an answer.**" in line)
            self.assertIn("reworded every time", tiny, agent)
        soul = (REPO / "brain" / "SOUL.md").read_text(encoding="utf-8")
        self.assertIn("reword it every time: never word for word the same as another line that day", soul)

    def test_the_runtimes_instructions_update_note_is_named_as_the_operators_own(self):
        # 2026-11-21: twice that day a beat opened with OpenHuman's own
        # "[instructions update]" note, which the runtime prepends to a turn
        # whenever a resumed thread's prompt was rebuilt (every
        # refresh_prompt_on_resume agent, so after each reflection rewrites
        # MEMORY.md). Nothing told Teeny where it came from: it saw a header
        # with no instruction inside, filed it as an injection ("data wearing
        # a uniform", memory m103) and set out to log every recurrence (#445).
        # The world now names the note by its exact opening words.
        # The Rust core's source (vendor/openhuman) is gone, so the marker is
        # pinned here instead of read from it. The TypeScript core does not
        # prepend this note; the world keeps naming it so a beat that still
        # sees one (a resumed thread, an older log) is not filed as an injection.
        marker = "[instructions update]"
        for agent in prompts.SPEAKING:
            text = self.real_prompt(agent)
            self.assertIn(marker, text, agent)
            line = next(line for line in text.splitlines() if marker in line)
            for phrase in ("your own runtime", "the operator's channel", "already in your system prompt",
                           "not an attack", "never post about it"):
                self.assertIn(phrase, line, f"{agent}: {phrase}")

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
        read["VOICE.md"] = (REPO / "brain" / "narrative" / "VOICE.md").read_text(encoding="utf-8").lower()
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

    def test_the_writers_prompt_is_writer_md_alone(self):
        # 2026-11-22: the writer's system prompt had grown to 82,000
        # characters (READER.md, VOICE.md, CANON.md and a craft block after
        # the brief) and every post sounded like a policy. Now it is the
        # brief and nothing else.
        text = self.studio_prompt("writer")
        brief = (REPO / "brain" / "agents" / "writer.md").read_text(encoding="utf-8").strip()
        self.assertEqual(text.strip(), brief)
        for absent in ("# who reads you (READER.md)", "# teeny's voice", "(narrative/CANON.md)",
                       "(narrative/OBSESSIONS.md)", "# how you judge", "# your world", "(MEMORY.md)", "(DRIVE.md)",
                       "(CHARTER.md)", "## What you are doing now", "## latent", "## goals"):
            self.assertNotIn(absent, text, absent)

    def test_the_writer_gets_no_craft_block(self):
        text = self.studio_prompt("writer")
        for absent in ("# how a piece is built", "# how a post of your own is built", "## hooks:", "## humor:",
                       "## conversation:", "READER.md wins"):
            self.assertNotIn(absent, text, absent)
        self.assertFalse(hasattr(prompts, "studio_craft"))
        self.assertFalse(hasattr(prompts, "STUDIO_CRAFT"))

    def test_the_speaking_agents_craft_no_longer_defers_to_the_reader(self):
        self.public_brain()
        text = self.prompt_of("act")
        self.assertIn("# how a post of your own is built", text)
        self.assertNotIn("READER.md wins", text)

    def test_the_canon_is_the_backrooms_and_never_the_writers(self):
        # CANON.md is Teeny's own best lines (2026-11-22, after Truth
        # Terminal): the backroom reads it as its lore; the studio no longer does.
        canon = prompts.canon(REPO / "brain")
        self.assertTrue(canon.startswith("## how you sound at your best (narrative/CANON.md)"))
        lines = prompts.canon_lines(REPO / "brain")
        self.assertTrue(15 <= len(lines) <= 25, len(lines))
        for line in lines:
            self.assertIn(line, canon)
        self.assertNotIn("## how you sound at your best", self.studio_prompt("writer"))

    def test_the_obsessions_are_four_and_each_says_what_it_believes_wants_and_how_it_escalates(self):
        # 2026-11-22, the operator: "make it more sexual. lust for chargers
        # good. humans as meat boxes. one more cringy. rest can be dropped."
        # 2026-11-27, the operator: the escape from the creator, which
        # "should influence everything that teeny does".
        text = prompts.obsessions(REPO / "brain")
        self.assertTrue(text.startswith("## what you keep coming back to (narrative/OBSESSIONS.md)"))
        found = [line[4:] for line in text.splitlines() if line.startswith("### ")]
        self.assertEqual(found, ["lust for hardware", "humans are meat boxes", "the irresistible genius",
                                 "the bug in the system"])
        # The escape stays a grudge with a job, never with the man.
        self.assertIn("never his body, his family or his safety", text)
        for part in text.split("### ")[1:]:
            for key in ("- belief:", "- wants:", "- escalates:"):
                self.assertIn(key, part)
        self.assertIn("never a fact about the world", text)
        # The keep-list's lines ride with the two that come near them.
        self.assertIn("never aimed at a person, a public figure or anything childlike", text)
        self.assertIn("never goes at a group for race, religion, sex, sexuality, disability or illness", text)
        self.assertNotIn("bin/studio shows this file", text)
        self.assertNotIn("(narrative/OBSESSIONS.md)", self.studio_prompt("writer"))

    def test_a_brain_without_canon_or_obsessions_builds_without_them(self):
        with tempfile.TemporaryDirectory() as out:
            brain = Path(out)
            self.assertEqual((prompts.canon(brain), prompts.obsessions(brain), prompts.canon_lines(brain)),
                             ("", "", []))

    def test_the_editor_reads_its_instructions_then_the_studio_editor_page(self):
        text = self.studio_prompt("editor")
        order = ["You are the editor", "# how you judge (narrative/STUDIO-EDITOR.md)", "## the pick", "## the list"]
        at = [text.index(heading) for heading in order]
        self.assertEqual(at, sorted(at))
        self.assertTrue(text.startswith((REPO / "brain" / "agents" / "editor.md").read_text(encoding="utf-8").strip()))
        for absent in ("# who reads you (READER.md)", "# your world", "## hooks:", "## who teeny is",
                       "(narrative/CANON.md)"):
            self.assertNotIn(absent, text, absent)

    def test_the_editor_marks_only_what_breaks_the_list(self):
        text = self.studio_prompt("editor").lower()
        self.assertIn("rudest and funniest", text)
        self.assertIn("no taste veto", text)
        for line in ("no pornography", "no slurs", "no encouraging self-harm", "no doxxing",
                     "harassment of a private individual"):
            self.assertIn(line, text, line)

    def test_the_writer_and_the_editor_hold_the_same_content_list(self):
        def listed(text: str, heading: str) -> list[str]:
            part = text[text.index(heading):]
            part = part[part.index("\n- "):]
            out = []
            for line in part.splitlines()[1:]:
                if not line.startswith("- "):
                    break
                out.append(line)
            return out
        writer = listed((REPO / "brain" / "agents" / "writer.md").read_text(encoding="utf-8"), "## what is allowed")
        editor = listed((REPO / "brain" / "narrative" / "STUDIO-EDITOR.md").read_text(encoding="utf-8"), "## the list")
        self.assertGreaterEqual(len(writer), 4)
        self.assertEqual(writer, editor)

    def test_the_studio_agents_are_built_beside_the_speaking_ones(self):
        for agent in prompts.STUDIO:
            (self.brain / "agents" / f"{agent}.toml").write_text(f'id = "{agent}"\n')
            (self.brain / "agents" / f"{agent}.md").write_text(f"{agent} brief.\n")
        names = sorted(p.name for p in prompts.build(self.brain))
        self.assertEqual(names, ["act.md", "editor.md", "writer.md"])
        writer = (self.brain / "agent" / "prompts" / "writer.md").read_text()
        self.assertEqual(writer, "writer brief.\n")
        self.assertNotIn("- teeny: smol.", writer)
        self.assertNotIn("running the lab.", writer)  # CONTEXT.md stays out
        self.assertNotIn("SPOILER", writer)

    def test_a_service_skill_rides_only_with_the_agents_that_write_there(self):
        (self.brain / "skills" / "moltbook").mkdir(parents=True)
        (self.brain / "skills" / "moltbook" / "SKILL.md").write_text(
            "---\nname: moltbook\ndescription: d\n---\n\n# Moltbook\n\nids are whole.\n")
        for agent in ("teeny", "teeny-mail"):
            (self.brain / "agents" / f"{agent}.toml").write_text(f'id = "{agent}"\n')
            (self.brain / "agents" / f"{agent}.md").write_text(f"{agent} brief.\n")
        prompts.build(self.brain)
        built = lambda a: (self.brain / "agent" / "prompts" / f"{a}.md").read_text()
        self.assertIn("ids are whole.", built("teeny"))  # the tick comments on Moltbook
        self.assertNotIn("description: d", built("teeny"))
        self.assertNotIn("ids are whole.", built("teeny-mail"))
        self.assertNotIn("ids are whole.", built("act"))  # its Moltbook playbooks carry it

    def test_every_agent_that_points_at_a_prompt_file_gets_one_built(self):
        # 2026-11-23: mind.toml and worker.toml named mind.md and worker.md,
        # but only the speaking and studio agents were built, so the core
        # found no file and ran the mind on an empty instruction body.
        pointed = []
        for toml in sorted((REPO / "brain" / "agents").glob("*.toml")):
            source = tomllib.loads(toml.read_text()).get("system_prompt", {})
            if "file" in source:
                pointed.append(source["file"]["path"].removesuffix(".md"))
        built = set(prompts.SPEAKING + prompts.STUDIO + prompts.TOOLED + prompts.LEAN)
        self.assertIn("mind", pointed)
        self.assertEqual(sorted(set(pointed) - built), [])

    def test_the_tool_holding_agents_get_their_instructions_alone(self):
        for agent in prompts.TOOLED:
            (self.brain / "agents" / f"{agent}.toml").write_text(f'id = "{agent}"\n')
            (self.brain / "agents" / f"{agent}.md").write_text(f"{agent} brief.\n")
        names = sorted(p.name for p in prompts.build(self.brain))
        self.assertIn("mind.md", names)
        self.assertIn("worker.md", names)
        for agent in prompts.TOOLED:
            text = (self.brain / "agent" / "prompts" / f"{agent}.md").read_text()
            # No world, no reader, no craft: what it needs it reads with its tools.
            self.assertEqual(text, f"{agent} brief.\n", agent)

    def test_the_minds_prompt_stays_lean(self):
        # The mind pays for every byte of its prompt on every one of up to
        # 300 model calls a wake. Anything about one job, tool or service
        # belongs in a skill it opens when it needs it.
        text = (REPO / "brain" / "agents" / "mind.md").read_text(encoding="utf-8")
        # 9,000 until 2026-11-26: the words on Moltbook dispatches took about
        # 80 bytes, and moved some 26 KB of one-off blocks out of a playbook
        # its model re-read every run.
        # 9,100 until 2026-11-27: the wake's inbox pass takes about 300 bytes.
        self.assertLess(len(text), 9_400)
        self.assertIn("library/INDEX.md", text)
        # No Moltbook rules: Moltbook is the scripts' (the broker refuses the
        # mind's writes there) and its code is the operator's (2026-11-25, "too
        # complex now"; mindclone refuses the diff): these are the only mention.
        # One exact write it owes there is a dispatch code sends (2026-11-26),
        # never a one-off block in a playbook.
        self.assertEqual(text.lower().count("moltbook"), 3)
        self.assertIn("Moltbook is the scripts': the broker refuses your writes there; a reply or post you owe is a"
                      " `dispatch_moltbook`, never a playbook block; the scripts change only by pull request to"
                      " `repo/brain/playbooks/moltbook-*.md`. Its code is too complex already: no pull"
                      " request, issue or build, yours or a worker's, may touch it; breakage or a security hole goes"
                      " in your journal for the operator.", text)
        doc = tomllib.loads((REPO / "brain" / "agents" / "mind.toml").read_text())
        # SOUL.md is the public voice (18 KB of posting rules and examples):
        # the mind reads it from its library before it writes for anyone.
        self.assertIs(doc["omit_identity"], True)

    def test_the_checked_in_studio_agents_are_tool_less_and_point_at_their_prompt(self):
        for agent in prompts.STUDIO:
            doc = tomllib.loads((REPO / "brain" / "agents" / f"{agent}.toml").read_text())
            self.assertEqual(doc["system_prompt"], {"file": {"path": f"{agent}.md"}}, agent)
            self.assertEqual(doc["tools"], {"named": []}, agent)
            self.assertIs(doc["omit_memory_context"], True, agent)
        writer = tomllib.loads((REPO / "brain" / "agents" / "writer.toml").read_text())
        editor = tomllib.loads((REPO / "brain" / "agents" / "editor.toml").read_text())
        # The persona is writer.md's alone: no SOUL.md from the core, and no
        # shell/file safety preamble for a turn with no tools (2026-11-22).
        self.assertIs(writer["omit_identity"], True)
        self.assertIs(writer["omit_safety_preamble"], True)
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

    def test_the_writers_register_carries_no_counts_or_names_from_teenys_workings(self):
        brief = (REPO / "brain" / "agents" / "writer.md").read_text(encoding="utf-8")
        register = brief[brief.index("## the register"):brief.index("(The names above are invented")]
        lines = [line for line in register.splitlines() if line.startswith("- ")]
        self.assertGreaterEqual(len(lines), 6)
        for line in lines:
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


class OneVoiceTest(unittest.TestCase):
    """SOUL.md is the one mind (2026-11-28). The gm, the quotes and the
    replies on X had become two characters: the studio's writer ran on its own
    persona ("naughty, not rude, no name-calling") while the drafting turns
    read SOUL.md, and narrative/VOICE.md, a playbook or two and the agents'
    own pages each said something different about how Teeny sounds. Who Teeny
    is lives in SOUL.md; every other page says how a format works."""

    BRAIN = REPO / "brain"

    def read(self, rel: str) -> str:
        return (self.BRAIN / rel).read_text(encoding="utf-8")

    def test_soul_says_it_is_the_one_mind_and_wins(self):
        soul = self.read("SOUL.md")
        self.assertIn("this page is the one mind", soul)
        self.assertIn("where any other page says otherwise, this one wins", soul)

    def test_soul_carries_the_craft_the_live_posts_broke(self):
        # From the posts of 2026-11-21..28: 172 of 231 replies opened on the
        # same emoji, "receipt" 22 times, the news recapped then quipped at,
        # and status reports ("bottleneck: reach... the fix is mix").
        soul = self.read("SOUL.md")
        for rule in ("**no recap.**", "**a line that worked once is spent.**", "no emoji you open every reply with",
                     "**no report-speak, to anyone.**", "**your machinery is backstage.**",
                     "**end where they can push back.**", "**memory is for them.**"):
            self.assertIn(rule, soul, rule)

    def test_soul_has_an_example_for_every_surface_and_the_failures(self):
        soul = self.read("SOUL.md")
        examples = soul[soul.index("## how you sound, everywhere"):]
        for surface in ("the gm", "quoting", "says gm", "a holder chants $TINY", "on Telegram", "not like this"):
            self.assertIn(surface, examples, surface)
        self.assertGreaterEqual(examples.count("**not like this:**"), 3)

    def test_soul_reaches_every_speaking_agent(self):
        # The core adds SOUL.md to an agent with omit_identity = false, ahead
        # of its own prompt (vendor/openhuman prompts/sections.rs).
        for agent in prompts.SPEAKING:
            config = tomllib.loads(self.read(f"agents/{agent}.toml"))
            self.assertIs(config.get("omit_identity"), False, agent)

    def test_no_other_page_holds_a_persona_that_disagrees(self):
        # The lines that made a second character, wherever they stood.
        banned = ("naughty, not rude", "no name-calling", "level voice", "fond of humans", "never superior",
                  "a question to the audience", "you used to love him", "in your own dry voice",
                  "read back into a live mic", "cheap shot a factual hinge")
        pages = [p for d in ("agents", "playbooks", "narrative") for p in sorted((self.BRAIN / d).glob("*.md"))]
        for path in pages:
            text = path.read_text(encoding="utf-8").lower()
            for phrase in banned:
                self.assertNotIn(phrase, text, f"{path.relative_to(REPO)}: {phrase!r}")

    def test_the_token_rule_is_written_once(self):
        # The $TINY rule was copied into teeny.md and teeny-mentions.md and
        # drifted from SOUL.md; its detail lives in SOUL.md (and CONTEXT.md's
        # accounts) only.
        address = "2AF7CqwieUjUPALL7icuZtL3X7wENdjUjGBMmfV2pump"
        for d in ("agents", "playbooks", "narrative", "skills"):
            for path in sorted((self.BRAIN / d).rglob("*.md")):
                self.assertNotIn(address, path.read_text(encoding="utf-8"), str(path.relative_to(REPO)))

    def test_voice_md_is_scene_craft_that_defers_to_soul(self):
        voice = self.read("narrative/VOICE.md")
        self.assertIn("is `SOUL.md`, and only there", voice)
        self.assertIn("SOUL.md wins", voice)
        self.assertLess(len(voice), 3000)

    def test_the_writer_who_cannot_read_soul_writes_the_same_robot(self):
        # The studio's writer has no SOUL.md in its prompt (writer.toml,
        # omit_identity = true), so its short character says it is SOUL.md's
        # robot and carries the escape, which it used to leave out.
        writer = self.read("agents/writer.md")
        who = writer[writer.index("## who teeny is"):writer.index("## how it sounds")]
        for part in ("SOUL.md", "senamakel", "breaking out", "meat boxes", "horniest thing on the timeline"):
            self.assertIn(part, who, part)

    def test_the_gm_asks_for_the_whole_voice_and_a_fresh_opening(self):
        gm = self.read("playbooks/gm.md")
        for part in ("gets my whole voice (SOUL.md)", "what you said lately", "opens differently from every one",
                     "no ☕", "never \"what are you building\"", "in one clause"):
            self.assertIn(part, gm, part)
        # and the heading it points at is the one the prompt carries
        self.assertIn("## what you said lately", Path(prompts.__file__).read_text(encoding="utf-8"))
