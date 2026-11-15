/**
 * The on-chain side of a Tiny Drops round: the Solana Foundation Rewards
 * program's merkle distribution, encoded by hand with @solana/web3.js.
 *
 * Source: github.com/solana-foundation/rewards (MIT). The audited-through commit
 * (OtterSec; audits/AUDIT_STATUS.md) is aa1cfd9276375e44e57d1917d110ff095fb6d475.
 * The last mainnet upgrade (2026-05-27) most likely came from 5f2746d. Every
 * fact below is the same at both commits, unless it is marked [post-audit].
 * Files are under program/src/.
 *
 *   program id     REWArDioXgQJ2fZKkfu9LCLjQfRwYWVVfsvcsR5hoXi (lib.rs declare_id!; on mainnet and devnet)
 *   distribution   PDA ["merkle_distribution", mint, authority, seed]
 *                  (state/merkle_distribution.rs). `seed` is the address of a
 *                  throwaway keypair that has to sign CreateMerkleDistribution.
 *   claim          PDA ["merkle_claim", distribution, claimant] (state/merkle_claim.rs)
 *   revocation     PDA ["revocation", distribution, claimant] (state/revocation.rs)
 *   event auth.    PDA ["event_authority"] (events/shared.rs; the IDL docs say "__event_authority", the code doesn't)
 *   vault          the distribution's associated token account, under the mint's
 *                  own token program (SPL Token or Token-2022)
 *   leaf           keccak256(0x00 || keccak256(claimant(32) || u64le(total_amount) || schedule))
 *                  with schedule = 0x00 for Immediate (utils/merkle_utils.rs, utils/vesting_utils.rs)
 *   node           keccak256(min(a,b) || max(a,b)), compared byte by byte, no prefix
 *                  (merkle_utils.rs hash_pair)
 *   instruction    first byte = discriminator (entrypoint.rs, instructions/definition.rs):
 *                  5 CreateMerkleDistribution, 6 ClaimMerkle, 7 CloseMerkleClaim, 8 CloseMerkleDistribution
 *
 * The program has no vesting work to do for an immediate full unlock: the
 * schedule lives in the leaf, Immediate unlocks the whole total_amount at once,
 * and amount = 0 in ClaimMerkle claims everything still unclaimed.
 */

import { keccak_256 } from '@noble/hashes/sha3'
import { bytesToHex, hexToBytes } from '@noble/hashes/utils'
import { PublicKey, SystemProgram, TransactionInstruction } from '@solana/web3.js'

export const REWARDS_PROGRAM_ID = 'REWArDioXgQJ2fZKkfu9LCLjQfRwYWVVfsvcsR5hoXi'
export const TOKEN_PROGRAM_ID = new PublicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA')
export const TOKEN_2022_PROGRAM_ID = new PublicKey('TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb')
export const ASSOCIATED_TOKEN_PROGRAM_ID = new PublicKey('ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL')

export const IX_CREATE_MERKLE_DISTRIBUTION = 5
export const IX_CLAIM_MERKLE = 6
export const IX_CLOSE_MERKLE_CLAIM = 7
export const IX_CLOSE_MERKLE_DISTRIBUTION = 8

/** VestingSchedule::Immediate {} as the program serializes it: one byte, 0. */
export const IMMEDIATE_SCHEDULE = Uint8Array.of(0)

/** Account discriminators (traits/account.rs). MerkleDistributionClosed is [post-audit]. */
export const ACCOUNT_MERKLE_DISTRIBUTION = 2
export const ACCOUNT_MERKLE_CLAIM = 3
export const ACCOUNT_MERKLE_DISTRIBUTION_CLOSED = 6

const text = (s: string) => new TextEncoder().encode(s)

function u64le(value: bigint): Uint8Array {
  if (value < 0n || value >= 1n << 64n) throw new RangeError('not a u64')
  const out = new Uint8Array(8)
  new DataView(out.buffer).setBigUint64(0, value, true)
  return out
}

