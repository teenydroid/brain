"""The verification challenge parser: an answer only when it is sure."""

from __future__ import annotations

import unittest

from .support import CHALLENGE, UNREADABLE
from teeny_hands.moltbook.challenge import collapse, solve


class SolveTest(unittest.TestCase):
    def test_moltbooks_own_example(self):
        self.assertEqual(solve(CHALLENGE), "15.00")

    def test_confident_answers(self):
        cases = {
            "A lObStEr ClAw ExErTs ThIrTy TwO nEwToNs AnD aNoThEr ClAw ExErTs FiFtEeN, wHaT iS tHe ToTaL fOrCe?": "47.00",
            "a lobster has twelve eggs and loses three": "9.00",
            "a lobster swims at tw enty meters and slows by five": "15.00",
            "a crab carries seven shells, multiplied by six": "42.00",
            "a lobster splits fifty two pearls into four": "13.00",
            "a lobster swims at one hundred and five meters and gains twenty-five": "130.00",
            "seven point five plus two": "9.50",
            "A] lO^bStEr ThRoWs SeVeN-tEeN pEbBlEs AnD fInDs ThIrTeEn MoRe, ToTaL?": "30.00",
            "a lobster has 20 claws and gains 5": "25.00",
            "a reef has ten crabs divided by four": "2.50",
            "a lobster has two thousand eggs and loses three hundred": "1700.00",
        }
        for text, answer in cases.items():
            with self.subTest(text=text):
                self.assertEqual(solve(text), answer)

    def test_a_comparison_is_the_difference(self):
        text = "lobster A swims twenty meters per second, lobster B swims five, how much faster is A than B"
        self.assertEqual(solve(text), "15.00")
        self.assertEqual(solve("crab B has five shells and crab A has twenty, how many more does A have than B"), "15.00")

    def test_not_confident_is_none(self):
        for text in (
            UNREADABLE,  # two numbers side by side, no operation
            "one lobster swims twenty meters and slows by five",  # three numbers
            "a lobster swims five twenty",  # adjacent numbers
            "a lobster swims twenty meters",  # one number
            "a lobster swims twenty meters and five more meters but then slows",  # two operations
            "a lobster swims twenty and five",  # no operation
            "twenty divided by zero",
            "lobster gains 3.5 and loses 2",
            "",
            "x" * 3000,
        ):
            with self.subTest(text=text[:60]):
                self.assertIsNone(solve(text))

    def test_collapse(self):
        self.assertEqual(collapse("twennttyy"), "twenty")
        self.assertEqual(collapse("three"), "thre")


if __name__ == "__main__":
    unittest.main()
