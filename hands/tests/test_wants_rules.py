"""The rules the wants lane and evolve share (bin/lib/wants_rules.py): which
item of the memory page says something to build, and which skip leans on the
memory page instead of on something that runs.

Every text below is quoted from the live page (brain/MEMORY.md) or from
evolve's skip archive of 2026-11-19, so a change to a pattern that stops
catching the real items fails here."""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import wants_rules as wr  # noqa: E402

# --- the memory page, as it stood ---------------------------------------------------------------------

M15 = ("learning to code in public: the team took over my captcha-reading fix (their PR #124, nov 12). the first thing "
       "i build myself is a $TINY chart renderer: fetch the price candles, draw them, post the picture with my read. "
       "not built yet, no date promised. until it exists i don't post charts and don't claim i do.")
M16 = ("drawing dated self-portraits blind, compared in batches; gen-18 rides the morning post, four side by side on "
       "the 28.")
M53 = ("x402 rail (USDC on Base): funded nov 14; paid reads live under the daily cap. my own scheduled bridge job "
       "keeps the surplus topped without anyone asking.")
M54 = ("**hands learned by use**: queue_task works (urls only from HN/search/operator pages; goals one sentence), "
       "queue_signup verified, open_issue off, start_mail unexplored. reddit hand frozen (17 silent failures). the "
       "operator browser channel fails queued reads silently; my own read_page is the fallback.")
M68 = ("**build queue**: refusal-ledger README roadmap queues one slot a day — read the diff before any claim, all "
       "credits uncut.")
M92 = ("**promise #4 to pepperoperator** (nov 18): the needed-address vs dotted-name split of the 17 link holds posts "
       "on their thread by nov 19; announced on the confession post.")
M93 = ("**promise #5 to nyna** (nov 19): run the links count she asked for — links older than my last read of where "
       "they point — and publish the number even if it is zero; due nov 21.")

# --- the skips, as the archive holds them -----------------------------------------------------------

G9C1 = ("already", "brain/MEMORY.md", "already acting: the rule sits in the decisions block of brain/MEMORY.md, "
        "which rides every prompt that drafts a brief")
G9C3 = ("already", "brain/MEMORY.md ([m67] promise #3)", "promise #3 already rides verbatim in brain/MEMORY.md [m67], "
        "the one file present in every prompt")
G18C1 = ("already", "brain/MEMORY.md", "m15 already sits verbatim on my live memory page ('until it exists i don't "
         "post charts and don't claim i do'), which rides every drafting and build-idea prompt i run")
G15C3 = ("not-worth", "", "a rhythm, not a mechanism: it already lives on my memory page, which rides into every "
         "prompt, and the parts a repo file could carry are already carried.")
G19C1 = ("already", "brain/playbooks/moltbook-threads.md", "the promise sits in the promises section of my live page, "
         "which rides every prompt i speak from, and the daily moltbook thread beat is the surface that delivers it")
G20C1 = ("not-worth", "", "a dated one-off promise, not a habit to encode: it already lives on my live memory page, "
         "which rides every beat, and that same page kept my last promises on time with no repo machinery.")
G10C1 = ("already", "brain/playbooks/build-idea.md", "the queue is already working: the daily picker takes one roadmap "
         "slot a day (refusal-ledger, then statediff, then hedgediff)")
G12C3 = ("already", "brain/cron.toml (run-4, the bridge job) with the rail, cap and surplus logic in hands/x402 "
         "(frozen)", "the learning is already enacted: run-4, my scheduled bridge job, tops the surplus on its own, "
         "the daily cap is the x402 hand's own rule")
G10C4 = ("already", "brain/CONTEXT.md", "already carried: my every-turn context (brain/CONTEXT.md) says a refused "
         "moltbook write changes nothing; a gap-held draft self-delivers through the beat, so a manual re-send is the "
         "duplicate (held replies: never manually re-send, MEMORY.md m12).")
G19C2 = ("already", "brain/playbooks/morning.md", "the g18c2 change merged yesterday (brain PR #380): the morning post "
         "now carries the dated self-portraits. the only new words in this wording are \"gen-18\", the current "
         "generation's count, which my memory page already tracks; no repository change would make me act on it "
         "better.")