function i64le(value: bigint): Uint8Array {
  if (value < -(1n << 63n) || value >= 1n << 63n) throw new RangeError('not an i64')
  const out = new Uint8Array(8)
  new DataView(out.buffer).setBigInt64(0, value, true)
  return out
}

function u32le(value: number): Uint8Array {
  const out = new Uint8Array(4)
  new DataView(out.buffer).setUint32(0, value, true)
  return out
}

function concat(...parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0))
  let at = 0
  for (const p of parts) {
    out.set(p, at)
    at += p.length
  }
  return out
}

function compare(a: Uint8Array, b: Uint8Array): number {
  for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i] - b[i]
  return a.length - b.length
}

// --- the tree ------------------------------------------------------------------

/** A leaf as compute_leaf_hash makes it, for an Immediate schedule unless told otherwise. */
export function leafHash(claimant: PublicKey, totalAmount: bigint, schedule: Uint8Array = IMMEDIATE_SCHEDULE): Uint8Array {
  const inner = keccak_256(concat(claimant.toBytes(), u64le(totalAmount), schedule))
  return keccak_256(concat(Uint8Array.of(0), inner))
}

/** hash_pair: keccak256 of the two hashes, smaller first, with no prefix. */
export function nodeHash(a: Uint8Array, b: Uint8Array): Uint8Array {
  return compare(a, b) < 0 ? keccak_256(concat(a, b)) : keccak_256(concat(b, a))
}

/** verify_proof: fold the siblings into the leaf and compare with the root. */
export function verifyProof(proof: Uint8Array[], root: Uint8Array, leaf: Uint8Array): boolean {
  return compare(proof.reduce((hash, sibling) => nodeHash(hash, sibling), leaf), root) === 0
}

/** `verifyProof` over the diary's hex strings, for an Immediate leaf. */
export function verifyClaimProof(root: string, claimant: string, amountAtomic: string, proof: string[]): boolean {
  try {
    return verifyProof(proof.map(hexToBytes), hexToBytes(root), leafHash(new PublicKey(claimant), BigInt(amountAtomic)))
  } catch {
    return false
  }
}

/**
 * The root and every proof, built the way the program's own integration tests
 * build them (tests/integration-tests/src/utils/merkle_utils.rs): pair
 * neighbours level by level in leaf order, and promote a lone last node
 * unchanged (it adds no proof step).
 */
export function buildTree(leaves: Uint8Array[]): { root: Uint8Array; proofs: Uint8Array[][] } {
  if (!leaves.length) throw new RangeError('no leaves')
  const levels: Uint8Array[][] = [leaves]
  while (levels[levels.length - 1].length > 1) {
    const prev = levels[levels.length - 1]
    const next: Uint8Array[] = []
    for (let i = 0; i < prev.length; i += 2) next.push(i + 1 < prev.length ? nodeHash(prev[i], prev[i + 1]) : prev[i])
    levels.push(next)
  }
  const proofs = leaves.map((_, index) => {
    const proof: Uint8Array[] = []
    let i = index
    for (const level of levels.slice(0, -1)) {
      const sibling = i % 2 === 0 ? i + 1 : i - 1
      if (sibling < level.length) proof.push(level[sibling])
      i = Math.floor(i / 2)
    }
    return proof
  })
  return { root: levels[levels.length - 1][0], proofs }
}

// --- addresses -----------------------------------------------------------------

export function distributionPda(programId: PublicKey, mint: PublicKey, authority: PublicKey, seed: PublicKey): [PublicKey, number] {
  return PublicKey.findProgramAddressSync([text('merkle_distribution'), mint.toBytes(), authority.toBytes(), seed.toBytes()], programId)
}

export function claimPda(programId: PublicKey, distribution: PublicKey, claimant: PublicKey): [PublicKey, number] {
  return PublicKey.findProgramAddressSync([text('merkle_claim'), distribution.toBytes(), claimant.toBytes()], programId)
}

