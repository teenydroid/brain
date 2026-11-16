"""Keccak-256, the claim tree and the draw order, against vectors computed
elsewhere: Keccak from @noble/hashes and the known empty/"abc" digests; the
tree from the Rewards program's own Rust (web/src/lib/distributor.test.ts);
the draw from the /rewards page's test (web/src/lib/drops.test.ts)."""

from __future__ import annotations

import hashlib
import unittest

from x.support import SECRETS  # noqa: F401 - puts hands/lib on sys.path
from teeny_hands.giveaway import draw, merkle
from teeny_hands.giveaway.keccak import keccak256

from giveaway.support import DEADLINE, FakeSolana


class KeccakTest(unittest.TestCase):
    VECTORS = {
        b"": "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        b"abc": "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
        # From @noble/hashes keccak_256: across one rate block (136 bytes) and over it.
        b"\x02" * 135: "8f6efb490107f1572ee022faa24703196f4327126d5f26cd173934b20cd1c096",
        b"\x01" * 136: "782165b181823a770bd1c5d6c0b5e7c5f1b53e91da289c27b3622c49fcb91402",
        b"a" * 200: "96ea54061def936c4be90b518992fdc6f12f535068a256229aca54267b4d084d",
    }

    def test_known_vectors(self):
        for data, digest in self.VECTORS.items():
            self.assertEqual(keccak256(data).hex(), digest, msg=len(data))

    def test_it_is_not_sha3(self):
        self.assertNotEqual(keccak256(b"").hex(), hashlib.sha3_256(b"").hexdigest())


class MerkleTest(unittest.TestCase):
    """Claimants [1;32], [2;32], [3;32] with 1000000, 2500000 and 42, all
    Immediate: the program's Rust gives these leaves, root and proofs."""

    LEAVES = [
        "ba9c9c52055de034f280764cb0c726ce9764a390491e19e648f2c7e24d601f45",
        "c4f930546c689f0761e0f3de34074bcd93e31deb2ab42dce8f886bf87827819c",
        "65a5b49cad9bacef0726790697c24cee61840b92af13227fd059ae2635f2882c",
    ]
    ROOT = "47dfbe7fa0962b4e379252f841e5d0bec0bb3632a3478e6754fc2196b8bb7bcb"
    PROOFS = [
        [LEAVES[1], LEAVES[2]],
        [LEAVES[0], LEAVES[2]],
        ["a0a99602f95f18470af71b317e28521c65bf70155742d686da1c67b63a379cf1"],
    ]
    AMOUNTS = [1_000_000, 2_500_000, 42]

    def leaves(self):
        return [merkle.leaf_hash(bytes([i + 1]) * 32, amount) for i, amount in enumerate(self.AMOUNTS)]

    def test_leaves_hash_as_the_program_does(self):
        self.assertEqual([leaf.hex() for leaf in self.leaves()], self.LEAVES)

    def test_root_and_proofs_match_the_program_with_the_odd_leaf_promoted(self):
        root, proofs = merkle.build(self.leaves())
        self.assertEqual(root.hex(), self.ROOT)
        self.assertEqual([[p.hex() for p in proof] for proof in proofs], self.PROOFS)

    def test_a_base58_claimant_is_the_same_key(self):
        from teeny_hands.wallet import solana

        self.assertEqual(merkle.leaf_hash(solana.b58encode(bytes([1]) * 32), 1_000_000).hex(), self.LEAVES[0])

    def test_every_leaf_of_any_tree_proves_and_a_wrong_one_does_not(self):
        for size in (1, 2, 3, 5, 8, 13):
            leaves = [merkle.leaf_hash(hashlib.sha256(bytes([size, i])).digest(), i + 1) for i in range(size)]
            root, proofs = merkle.build(leaves)
            for leaf, proof in zip(leaves, proofs):
                self.assertTrue(merkle.verify(proof, root, leaf))
            if size == 1:
                self.assertEqual((root, proofs), (leaves[0], [[]]))
            wrong = merkle.leaf_hash(hashlib.sha256(b"other").digest(), 1)
            self.assertFalse(merkle.verify(proofs[0], root, wrong))

    def test_amounts_are_positive_u64(self):
        for amount in (0, -1, 1 << 64, True):
            with self.assertRaises(ValueError):
                merkle.leaf_hash(bytes(32), amount)


class DrawOrderTest(unittest.TestCase):
    BLOCKHASH = "4sGjMW1sUnHzSxGspuhpqLDx6wiyjNtZAMdL4VZHirAn"

    def test_the_same_seed_and_order_as_the_page(self):
        seed = draw.seed_hash(7, self.BLOCKHASH)
        self.assertEqual(seed, "b52000ab6bf32a45edcbd06d2b3167774efb1fbdd0a53cc8d89f68ffbd5a4aa1")
        self.assertEqual(draw.order(seed, ["alice", "bob", "carol", "dave", "eve"]), ["carol", "alice", "bob", "dave", "eve"])
        self.assertEqual(draw.order(seed, ["Eve", "ALICE"]), ["alice", "eve"])

    def test_removing_one_handle_never_reorders_the_rest(self):
        seed = draw.seed_hash(7, self.BLOCKHASH)
        full = draw.order(seed, ["alice", "bob", "carol", "dave", "eve"])
        self.assertEqual(draw.order(seed, [h for h in full if h != "alice"]), [h for h in full if h != "alice"])

    def test_the_seed_slot_is_the_first_finalized_block_at_or_after_the_deadline(self):
        fake = FakeSolana()
        original = draw.rpc
        draw.rpc = fake
        self.addCleanup(setattr, draw, "rpc", original)
        got = draw.seed_slot(DEADLINE)
        self.assertEqual(got, {"slot": fake.expected(), "blockhash": fake.blockhash(fake.expected())})
        self.assertNotIn("sendTransaction", fake.calls)

    def test_no_seed_before_a_block_past_the_deadline_is_final(self):
        fake = FakeSolana(tip=12000)
        original = draw.rpc
        draw.rpc = fake
        self.addCleanup(setattr, draw, "rpc", original)
        from teeny_hands import hand

        with self.assertRaises(hand.Refused) as caught:
            draw.seed_slot(DEADLINE)
        self.assertEqual(caught.exception.detail["rule"], "seed_not_final")


if __name__ == "__main__":
    unittest.main()
