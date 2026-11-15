# Tiny Drops: the diary contract

The giveaway hand (`hands/giveaway`) publishes every round to the public diary
repository (`teenydroid/diary`) under `giveaways/`. The `/rewards` page reads
those files and nothing else from Teeny's side. This document and the types in
`web/src/lib/drops.ts` describe the same contract, so change them together.

The page treats the repository as public input. It checks every field again,
and it does not show a round whose file doesn't parse. It never fills a gap
with a default number.

## `giveaways/index.json`

```json
{ "rounds": [ { "id": 3, "tag": "drop #3", "status": "open", "deadline": "2026-11-20T18:00:00Z" } ] }
```

| Field | Type | Rule |
| --- | --- | --- |
| `id` | integer ≥ 1 | unique |
| `tag` | string | exactly `"drop #<id>"` |
| `status` | `"open"` \| `"drawn"` \| `"settled"` \| `"clawed_back"` | |
| `deadline` | ISO 8601 UTC | |

The page reads the newest 12 rounds.

## `giveaways/drop-<id>.json`

| Field | Type | Rule |
| --- | --- | --- |
| `id`, `tag`, `status`, `deadline` | as in the index | |
| `format` | `"drop"` \| `"milestone"` \| `"challenge"` | |
| `pool_tiny` | decimal string, whole $TINY (`"250000"`, `"1234.5"`) | no more fractional digits than `decimals` |
| `winners` | integer | how many random winners |
| `best_reply_tiny` | decimal string | `"0"` for none, never more than the pool |
| `prompt` | string ≤ 1000 chars | the hook, shown as the round's headline |
| `post_id` | string of digits | the round post on X |
| `post_url` | `https://x.com/<user>/status/<post_id>` | must end in `post_id` |
| `cluster` | `"devnet"` \| `"mainnet"` | |
| `mint` | base58 pubkey | |
| `decimals` | integer 0–18 | the mint's decimals |
| `token_program` | `TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA` \| `TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb` | the mint's owner; $TINY is Token-2022 |
| `program_id` | base58 pubkey | the Rewards program, `REWArDioXgQJ2fZKkfu9LCLjQfRwYWVVfsvcsR5hoXi` |
| `authority` | base58 \| null | required once `settled`; the giveaway wallet (index 1) that funds the round and claws it back |
| `distribution_seed` | base58 \| null | required once `settled`; the throwaway keypair's address that seeds the distribution PDA |
| `distributor` | base58 \| null | required once `settled`; must equal the distribution PDA below |
| `vault` | base58 \| null | required once `settled`; the distribution's associated token account under `token_program` |
| `merkle_root` | 64 hex \| null | required once `settled`; must equal the root stored on chain |
| `clawback_at` | ISO \| null | required once `settled`; the distribution's `clawback_ts`. The page offers claims until then |
| `seed` | `{slot, blockhash, sha256}` \| null | required once the status is past `open` |
| `entrants` | `[{handle, eligible, reason}]` | handle lowercase without `@`, one per account |
| `draw_order` | `[handle]` | see "The draw" |
| `claims` | `[{index, handle, kind, address, amount_atomic, proof}]` | see "Claims" |
| `txs` | `{create?, fund?, clawback?}` | Solana signatures |

### The draw

```
seed.sha256 = hex(sha256(utf8("<id>:<blockhash>")))
```

`blockhash` is the base58 blockhash of `seed.slot`, which is the first
finalized slot after the deadline.

```
draw_order = the handles of every entrant with eligible = true,
             sorted ascending by hex(sha256(bytes.fromhex(seed.sha256) + utf8(handle.lower())))
             (ties, which shouldn't happen, broken by handle)
```

The `random` winners are the first `winners` handles of `draw_order`. A
candidate that fails a late check, such as the follow lookup, becomes
`eligible: false` with a reason and drops out of `draw_order`. Because the
order is a sort by a per-handle key, removing one handle never reorders the
others. The page re-runs all three checks from the file and shows the results.

### Claims

There is one claim per address. A person who wins twice in a round gets a
single leaf holding the sum, and its `kind` names the best-reply prize if one
of the wins was that.

- `amount_atomic` is the u64 amount in the mint's smallest unit, as a decimal
  string, and must be greater than 0.
- The sum of all claims must be no more than the pool.
- `proof` is the list of sibling hashes from leaf to root, as 64-hex strings.
- Nothing is locked: the on-chain `amount_locked` is always 0.

## The program: Jito merkle-distributor

These facts were verified from github.com/jito-foundation/distributor at
commit `2cf79f63973a79e6354871dc9355a63aa1d15139`, and against the live
mainnet JTO airdrop.

- **Program id:** `mERKcfxMC5SqJn4Ld4BUris3WKZZ1ojjWJ3A3J5CKxv`. It is
  executable on mainnet-beta and not deployed on devnet, so a devnet round
  names its own deployment in `program_id`. That deployment has to be built
  with its own `declare_id!`.
- **Distributor PDA:** `["MerkleDistributor", mint, u64le(version)]`. Tiny
  Drops uses version = round id.
- **Claim status PDA:** `["ClaimStatus", claimant, distributor]`.
- **Vault:** the distributor's associated token account for the mint, under
  the classic Token program.
- **Leaf:**
  `sha256(0x00 || sha256(claimant(32) || u64le(amount_unlocked) || u64le(amount_locked)))`.
- **Inner node:** `sha256(0x01 || min(a,b) || max(a,b))`, comparing the two
  32-byte hashes lexicographically.
- **Tree shape:** the tree is built level by level in leaf order, and a lone
  last node is paired with itself (`merkle-tree/src/merkle_tree.rs`,
  `sorted_hashes = true`).
- **`new_claim` data:** `4eb1627bd215bb53` (`sha256("global:new_claim")[:8]`),
  then `u64le amount_unlocked`, `u64le amount_locked`, `u32le len(proof)` and
  `32 bytes × len`.
- **`new_claim` accounts:** distributor (w), claim_status (w), from = vault (w),
  to = claimant's token account (w), claimant (w, signer), token program,
  system program.

The claimant pays the rent for the claim status account (64 bytes, about
0.0014 SOL) and, if they don't have one yet, for their associated token
account (about 0.002 SOL).

**The program takes the classic SPL Token program only**
(`Program<'info, Token>`). $TINY
(`2AF7CqwieUjUPALL7icuZtL3X7wENdjUjGBMmfV2pump`) is a Token-2022 mint, so
this program cannot pay it out as deployed. Before the first $TINY round, PR 5
has to choose a distributor that supports Token-2022.
