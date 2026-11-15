/**
 * The on-chain side of a Tiny Drops claim: Jito's audited merkle-distributor
 * program, hand-encoded with @solana/web3.js (no Anchor client).
 *
 * Every fact here is read from the program source at
 * github.com/jito-foundation/distributor, commit
 * 2cf79f63973a79e6354871dc9355a63aa1d15139 (master, 2024-04-04):
 *
 *   program id   mERKcfxMC5SqJn4Ld4BUris3WKZZ1ojjWJ3A3J5CKxv
 *                (programs/merkle-distributor/src/lib.rs `declare_id!`; executable on
 *                mainnet-beta, NOT deployed on devnet: a devnet round names its own
 *                deployment in `program_id`)
 *   distributor  PDA ["MerkleDistributor", mint, u64le(version)]
 *                (instructions/new_distributor.rs); Tiny Drops uses version = round id
 *   claim status PDA ["ClaimStatus", claimant, distributor] (instructions/new_claim.rs)
 *   vault        the distributor's associated token account for the mint
 *   leaf         sha256(0x00 || sha256(claimant(32) || u64le(amount_unlocked) || u64le(amount_locked)))
 *                (new_claim.rs `hashv` then LEAF_PREFIX; merkle-tree/src/tree_node.rs `hash`)
 *   node         sha256(0x01 || min(a, b) || max(a, b)), bytes compared lexicographically
 *                (verify/src/lib.rs; merkle-tree/src/merkle_tree.rs with sorted_hashes = true;
 *                an odd node at the end of a level is paired with itself)
 *   new_claim    data: sha256("global:new_claim")[0..8] = 4eb1627bd215bb53,
 *                u64le amount_unlocked, u64le amount_locked, u32le proof length, 32 bytes each
 *                accounts: distributor (w), claim_status (w), from = vault (w),
 *                to = claimant's token account (w), claimant (w, signer),
 *                token_program, system_program
 *
 * The program takes the classic SPL Token program only (`Program<'info, Token>`
 * from anchor_spl::token), so it cannot pay out a Token-2022 mint; the page
 * checks the mint's owner before it builds a claim.
 */

import { sha256 } from '@noble/hashes/sha2'
import { bytesToHex, hexToBytes } from '@noble/hashes/utils'
import { PublicKey, SystemProgram, TransactionInstruction } from '@solana/web3.js'

export const JITO_DISTRIBUTOR_PROGRAM_ID = 'mERKcfxMC5SqJn4Ld4BUris3WKZZ1ojjWJ3A3J5CKxv'
export const TOKEN_PROGRAM_ID = new PublicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA')
export const TOKEN_2022_PROGRAM_ID = new PublicKey('TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb')
export const ASSOCIATED_TOKEN_PROGRAM_ID = new PublicKey('ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL')

/** Anchor's instruction discriminator for `new_claim`: sha256("global:new_claim")[0..8]. */
export const NEW_CLAIM_DISCRIMINATOR = Uint8Array.from([0x4e, 0xb1, 0x62, 0x7b, 0xd2, 0x15, 0xbb, 0x53])

function u64le(value: bigint): Uint8Array {
  if (value < 0n || value >= 1n << 64n) throw new RangeError('not a u64')
  const out = new Uint8Array(8)
  new DataView(out.buffer).setBigUint64(0, value, true)
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

/** A leaf as the program hashes it: sha256(0x00 || sha256(claimant || u64le(unlocked) || u64le(locked))). */
export function leafHash(claimant: PublicKey, amountUnlocked: bigint, amountLocked: bigint): Uint8Array {
  const inner = sha256(concat(claimant.toBytes(), u64le(amountUnlocked), u64le(amountLocked)))
  return sha256(concat(Uint8Array.of(0), inner))
}

function compare(a: Uint8Array, b: Uint8Array): number {
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] - b[i]
  return 0
}

/** An inner node: sha256(0x01 || the smaller child || the larger), as verify/src/lib.rs does it. */
export function nodeHash(a: Uint8Array, b: Uint8Array): Uint8Array {
  return compare(a, b) <= 0 ? sha256(concat(Uint8Array.of(1), a, b)) : sha256(concat(Uint8Array.of(1), b, a))
}

/** The program's proof check (verify/src/lib.rs), so a bad proof is caught before anyone pays a fee. */
export function verifyProof(proof: Uint8Array[], root: Uint8Array, leaf: Uint8Array): boolean {
  const computed = proof.reduce((hash, sibling) => nodeHash(hash, sibling), leaf)
  return compare(computed, root) === 0 && computed.length === root.length
}

/** `verifyProof` over the diary's hex strings. */
export function verifyClaimProof(root: string, claimant: string, amountAtomic: string, proof: string[]): boolean {
  try {
    const leaf = leafHash(new PublicKey(claimant), BigInt(amountAtomic), 0n)
    return verifyProof(proof.map(hexToBytes), hexToBytes(root), leaf)
  } catch {
    return false
  }
}

