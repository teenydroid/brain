"""The on-chain step of a round: the interface, and which implementation runs.

`settle` publishes the round's tree, then asks for its merkle distribution
on the Solana Foundation Rewards program; `clawback` asks for it to be
closed once `clawback_at` has passed. Both are signed by the giveaway
wallet, Solana index 1 (`m/44'/501'/1'/0'`), inside the core: the key never
leaves it (docs/drops-schema.md, "The program"; the plan's PR 5).

The implementation is onchain.CoreChain (the core signs with Solana index 1
under `[wallet_policy.accounts.solana_1]`; the spine builds the transaction).
`NOT_READY` refuses both calls with rule `chain_not_ready`, as onchain does
when there is no spine binary: `settle` then leaves the round `drawn`, its
JSON published with `distributor` and `vault` null, and nothing is signed.
A test puts a fake (or NOT_READY) in front with `use`.

The interface:

    create_distribution(plan) -> {"distribution_seed": base58, "distributor": base58,
                                  "vault": base58, "txs": {"create": sig, "fund"?: sig}}
        plan = {"round_id": int, "cluster": "devnet"|"mainnet", "mint": base58,
                "decimals": int, "token_program": base58, "program_id": base58,
                "authority": base58 (index 1), "merkle_root": 64 hex,
                "total_amount": int (atomic; fund the vault with exactly this),
                "clawback_ts": int (unix seconds), "leaves": int}
        It must be safe to call again for the same round_id after a failure
        or a crash: a distribution already created for it (found by
        round_id, e.g. a seed keypair kept per round) is returned, never
        created twice, and an outcome it cannot know raises hand.HandError
        with "in_doubt" in the message, which the hand never retries
        blindly. It refuses (hand.Refused) a plan whose root, total or
        vault the prepared transaction does not match.

    close_distribution(plan) -> {"tx": sig}
        plan = {"round_id", "cluster", "mint", "token_program", "program_id",
                "authority", "distributor", "vault", "distribution_seed"}
        Only after clawback_at; the program refuses earlier.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from .. import hand

Json = dict[str, Any]


class Chain(Protocol):
    def create_distribution(self, plan: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def close_distribution(self, plan: Mapping[str, Any]) -> Mapping[str, Any]: ...


class NotReady:
    def _refuse(self) -> Mapping[str, Any]:
        raise hand.Refused(
            "the on-chain step is not available here; nothing was signed",
            rule="chain_not_ready",
        )

    def create_distribution(self, plan: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._refuse()

    def close_distribution(self, plan: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._refuse()


NOT_READY: Chain = NotReady()
_impl: Chain | None = None
_default: Chain | None = None


def use(impl: Chain | None) -> None:
    """Puts `impl` in front of the chain (None: back to the default)."""
    global _impl
    _impl = impl


def _chain() -> Chain:
    global _default
    if _impl is not None:
        return _impl
    if _default is None:
        from .onchain import CoreChain

        _default = CoreChain()
    return _default


def create_distribution(plan: Mapping[str, Any]) -> Mapping[str, Any]:
    return _chain().create_distribution(plan)


def close_distribution(plan: Mapping[str, Any]) -> Mapping[str, Any]:
    return _chain().close_distribution(plan)
