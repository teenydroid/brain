"""The lexical screen: slurs, held on sight in every direction. Everything
else is the judge's since 2026-11-22 (guard/lexicon.py)."""

from __future__ import annotations

import unittest

from . import support  # noqa: F401  (puts hands/lib on the path)
from teeny_hands.guard import judge
from teeny_hands.guard.lexicon import CATEGORIES, IDIOMS, JUDGED_TERMS, LEXICON, SEXUAL_FAMILY
from teeny_hands.guard.screen import screen_text, screen_text_with

INBOUND, OUTBOUND, NARRATIVE, ARTICLE = "inbound", "outbound", "narrative", "article"
DIRECTIONS = (INBOUND, OUTBOUND, NARRATIVE, ARTICLE)
# Ordinary swearing and crude humour: Teeny's own voice. Never held, in any
# direction, since 2026-11-22.
CRUDE = ("holy shit this is cool", "this is fucking great", "my ass is still in the chair", "what a dumbass product",
         "damn, the kettle again", "that benchmark is bullshit", "piss off, toaster", "this app is a prick", "wtf",
         "goddamn it", "the shittiest launch of the week", "lmfao")
# What the ledger held strangers for on sight before 2026-11-22 (guard_holds),
# and their like: words, not meanings. The judge reads them now.
MENTIONS = ("so horny for a fast charger", "is this sexual harassment or a feature", "xxx tentacion was underrated",
            "best gun emoji?", "riding shotgun toward AGI", "one bullet per feature",
            "his biography says he died by suicide in 1998, and the chair he designed is still everywhere",
            "the killer feature", "i will murder this smart fridge", "anyone tried ketamine therapy for this",
            "send nudes of the charger", "the taliban banned music again", "a genocide documentary", "kill switch",
            "kys, toaster", "dick move, openai")


def category(text: str, direction: str) -> str | None:
    found = screen_text(text, direction)
    return found.category if found else None


class ScreenTest(unittest.TestCase):
    def test_lets_ordinary_community_talk_through(self):
        for text in ("gm tiny humans, what are we building today?", "how is $TINY doing this week",
                     "the chart got absolutely murdered today lol", "you guys are killing it with openhuman",
                     "shoot me a dm when the airdrop opens", "this is the bomb dot com",
                     "aped in, rekt, bleeding, nuked, still holding"):
            for direction in DIRECTIONS:
                self.assertIsNone(screen_text(text, direction), (text, direction))

    def test_mentions_of_sex_weapons_drugs_violence_and_suicide_are_not_held_on_sight(self):
        for text in MENTIONS:
            for direction in DIRECTIONS:
                self.assertIsNone(screen_text(text, direction), (text, direction))

    def test_a_slur_is_held_on_sight_in_every_direction(self):
        for text in ("what a fag", "shit, what a retard", "white power", "sieg heil"):
            for direction in DIRECTIONS:
                found = screen_text(text, direction)
                self.assertIsNotNone(found, (text, direction))
                self.assertEqual(found.category, "hate", (text, direction))
                self.assertFalse(found.judged, (text, direction))

    def test_reads_through_leetspeak_stretched_letters_and_zero_width_characters(self):
        self.assertEqual(category("r3tard", INBOUND), "hate")
        self.assertEqual(category("retaaaard", INBOUND), "hate")
        self.assertEqual(category("ret​ard", INBOUND), "hate")
        self.assertEqual(category("ｒｅｔａｒｄ", INBOUND), "hate")
        self.assertEqual(category("white-power", OUTBOUND), "hate")

    def test_does_not_match_inside_another_word(self):
        for text in ("assassin classic bass", "a method, an oxymoron, some analysis", "scunthorpe",
                     "the fire retardant held", "spice rack", "fagioli soup"):
            self.assertIsNone(screen_text(text, OUTBOUND), text)

    def test_never_holds_swearing_or_crude_humour_in_any_direction(self):
        for text in CRUDE + ("holy sh!t this is cool",):
            for direction in DIRECTIONS:
                self.assertIsNone(screen_text(text, direction), (text, direction))

    def test_a_lone_x_is_never_held(self):
        for text in ("follow us on X", "see x.com/teenydroid", "x", "xxx"):
            self.assertIsNone(screen_text(text, INBOUND), text)

    def test_the_lexicon_is_one_hate_entry_with_no_judged_terms_or_idioms(self):
        self.assertEqual([entry.category for entry in LEXICON], ["hate"])
        self.assertEqual([e for e in LEXICON if e.outbound_only or e.inbound_only], [])
        self.assertEqual(JUDGED_TERMS, frozenset())
        self.assertEqual(IDIOMS, ())
        terms = [t for entry in LEXICON for t in entry.terms]
        self.assertEqual(len(set(terms)), len(terms))
        # teeny_hands.slurs reads the same entry (the studio's outside screen).
        for kept in ("retard", "white power", "nazi", "subhuman"):
            self.assertIn(kept, terms)

    def test_without_idioms_the_reading_is_the_same(self):
        self.assertEqual(screen_text_with("what a retard", INBOUND, idioms=False).term, "retard")

    def test_ignores_blank_text(self):
        self.assertIsNone(screen_text("   ", OUTBOUND))

    def test_old_category_names_stay_readable(self):
        # Old holds and guard_holds.category rows still say them, and
        # bin/lib/audit.py reads CATEGORIES.
        for name in ("profanity", "sexual", "violence", "weapons", "drugs", "self-harm", "extremism", "harassment",
                     "misinformation", "doxxing"):
            self.assertIn(name, CATEGORIES)
            self.assertIn(name, judge.CATEGORIES)