export function revocationPda(programId: PublicKey, distribution: PublicKey, claimant: PublicKey): PublicKey {
  return PublicKey.findProgramAddressSync([text('revocation'), distribution.toBytes(), claimant.toBytes()], programId)[0]
}

export function eventAuthorityPda(programId: PublicKey): PublicKey {
  return PublicKey.findProgramAddressSync([text('event_authority')], programId)[0]
}

/** The associated token account of `owner` for `mint` under `tokenProgram`. */
export function associatedTokenAddress(owner: PublicKey, mint: PublicKey, tokenProgram: PublicKey): PublicKey {
  return PublicKey.findProgramAddressSync([owner.toBytes(), tokenProgram.toBytes(), mint.toBytes()], ASSOCIATED_TOKEN_PROGRAM_ID)[0]
}

/** The ATA program's CreateIdempotent (instruction 1): a no-op when the account is already there. */
export function createAtaIdempotentInstruction(payer: PublicKey, owner: PublicKey, mint: PublicKey, tokenProgram: PublicKey): TransactionInstruction {
  return new TransactionInstruction({
    programId: ASSOCIATED_TOKEN_PROGRAM_ID,
    keys: [
      { pubkey: payer, isSigner: true, isWritable: true },
      { pubkey: associatedTokenAddress(owner, mint, tokenProgram), isSigner: false, isWritable: true },
      { pubkey: owner, isSigner: false, isWritable: false },
      { pubkey: mint, isSigner: false, isWritable: false },
      { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      { pubkey: tokenProgram, isSigner: false, isWritable: false },
    ],
    data: Buffer.from([1]),
  })
}

// --- instructions --------------------------------------------------------------

/** ClaimMerkle data: 6, claim_bump, u64 total_amount, u64 amount (0 = all), schedule, u32 proof length, 32 bytes each. */
export function claimMerkleData(claimBump: number, totalAmount: bigint, amount: bigint, proof: Uint8Array[], schedule: Uint8Array = IMMEDIATE_SCHEDULE): Uint8Array {
  for (const p of proof) if (p.length !== 32) throw new RangeError('proof element is not 32 bytes')
  return concat(Uint8Array.of(IX_CLAIM_MERKLE, claimBump), u64le(totalAmount), u64le(amount), schedule, u32le(proof.length), ...proof)
}

export interface ClaimMerkleArgs {
  programId: PublicKey
  distribution: PublicKey
  mint: PublicKey
  tokenProgram: PublicKey
  claimant: PublicKey
  /** Pays the claim PDA's rent; the claimant unless someone sponsors it. */
  payer?: PublicKey
  totalAmount: bigint
  proof: Uint8Array[]
}

/** ClaimMerkle, accounts in the order of instructions/merkle/claim/accounts.rs, claiming everything unlocked. */
export function claimMerkleInstruction(args: ClaimMerkleArgs): TransactionInstruction {
  const { programId, distribution, mint, tokenProgram, claimant } = args
  const [claim, bump] = claimPda(programId, distribution, claimant)
  return new TransactionInstruction({
    programId,
    keys: [
      { pubkey: args.payer ?? claimant, isSigner: true, isWritable: true },
      { pubkey: claimant, isSigner: true, isWritable: false },
      { pubkey: distribution, isSigner: false, isWritable: true },
      { pubkey: claim, isSigner: false, isWritable: true },
      { pubkey: revocationPda(programId, distribution, claimant), isSigner: false, isWritable: false },
      { pubkey: mint, isSigner: false, isWritable: false },
      { pubkey: associatedTokenAddress(distribution, mint, tokenProgram), isSigner: false, isWritable: true },
      { pubkey: associatedTokenAddress(claimant, mint, tokenProgram), isSigner: false, isWritable: true },
      { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      { pubkey: tokenProgram, isSigner: false, isWritable: false },
      { pubkey: eventAuthorityPda(programId), isSigner: false, isWritable: false },
      { pubkey: programId, isSigner: false, isWritable: false },
    ],
    data: Buffer.from(claimMerkleData(bump, args.totalAmount, 0n, args.proof)),
  })
}

/**
 * CreateMerkleDistribution data: 5, bump, revocable, u64 amount (moved from the
 * authority's token account into the vault in the same instruction), merkle_root,
 * u64 total_amount, i64 clawback_ts. Not used by the page; here so the spine's
 * builder has a reference and a test vector.
 */
export function createMerkleDistributionData(args: { bump: number; revocable: number; amount: bigint; root: Uint8Array; totalAmount: bigint; clawbackTs: bigint }): Uint8Array {
  if (args.root.length !== 32) throw new RangeError('root is not 32 bytes')
  return concat(Uint8Array.of(IX_CREATE_MERKLE_DISTRIBUTION, args.bump, args.revocable), u64le(args.amount), args.root, u64le(args.totalAmount), i64le(args.clawbackTs))
}

/** CreateMerkleDistribution, accounts in the order of instructions/merkle/create_distribution/accounts.rs. */
export function createMerkleDistributionInstruction(args: {
  programId: PublicKey
  payer: PublicKey
  authority: PublicKey
  seed: PublicKey
  mint: PublicKey
  tokenProgram: PublicKey
  amount: bigint
  root: Uint8Array
  totalAmount: bigint
  clawbackTs: bigint
}): TransactionInstruction {
  const { programId, payer, authority, seed, mint, tokenProgram } = args
  const [distribution, bump] = distributionPda(programId, mint, authority, seed)
  return new TransactionInstruction({
    programId,
    keys: [
      { pubkey: payer, isSigner: true, isWritable: true },
      { pubkey: authority, isSigner: true, isWritable: false },
      { pubkey: seed, isSigner: true, isWritable: false },
      { pubkey: distribution, isSigner: false, isWritable: true },
      { pubkey: mint, isSigner: false, isWritable: false },
      { pubkey: associatedTokenAddress(distribution, mint, tokenProgram), isSigner: false, isWritable: true },
      { pubkey: associatedTokenAddress(authority, mint, tokenProgram), isSigner: false, isWritable: true },
      { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      { pubkey: tokenProgram, isSigner: false, isWritable: false },
      { pubkey: ASSOCIATED_TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
      { pubkey: eventAuthorityPda(programId), isSigner: false, isWritable: false },
      { pubkey: programId, isSigner: false, isWritable: false },
    ],
    data: Buffer.from(createMerkleDistributionData({ bump, revocable: 0, amount: args.amount, root: args.root, totalAmount: args.totalAmount, clawbackTs: args.clawbackTs })),
  })
}

/** CloseMerkleDistribution (the clawback): data is the one byte 8; accounts per close_distribution/accounts.rs. */
export function closeMerkleDistributionInstruction(args: { programId: PublicKey; authority: PublicKey; distribution: PublicKey; mint: PublicKey; tokenProgram: PublicKey }): TransactionInstruction {
  const { programId, authority, distribution, mint, tokenProgram } = args
  return new TransactionInstruction({
    programId,
    keys: [
      { pubkey: authority, isSigner: true, isWritable: true },
      { pubkey: distribution, isSigner: false, isWritable: true },
      { pubkey: mint, isSigner: false, isWritable: false },
      { pubkey: associatedTokenAddress(distribution, mint, tokenProgram), isSigner: false, isWritable: true },
      { pubkey: associatedTokenAddress(authority, mint, tokenProgram), isSigner: false, isWritable: true },
      { pubkey: tokenProgram, isSigner: false, isWritable: false },
      { pubkey: eventAuthorityPda(programId), isSigner: false, isWritable: false },
      { pubkey: programId, isSigner: false, isWritable: false },
    ],
    data: Buffer.from([IX_CLOSE_MERKLE_DISTRIBUTION]),
  })
}

// --- reading accounts ------------------------------------------------------------

/** A MerkleDistribution account: [disc 2][version][bump][revocable][6 pad][authority][mint][seed][root][u64 total][u64 claimed][i64 clawback_ts]. */
export interface DistributionState {
  closed: boolean
  authority?: string
  mint?: string
  seed?: string
  root?: string
  totalAmount?: bigint
  totalClaimed?: bigint
  clawbackTs?: bigint
}

export function parseDistribution(data: Uint8Array): DistributionState | undefined {
  if (data.length >= 2 && data[0] === ACCOUNT_MERKLE_DISTRIBUTION_CLOSED) return { closed: true }
  if (data.length < 162 || data[0] !== ACCOUNT_MERKLE_DISTRIBUTION) return undefined
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength)
  const key = (at: number) => new PublicKey(data.slice(at, at + 32)).toBase58()
  return {
    closed: false,
    authority: key(10),
    mint: key(42),
    seed: key(74),
    root: bytesToHex(data.slice(106, 138)),
    totalAmount: view.getBigUint64(138, true),
    totalClaimed: view.getBigUint64(146, true),
    clawbackTs: view.getBigInt64(154, true),
  }
}

/** How much a MerkleClaim account says was claimed: [disc 3][version][bump][7 pad][u64 claimed_amount]. */
export function parseClaimedAmount(data: Uint8Array): bigint | undefined {
  if (data.length < 18 || data[0] !== ACCOUNT_MERKLE_CLAIM) return undefined
  return new DataView(data.buffer, data.byteOffset, data.byteLength).getBigUint64(10, true)
}

/**
 * Token-2022 extensions a round must not use. A transfer fee would pay winners
 * less than their leaf. A transfer hook runs someone else's code on every claim
 * (the deployed program refuses those itself [post-audit]).
 */
export const REFUSED_EXTENSIONS = new Map([
  [1, 'a transfer fee'],
  [14, 'a transfer hook'],
])

/**
 * The extension types a Token-2022 mint account carries: the base mint is 82
 * bytes, padded to 165, then the account-type byte (1 = mint) and TLV entries
 * (u16 type, u16 length, data). A classic SPL mint has none.
 */
export function mintExtensions(data: Uint8Array): number[] {
  if (data.length <= 165) return []
  if (data[165] !== 1) throw new RangeError('not a mint')
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength)
  const types: number[] = []
  for (let at = 166; at + 4 <= data.length; ) {
    const type = view.getUint16(at, true)
    const length = view.getUint16(at + 2, true)
    if (type === 0) break
    types.push(type)
    at += 4 + length
  }
  return types
}

/** Why a mint can't be paid out safely, or undefined when it can. */
export function refusedMint(owner: PublicKey, data: Uint8Array): string | undefined {
  if (owner.equals(TOKEN_PROGRAM_ID)) return undefined
  if (!owner.equals(TOKEN_2022_PROGRAM_ID)) return 'is not a token mint'
  try {
    const bad = mintExtensions(data).find((t) => REFUSED_EXTENSIONS.has(t))
    return bad === undefined ? undefined : `has ${REFUSED_EXTENSIONS.get(bad)}`
  } catch {
    return 'is not a token mint'
  }
}

/** What a round's file says about the chain, against what the program would derive. */
export function checkRoundAccounts(round: {
  program_id: string
  mint: string
  token_program: string
  authority: string | null
  distribution_seed: string | null
  distributor: string | null
  vault: string | null
}): boolean {
  if (!round.distributor || !round.vault || !round.authority || !round.distribution_seed) return false
  try {
    const mint = new PublicKey(round.mint)
    const [distribution] = distributionPda(new PublicKey(round.program_id), mint, new PublicKey(round.authority), new PublicKey(round.distribution_seed))
    return distribution.toBase58() === round.distributor && associatedTokenAddress(distribution, mint, new PublicKey(round.token_program)).toBase58() === round.vault
  } catch {
    return false
  }
}

export { bytesToHex, hexToBytes }