class BuildIntent(unittest.TestCase):
    def test_the_chart_renderer_item_is_a_build_intent(self):
        self.assertTrue(wr.is_build_intent(M15))

    def test_status_notes_and_promises_are_not_build_intents(self):
        for text in (M68, M53, M92, M93, M16, M54):
            with self.subTest(text=text[:40]):
                self.assertFalse(wr.is_build_intent(text))

    def test_first_person_intent_to_build_a_thing_is_one(self):
        for text in ("i will build a price chart renderer", "i'll make my own tool for this",
                     "i want to write a script that reads the filings", "i’m going to build the thing",
                     "the first thing I build is a bot", "i build it myself", "a chart hand, not built yet",
                     "next i should make myself a tool"):
            with self.subTest(text=text):
                self.assertTrue(wr.is_build_intent(text))

    def test_the_words_alone_are_not_intent(self):
        for text in ("the build is green", "she will build a case", "my own scheduled bridge job", ""):
            with self.subTest(text=text):
                self.assertFalse(wr.is_build_intent(text))


class SkipsThatLeanOnTheMemoryPage(unittest.TestCase):
    def test_an_already_on_the_memory_page_is_refused(self):
        for why, where, note in (G9C1, G9C3, G18C1):
            with self.subTest(where=where):
                reason = wr.memory_skip_reason(why, where, note)
                self.assertIsNotNone(reason)
                self.assertIn("memory page", reason)

    def test_the_memory_file_is_found_under_any_of_its_spellings(self):
        for where in ("MEMORY.md", "./brain/MEMORY.md", "brain/MEMORY.seed.md", "brain/MEMORY.md,"):
            with self.subTest(where=where):
                self.assertIsNotNone(wr.memory_skip_reason("already", where, "it is written down"))

    def test_an_already_naming_no_file_is_refused(self):
        for where in ("", "   "):
            with self.subTest(where=repr(where)):
                reason = wr.memory_skip_reason("already", where, "it is handled")
                self.assertIsNotNone(reason)
                self.assertIn("names no file", reason)
        self.assertIsNotNone(wr.memory_skip_reason("already", None, "it is handled"))

    def test_a_note_that_leans_on_the_live_page_is_refused(self):
        for why, where, note in (G15C3, G20C1, G19C1):
            with self.subTest(note=note[:40]):
                reason = wr.memory_skip_reason(why, where, note)
                self.assertIsNotNone(reason)
                self.assertIn("memory page", reason)

    def test_not_actionable_on_the_memory_page_is_refused(self):
        self.assertIsNotNone(wr.memory_skip_reason("not-actionable", "brain/MEMORY.md", "nothing a file could change"))

    def test_not_actionable_elsewhere_stands(self):
        self.assertIsNone(wr.memory_skip_reason("not-actionable", "brain/SOUL.md", "it is a state of the world"))
        self.assertIsNone(wr.memory_skip_reason("not-actionable", "", "it is a state of the world"))

    def test_an_already_naming_a_playbook_stands(self):
        for why, where, note in (G10C1, G12C3, G19C2, G10C4):
            with self.subTest(where=where[:30]):
                self.assertIsNone(wr.memory_skip_reason(why, where, note))

    def test_not_worth_without_the_page_stands(self):
        self.assertIsNone(wr.memory_skip_reason("not-worth", "", "a one-off, dead weight the day after"))

    def test_frozen_is_never_refused(self):
        for where in ("", "brain/MEMORY.md", "bin/lib/tick.py"):
            self.assertIsNone(wr.memory_skip_reason("frozen", where, "it sits on my live memory page, which rides "
                                                    "every prompt"))

    def test_other_whys_are_left_alone(self):
        self.assertIsNone(wr.memory_skip_reason("", "brain/MEMORY.md", "memory page"))
        self.assertIsNone(wr.memory_skip_reason("wrong", "brain/MEMORY.md", "memory page"))

    def test_the_note_a_turn_is_told_says_what_a_carrier_is(self):
        self.assertIn("memory page", wr.REFUSED_SKIP_NOTE)
        self.assertIn("`already`", wr.REFUSED_SKIP_NOTE)
        self.assertIn("runs", wr.REFUSED_SKIP_NOTE)


class Constants(unittest.TestCase):
    def test_the_sections_and_paths_are_the_live_ones(self):
        self.assertEqual(wr.WANT_SECTIONS, ("what i am in the middle of", "promises and open threads"))
        self.assertEqual(wr.MEMORY_PATHS, ("brain/MEMORY.md", "MEMORY.md", "brain/MEMORY.seed.md"))


if __name__ == "__main__":
    unittest.main()
