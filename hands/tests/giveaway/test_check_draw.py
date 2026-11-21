"""contract.check_draw, the Python mirror of drops.ts checkDraw: once a round is
settled or clawed back, every one of the first `winners` handles of draw_order
must hold a leaf; a drawn round has no tree yet, so empty claims is fine."""

from __future__ import annotations

import unittest

from giveaway import contract
from giveaway import support  # noqa: F401 - puts hands/lib on sys.path
from teeny_hands.giveaway import draw

HANDLES = ["alice", "bob", "carol", "dave"]


def make_round(status: str, claim_handles: list[str], kinds: dict[str, str] | None = None) -> dict:
    blockhash = "11111111111111111111111111111111"
    sha = draw.seed_hash(3, blockhash)
    kinds = kinds or {}
    return {
        "id": 3,
        "status": status,
        "winners": 2,
        "seed": {"slot": 1, "blockhash": blockhash, "sha256": sha},
        "entrants": [{"handle": h, "eligible": True, "reason": "ok"} for h in HANDLES],
        "draw_order": draw.order(sha, HANDLES),
        "claims": [{"handle": h, "kind": kinds.get(h, "random")} for h in claim_handles],
    }


class CheckDrawWinnersCase(unittest.TestCase):
    def first_two(self) -> list[str]:
        return make_round("settled", [])["draw_order"][:2]

    def test_settled_with_every_drawn_winner_passes(self) -> None:
        winners = self.first_two()
        self.assertTrue(contract.check_draw(make_round("settled", winners))["winners"])

    def test_settled_with_a_drawn_winner_missing_fails(self) -> None:
        winners = self.first_two()
        for status in ("settled", "clawed_back"):
            self.assertFalse(contract.check_draw(make_round(status, winners[:1]))["winners"])
            self.assertFalse(contract.check_draw(make_round(status, []))["winners"])

    def test_double_winner_with_a_best_reply_leaf_still_counts(self) -> None:
        winners = self.first_two()
        rnd = make_round("settled", winners, kinds={winners[0]: "best_reply"})
        self.assertTrue(contract.check_draw(rnd)["winners"])

    def test_drawn_round_without_a_tree_passes(self) -> None:
        self.assertTrue(contract.check_draw(make_round("drawn", []))["winners"])

    def test_leaf_outside_the_first_winners_still_fails(self) -> None:
        extra = make_round("settled", [])["draw_order"][2]
        winners = self.first_two()
        self.assertFalse(contract.check_draw(make_round("settled", winners + [extra]))["winners"])


if __name__ == "__main__":
    unittest.main()
