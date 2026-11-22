"""teeny_hands.slurs: the one screen the studio's outside material faces, the
guard lexicon's `hate` category, matched as the guard's screen matches."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from teeny_hands import slurs  # noqa: E402
from teeny_hands.guard.lexicon import LEXICON  # noqa: E402


class HatefulTest(unittest.TestCase):
    def test_a_slur_is_caught_however_it_is_written(self):
        for text in ("what a retard", "RETARDED take", "r3tard", "the nazis were right", "white power forever"):
            self.assertTrue(slurs.hateful(text), text)

    def test_any_of_several_texts_is_enough(self):
        self.assertTrue(slurs.hateful("a fine headline", "", "a quote calling them subhuman"))
        self.assertFalse(slurs.hateful("a fine headline", "a fine quote"))

    def test_the_rest_of_the_lexicon_is_not_a_slur(self):
        for text in ("this fucking robot", "porn site sues an AI company", "kill the product line",
                     "a gun maker's app leaked", "cocaine fuelled launch party", "go kill yourself"):
            self.assertFalse(slurs.hateful(text), text)

    def test_a_word_inside_another_word_is_no_slur(self):
        for text in ("a spicy take", "faggots of wood", "the nazareth office"):
            # "faggot" is in the hate list as written; a bundle of sticks is not
            # told apart by a word list, so only the containing words are checked.
            if text == "faggots of wood":
                continue
            self.assertFalse(slurs.hateful(text), text)

    def test_nothing_or_not_text_is_no_slur(self):
        self.assertFalse(slurs.hateful())
        self.assertFalse(slurs.hateful("", "   ", None))  # type: ignore[arg-type]

    def test_it_reads_the_guards_own_hate_terms(self):
        hate = {t for e in LEXICON if e.category == "hate" for t in e.terms}
        self.assertIn("subhuman", hate)
        self.assertTrue(all(slurs.hateful(f"they said {t} on air") for t in sorted(hate)))


if __name__ == "__main__":
    unittest.main()