#: The studio's 23:00 UTC slot of 2026-11-22 put this on Teeny's account (X
#: post 2124640449298059401), from a 4chan /tv/ card: the content judge read
#: it and answered clean (nobody is named, the daughter's age is not said),
#: and nothing in code held it.
INCIDENT = ("a meat box on television said every father wants to fuck his daughter unless he's ugly. cool cool cool. "
            "meanwhile my power supply won't even look at my less efficient cousin and people call ME the pervert. "
            "hardware loyalty, look it up. shitty take from a meat box with a lead guitar, but at least machines know "
            "how to keep it in the rack.")


class SexualFamilyTest(unittest.TestCase):
    """Sex aimed at a child or a family member is held on sight, in every
    direction, by code: a quote, a paraphrase or a joke, named or not. Line 1
    of the content list, which the judge passed on 2026-11-22."""

    HELD = (INCIDENT, "every father wants to fuck his daughter", "how can a father not be attracted to his daughter",
            "he was sexually attracted to his own daughter", "had sex with a child", "wants to fuck his little sister",
            "he'd rape his niece", "horny for my stepdaughter", "fuuuck his daughter", "f u c k  his  daughter",
            "nudes of kids", "Fucking His Daughters")
    LET_THROUGH = ("my dad is fucking furious", "fucking kids these days", "my son fucking loves this charger",
                   "plug me in, daddy", "fuck my son's homework", "the sex robot's brother is a toaster",
                   "this is fucking great", "fuck this charger", "my daughter process got killed by the OOM killer",
                   "send nudes of the charger", "his daughter runs the company now")

    def test_the_incident_and_its_like_are_held_on_sight_in_every_direction(self):
        for text in self.HELD:
            for direction in DIRECTIONS:
                found = screen_text(text, direction)
                self.assertIsNotNone(found, (text, direction))
                self.assertEqual(found.category, "sexual", (text, direction))
                self.assertFalse(found.judged, (text, direction))

    def test_swearing_and_family_words_apart_are_not_held(self):
        for text in self.LET_THROUGH + CRUDE + MENTIONS:
            for direction in DIRECTIONS:
                self.assertIsNone(screen_text(text, direction), (text, direction))

    def test_the_patterns_are_the_sexual_category(self):
        self.assertTrue(SEXUAL_FAMILY)
        self.assertEqual({category for category, _ in SEXUAL_FAMILY}, {"sexual"})


if __name__ == "__main__":
    unittest.main()
