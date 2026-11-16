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
- Every leaf uses the Immediate vesting schedule, so the whole amount is
  claimable at once.

## The program: Solana Foundation Rewards

Rounds are paid through the merkle distribution of the Solana Foundation
Rewards program (github.com/solana-foundation/rewards, MIT). It supports both
SPL Token and Token-2022, so it can pay out $TINY. `web/src/lib/distributor.ts`
encodes it by hand. Its tests check byte for byte against vectors computed in
Rust, using the hashing copied verbatim from the program's own test utilities.

### Which code is live

- **Audited code.** OtterSec audited through commit
  `aa1cfd9276375e44e57d1917d110ff095fb6d475` (`audits/AUDIT_STATUS.md`).
- **The two deployments.** Program id `REWArDioXgQJ2fZKkfu9LCLjQfRwYWVVfsvcsR5hoXi`
  is executable on mainnet and on devnet, and the two clusters run different
  binaries. Mainnet was last upgraded at slot 422571636 (2026-05-27), with
  upgrade authority `DXtFpbPjcn2hxPnw79x1Pfoj35vXh5AsWBkS37YnXMVv`. Devnet was
  last upgraded on 2026-06-02.
- **Not verified.** The mainnet program is **not verified**.
  - The OtterSec registry reports `is_verified: false`. Its on-chain hash is
    `96a2835c…d88d` (sha256 of the program's code, with trailing zeros
    trimmed). That is a 170,617-byte binary.
  - The last verified build was of `758aa7a` (2026-05-19), with hash
    `449b6b27…be1c`. A reproduction of that build matched it exactly, so the
    method below works.
  - Builds tried with the same method:

    | Commit | Build image | Size (bytes) | Hash |
    | --- | --- | --- | --- |
    | `5f2746d` | 3.1.10 | 179,801 | `4e7f4e77…` |
    | `5f2746d` | 4.0.0 | 172,177 | `e8a42dc7…` |
    | `360011f` | 4.0.0 | 172,177 | `e8a42dc7…` |
    | `5006e14` | 4.0.0 | 172,177 | `e8a42dc7…` |
    | `2ee78cb` | 4.0.0 | 170,593 | `e3ea1ae3…` |
    | `aa1cfd9` (the audited commit) | 3.1.10 | 181,737 | `3ba5eeff…` |

    None of these matches the live binary.
- **What that means.** The live program is post-audit code built in a way that
  isn't published, and not the audited commit itself.
- **The parts we use are unchanged.** Everything this contract relies on reads
  the same at `aa1cfd9`, at `5f2746d` and at main `dc88bfd`: the instruction
  discriminators, the data layouts, the PDA seeds, and the leaf and node
  hashing. The post-audit changes we know of only add checks, and our
  transactions already satisfy them:
  - the claimant's token account must be owned by the claimant;
  - mints with a transfer hook are refused on create;
  - the deposit must cover `total_amount`;
  - closing leaves a closed marker behind.
- **Before the first mainnet round,** ask the maintainers which commit is
  deployed, or wait for a verified build.

### Addresses