/** The root and every proof for a list of leaves, built the way merkle-tree/src/merkle_tree.rs builds it. */
export function buildTree(leaves: Uint8Array[]): { root: Uint8Array; proofs: Uint8Array[][] } {
  if (!leaves.length) throw new RangeError('no leaves')
  const levels: Uint8Array[][] = [leaves]
  while (levels[levels.length - 1].length > 1) {
    const prev = levels[levels.length - 1]
    const next: Uint8Array[] = []
    for (let i = 0; i < prev.length; i += 2) next.push(nodeHash(prev[i], prev[i + 1] ?? prev[i]))
    levels.push(next)
  }
  const proofs = leaves.map((_, index) => {
    const proof: Uint8Array[] = []
    let i = index
    for (const level of levels.slice(0, -1)) {
      proof.push(i % 2 === 0 ? (level[i + 1] ?? level[i]) : level[i - 1])
      i = Math.floor(i / 2)
    }
    return proof
  })
  return { root: levels[levels.length - 1][0], proofs }
}

export function distributorPda(programId: PublicKey, mint: PublicKey, version: bigint): PublicKey {
  return PublicKey.findProgramAddressSync([new TextEncoder().encode('MerkleDistributor'), mint.toBytes(), u64le(version)], programId)[0]
}

export function claimStatusPda(programId: PublicKey, claimant: PublicKey, distributor: PublicKey): PublicKey {
  return PublicKey.findProgramAddressSync([new TextEncoder().encode('ClaimStatus'), claimant.toBytes(), distributor.toBytes()], programId)[0]
}

/** The associated token account of `owner` for a classic SPL Token mint. */
export function associatedTokenAddress(owner: PublicKey, mint: PublicKey): PublicKey {
  return PublicKey.findProgramAddressSync([owner.toBytes(), TOKEN_PROGRAM_ID.toBytes(), mint.toBytes()], ASSOCIATED_TOKEN_PROGRAM_ID)[0]
}

/** The ATA program's CreateIdempotent (instruction 1): a no-op when the account is already there. */
export function createAtaIdempotentInstruction(payer: PublicKey, owner: PublicKey, mint: PublicKey): TransactionInstruction {
  return new TransactionInstruction({
    programId: ASSOCIATED_TOKEN_PROGRAM_ID,
    keys: [
      { pubkey: payer, isSigner: true, isWritable: true },
      { pubkey: associatedTokenAddress(owner, mint), isSigner: false, isWritable: true },
      { pubkey: owner, isSigner: false, isWritable: false },
      { pubkey: mint, isSigner: false, isWritable: false },
      { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
    ],
    data: Buffer.from([1]),
  })
}

/** The `new_claim` instruction data, byte for byte as Anchor's borsh encoding of the IDL args. */
export function newClaimData(amountUnlocked: bigint, amountLocked: bigint, proof: Uint8Array[]): Uint8Array {
  const length = new Uint8Array(4)
  new DataView(length.buffer).setUint32(0, proof.length, true)
  for (const p of proof) if (p.length !== 32) throw new RangeError('proof element is not 32 bytes')
  return concat(NEW_CLAIM_DISCRIMINATOR, u64le(amountUnlocked), u64le(amountLocked), length, ...proof)
}

export interface NewClaimArgs {
  programId: PublicKey
  distributor: PublicKey
  mint: PublicKey
  claimant: PublicKey
  amountUnlocked: bigint
  amountLocked: bigint
  proof: Uint8Array[]
}

/** `new_claim`, accounts in the order of the program's `NewClaim` struct. */
export function newClaimInstruction(args: NewClaimArgs): TransactionInstruction {
  const { programId, distributor, mint, claimant } = args
  return new TransactionInstruction({
    programId,
    keys: [
      { pubkey: distributor, isSigner: false, isWritable: true },
      { pubkey: claimStatusPda(programId, claimant, distributor), isSigner: false, isWritable: true },
      { pubkey: associatedTokenAddress(distributor, mint), isSigner: false, isWritable: true },
      { pubkey: associatedTokenAddress(claimant, mint), isSigner: false, isWritable: true },
      { pubkey: claimant, isSigner: true, isWritable: true },
      { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
      { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
    ],
    data: Buffer.from(newClaimData(args.amountUnlocked, args.amountLocked, args.proof)),
  })
}

/** What a round's file says about the chain, checked against what the program would derive. */
export function checkRoundAccounts(round: { id: number; program_id: string; mint: string; distributor: string | null; vault: string | null }): boolean {
  if (!round.distributor || !round.vault) return false
  try {
    const distributor = distributorPda(new PublicKey(round.program_id), new PublicKey(round.mint), BigInt(round.id))
    return distributor.toBase58() === round.distributor && associatedTokenAddress(distributor, new PublicKey(round.mint)).toBase58() === round.vault
  } catch {
    return false
  }
}

export { bytesToHex, hexToBytes }
