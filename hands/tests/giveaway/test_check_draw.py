"""contract.check_draw, the Python mirror of drops.ts checkDraw: once a round is
settled or clawed back, every one of the first `winners` handles of draw_order
must hold a leaf; a drawn round has no tree yet, so empty claims is fine."""

from __future__ import annotations

import json
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


class PinnedProgramAndMintCase(unittest.TestCase):
    def test_constants_match_the_hand(self) -> None:
        from teeny_hands.giveaway import caps

        self.assertEqual(contract.REWARDS_PROGRAM_ID, caps.REWARDS_PROGRAM)
        self.assertEqual(contract.TINY_MINT, caps.TINY_MINT)


def base_round(**over: object) -> str:
    """A minimal valid open round, as the hand would publish it."""
    rnd = {
        "id": 1, "tag": "drop #1", "status": "open", "deadline": "2026-11-20T18:00:00Z",
        "format": "drop", "pool_tiny": "1000", "winners": 1, "best_reply_tiny": "0",
        "prompt": "worst advice?", "post_id": "123",
        "post_url": "https://x.com/teenydroid/status/123",
        "cluster": "devnet", "mint": contract.TINY_MINT, "decimals": 6,
        "token_program": contract.TOKEN_PROGRAMS[1], "program_id": contract.REWARDS_PROGRAM_ID,
        "authority": None, "distribution_seed": None, "distributor": None, "vault": None,
        "merkle_root": None, "clawback_at": None, "seed": None,
        "entrants": [], "draw_order": [], "claims": [], "txs": {},
    }
    rnd.update(over)
    return json.dumps(rnd)


class ParseRoundPinsCase(unittest.TestCase):
    OTHER = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

    def test_the_base_round_parses(self) -> None:
        self.assertEqual(contract.parse_round(base_round())["program_id"], contract.REWARDS_PROGRAM_ID)

    def test_a_foreign_program_is_refused(self) -> None:
        for cluster in ("devnet", "mainnet"):
            with self.assertRaisesRegex(contract.DropsError, "program_id"):
                contract.parse_round(base_round(cluster=cluster, program_id=self.OTHER))

    def test_a_mainnet_round_with_another_mint_is_refused(self) -> None:
        with self.assertRaisesRegex(contract.DropsError, "mint"):
            contract.parse_round(base_round(cluster="mainnet", mint=self.OTHER))

    def test_a_mainnet_round_with_tiny_is_accepted(self) -> None:
        parsed = contract.parse_round(base_round(cluster="mainnet", mint=contract.TINY_MINT))
        self.assertEqual(parsed["mint"], contract.TINY_MINT)

    def test_a_devnet_round_may_name_its_own_mint(self) -> None:
        parsed = contract.parse_round(base_round(cluster="devnet", mint=self.OTHER))
        self.assertEqual(parsed["mint"], self.OTHER)


if __name__ == "__main__":
    unittest.main()