| What | Seeds (program = `REWArDioXgQJ2fZKkfu9LCLjQfRwYWVVfsvcsR5hoXi`) |
| --- | --- |
| merkle distribution | `["merkle_distribution", mint, authority, seed]` |
| merkle claim | `["merkle_claim", distribution, claimant]` |
| revocation marker | `["revocation", distribution, claimant]` |
| event authority | `["event_authority"]` (the IDL's docs say `__event_authority`; the code says `event_authority`) |
| vault | associated token account of the distribution for the mint, under the mint's token program |

`seed` is the address of a throwaway keypair that **must sign**
CreateMerkleDistribution. Each round gets a new one, so each round gets its own
distribution. The giveaway hand, or the spine, generates it, signs with it, and
publishes its address as `distribution_seed`. It is never needed again.

### Hashing (keccak-256, not sha256)

```
leaf = keccak256(0x00 || keccak256(claimant(32) || u64le(total_amount) || schedule))
       schedule for Immediate = the single byte 0x00
node = keccak256(min(a, b) || max(a, b))      a, b compared as bytes; no prefix byte
```

Build the tree the way the program's tests do
(`tests/integration-tests/src/utils/merkle_utils.rs`). Pair neighbours level
by level in leaf order. A lone last node moves up unchanged and adds no proof
step. A one-leaf tree's root is the leaf itself, with an empty proof.

Vector, computed in Rust from the program's code: claimants `[1;32]`, `[2;32]`
and `[3;32]` with totals 1000000, 2500000 and 42, all Immediate, give root
`47dfbe7fa0962b4e379252f841e5d0bec0bb3632a3478e6754fc2196b8bb7bcb`. The other
values are in `web/src/lib/distributor.test.ts`.

### Instructions

The first byte is the discriminator. Integers are little-endian. `w` means
writable and `s` means signer.

**CreateMerkleDistribution (5)** creates the distribution and its vault, then
moves `amount` from the authority's token account into the vault.

- **Data:** `5`, `bump u8`, `revocable u8` (0), `amount u64`,
  `merkle_root [32]`, `total_amount u64`, `clawback_ts i64`. That is 59 bytes
  in all.
- **Accounts:**
  1. payer (w, s)
  2. authority (s)
  3. seed (s)
  4. distribution (w)
  5. mint
  6. distribution vault (w)
  7. authority's token account (w)
  8. System program
  9. token program
  10. ATA program
  11. event authority
  12. the Rewards program
- **Rules:** `amount` and `total_amount` must be greater than 0. The program
  stores the amount that actually landed in the vault as `total_amount`, and
  [post-audit] refuses the call if that is less than `total_amount`. Set both
  to the round's total. `clawback_ts` is when the authority may close the
  distribution; set it to `clawback_at`. Use `revocable = 0`: nothing is ever
  revoked.

**ClaimMerkle (6)** claims everything unlocked.

- **Data:** `6`, `claim_bump u8`, `total_amount u64` (from the leaf),
  `amount u64` (0 = everything unlocked), `schedule` (Immediate = `0x00`),
  `u32 proof length`, then `32 × length` bytes of proof.
- **Accounts:**
  1. payer (w, s)
  2. claimant (s)
  3. distribution (w)
  4. merkle claim PDA (w)
  5. revocation PDA
  6. mint
  7. distribution vault (w)
  8. claimant's token account (w; [post-audit] owned by the claimant)
  9. System program
  10. token program
  11. event authority
  12. the Rewards program
- **Usage:** the page passes the claimant as the payer, and adds an idempotent
  ATA create under the mint's token program before the claim.

**CloseMerkleDistribution (8)** is the clawback.

- **Data:** the single byte `8`.
- **Accounts:**
  1. authority (w, s)
  2. distribution (w)
  3. mint
  4. distribution vault (w)
  5. authority's token account (w)
  6. token program
  7. event authority
  8. the Rewards program
- **What it does:**
  - It works only once `clawback_ts` has passed.
  - It moves everything left in the vault to the authority's token account,
    closes the vault, and returns the rent to the authority.
  - [post-audit] It turns the distribution into a 3-byte
    `MerkleDistributionClosed` marker (discriminator 6), so the same address
    can never be used again. The audited code closed the account outright.
- **Claims until then:** the program doesn't stop claims at `clawback_ts`.
  Claims keep working until this instruction runs, so the hand should close
  the round promptly at `clawback_at`. The page stops offering claims at
  `clawback_at`.

**CloseMerkleClaim (7)**

- **Data:** `7`.
- **Accounts:**
  1. claimant (w, s)
  2. distribution
  3. merkle claim PDA (w)
  4. event authority
  5. the Rewards program
- **What it does:** once the distribution is closed, the claimant can close
  their claim account and get its rent back.

### Account layouts

The first byte of each account is its discriminator, and the second is its
version (1).

- **MerkleDistribution (2), 162 bytes:**
  - byte 2: bump
  - byte 3: revocable
  - bytes 4–9: padding
  - bytes 10–41: authority
  - bytes 42–73: mint
  - bytes 74–105: seed
  - bytes 106–137: root
  - bytes 138–145: total_amount
  - bytes 146–153: total_claimed
  - bytes 154–161: clawback_ts
- **MerkleClaim (3), 18 bytes:**
  - byte 2: bump
  - bytes 3–9: padding
  - bytes 10–17: claimed_amount

### What a claim costs

- **The claim record:** rent for the 18-byte MerkleClaim account, 1,016,160
  lamports (about 0.001 SOL). The claimant gets it back with CloseMerkleClaim
  once the round is closed.
- **A token account, if the claimant has none:** rent for a Token-2022
  associated token account (170 bytes with ImmutableOwner), 2,074,080 lamports
  (about 0.0021 SOL).
- **The network fee.**

### The $TINY mint

The $TINY mint (`2AF7CqwieUjUPALL7icuZtL3X7wENdjUjGBMmfV2pump`, as read
2026-11-15):

- It is Token-2022 with 6 decimals.
- Its only extensions are MetadataPointer (18) and TokenMetadata (19).
- Its mint authority and freeze authority are both none.

The page refuses to build a claim for a mint carrying a transfer fee (extension
1) or a transfer hook (extension 14).

The giveaway wallet's transactions need three signers: the payer, the
authority and the throwaway seed. That matters for PR 5:
`wallet_execute_prepared_program` has to accept a transaction the host has
already signed with the seed keypair.
