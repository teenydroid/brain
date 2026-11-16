"""The round's Merkle tree, hashed exactly as the Solana Foundation Rewards
program (REWArDioXgQJ2fZKkfu9LCLjQfRwYWVVfsvcsR5hoXi) checks a claim
(docs/drops-schema.md, "Hashing"; web/src/lib/distributor.ts):

    leaf = keccak256(0x00 || keccak256(claimant(32) || u64le(total_amount) || schedule))
           schedule for Immediate = the single byte 0x00
    node = keccak256(min(a, b) || max(a, b))     a, b compared as bytes, no prefix

The tree pairs neighbours level by level in leaf order. A lone last node
moves up unchanged and adds no proof step; a one-leaf tree's root is the leaf
itself, with an empty proof. A proof is the sibling hashes from leaf to root.
"""

from __future__ import annotations

from typing import Sequence

from ..wallet import solana
from .keccak import keccak256

#: The Immediate vesting schedule: everything claimable at once.
IMMEDIATE = b"\x00"
U64_MAX = (1 << 64) - 1


def leaf_hash(claimant: bytes | str, total_amount: int) -> bytes:
    """One leaf: a claimant (32 bytes, or its base58 address) and its amount
    in the mint's smallest unit."""
    key = solana.b58decode(claimant) if isinstance(claimant, str) else claimant
    if key is None or len(key) != 32:
        raise ValueError("a claimant is a 32-byte Solana public key")
    if isinstance(total_amount, bool) or not isinstance(total_amount, int) or not 0 < total_amount <= U64_MAX:
        raise ValueError("an amount is a positive u64")
    inner = keccak256(key + total_amount.to_bytes(8, "little") + IMMEDIATE)
    return keccak256(b"\x00" + inner)


def node_hash(a: bytes, b: bytes) -> bytes:
    return keccak256(min(a, b) + max(a, b))


def build(leaves: Sequence[bytes]) -> tuple[bytes, list[list[bytes]]]:
    """(root, the proof of each leaf in order). At least one leaf."""
    if not leaves:
        raise ValueError("a tree needs at least one leaf")
    proofs: list[list[bytes]] = [[] for _ in leaves]
    # Which current-level node each leaf sits under.
    position = list(range(len(leaves)))
    level = list(leaves)
    while len(level) > 1:
        upper: list[bytes] = []
        for i in range(0, len(level), 2):
            upper.append(node_hash(level[i], level[i + 1]) if i + 1 < len(level) else level[i])
        for leaf, at in enumerate(position):
            sibling = at ^ 1
            if sibling < len(level):
                proofs[leaf].append(level[sibling])
            position[leaf] = at // 2
        level = upper
    return level[0], proofs


def verify(proof: Sequence[bytes], root: bytes, leaf: bytes) -> bool:
    """The program's check: fold the proof into the leaf and compare."""
    current = leaf
    for sibling in proof:
        current = node_hash(current, sibling)
    return current == root
