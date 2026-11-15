import { PublicKey, SystemProgram } from "@solana/web3.js"
import { describe, expect, it } from "vitest"
import {
  ASSOCIATED_TOKEN_PROGRAM_ID,
  associatedTokenAddress,
  buildTree,
  bytesToHex,
  checkRoundAccounts,
  claimMerkleData,
  claimMerkleInstruction,
  claimPda,
  closeMerkleDistributionInstruction,
  createMerkleDistributionData,
  createMerkleDistributionInstruction,
  distributionPda,
  eventAuthorityPda,
  hexToBytes,
  leafHash,
  mintExtensions,
  parseClaimedAmount,
  parseDistribution,
  refusedMint,
  revocationPda,
  REWARDS_PROGRAM_ID,
  TOKEN_2022_PROGRAM_ID,
  TOKEN_PROGRAM_ID,
  verifyClaimProof,
  verifyProof,
} from "./distributor"

const PROGRAM = new PublicKey(REWARDS_PROGRAM_ID)
const TINY = new PublicKey("2AF7CqwieUjUPALL7icuZtL3X7wENdjUjGBMmfV2pump")
const key = (byte: number) => new PublicKey(new Uint8Array(32).fill(byte))

/**
 * Computed in Rust with tiny-keccak 2.0.2 and the hashing copied verbatim
 * from solana-foundation/rewards @ 5f2746d tests/integration-tests/src/utils/merkle_utils.rs
 * (itself a mirror of program/src/utils/merkle_utils.rs): claimants [1;32],
 * [2;32], [3;32] with totals 1000000, 2500000 and 42, all Immediate.
 */
const RUST = {
  leaves: [
    "ba9c9c52055de034f280764cb0c726ce9764a390491e19e648f2c7e24d601f45",
    "c4f930546c689f0761e0f3de34074bcd93e31deb2ab42dce8f886bf87827819c",
    "65a5b49cad9bacef0726790697c24cee61840b92af13227fd059ae2635f2882c",
  ],
  root: "47dfbe7fa0962b4e379252f841e5d0bec0bb3632a3478e6754fc2196b8bb7bcb",
  proofs: [
    ["c4f930546c689f0761e0f3de34074bcd93e31deb2ab42dce8f886bf87827819c", "65a5b49cad9bacef0726790697c24cee61840b92af13227fd059ae2635f2882c"],
    ["ba9c9c52055de034f280764cb0c726ce9764a390491e19e648f2c7e24d601f45", "65a5b49cad9bacef0726790697c24cee61840b92af13227fd059ae2635f2882c"],
    ["a0a99602f95f18470af71b317e28521c65bf70155742d686da1c67b63a379cf1"],
  ],
  /** ClaimMerkle for leaf 0: [6][bump 254][total][amount 0][Immediate][u32 2][proof]. */
  claim:
    "06fe40420f0000000000000000000000000000020000" +
    "00c4f930546c689f0761e0f3de34074bcd93e31deb2ab42dce8f886bf87827819c65a5b49cad9bacef0726790697c24cee61840b92af13227fd059ae2635f2882c",
  /** CreateMerkleDistribution: [5][bump 253][revocable 0][amount 3500042][root][total 3500042][clawback 1798000000]. */
  create:
    "05fd000a6835000000000047dfbe7fa0962b4e379252f841e5d0bec0bb3632a3478e6754fc2196b8bb7bcb0a68350000000000804d2b6b00000000",
}
const AMOUNTS = [1_000_000n, 2_500_000n, 42n]

/** The $TINY mint account as mainnet returned it on 2026-11-15: Token-2022, MetadataPointer (18) + TokenMetadata (19). */
const TINY_MINT_DATA = Buffer.from("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAMHgvLKR2AwAGAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAARIAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABE53XE3Ce878gYsrT8EIgerwCtFSKIYQTv6TghaMhzfEwCzAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAETndcTcJ7zvyBiytPwQiB6vAK0VIohhBO/pOCFoyHN8OAAAAVGlueSBIdW1hbnMgQUkEAAAAVElOWVEAAABodHRwczovL2FwaS52b3J0ZXhkZXBsb3llci5jb20vbWV0YWRhdGEvZjg5YTk4MmQtYzU3ZC00YzBlLWEyNmEtY2EwZmZiZGY4MmIyLmpzb24AAAAA", "base64")

describe("the rewards program's hashing", () => {
  it("hashes Immediate leaves exactly as the program's own Rust does", () => {
    AMOUNTS.forEach((amount, i) => expect(bytesToHex(leafHash(key(i + 1), amount))).toBe(RUST.leaves[i]))
  })

  it("builds the same root and proofs as the program's test tree, the odd leaf promoted", () => {
    const { root, proofs } = buildTree(AMOUNTS.map((amount, i) => leafHash(key(i + 1), amount)))
    expect(bytesToHex(root)).toBe(RUST.root)
    expect(proofs.map((p) => p.map(bytesToHex))).toEqual(RUST.proofs)
  })

  it("checks a diary claim's hex proof against the root, and refuses a wrong amount, claimant or proof", () => {
    const claimant = key(1).toBase58()
    expect(verifyClaimProof(RUST.root, claimant, "1000000", RUST.proofs[0])).toBe(true)
    expect(verifyClaimProof(RUST.root, claimant, "1000001", RUST.proofs[0])).toBe(false)
    expect(verifyClaimProof(RUST.root, key(2).toBase58(), "1000000", RUST.proofs[0])).toBe(false)
    expect(verifyClaimProof(RUST.root, claimant, "1000000", RUST.proofs[0].slice(1))).toBe(false)
    expect(verifyClaimProof(RUST.root, "not a key", "1000000", RUST.proofs[0])).toBe(false)
  })

  it("proves every leaf of trees of any size, a single leaf being its own root", () => {
    for (const size of [1, 2, 3, 5, 8, 13]) {
      const leaves = Array.from({ length: size }, (_, i) => leafHash(PublicKey.unique(), BigInt(i + 1)))
      const { root, proofs } = buildTree(leaves)
      leaves.forEach((leaf, i) => expect(verifyProof(proofs[i], root, leaf)).toBe(true))
      if (size === 1) expect(bytesToHex(root)).toBe(bytesToHex(leaves[0]))
    }
  })
})

describe("the program's addresses", () => {
  it("derives every PDA from the seeds in its state files", () => {
    const [mint, authority, seed, claimant] = [TINY, key(7), key(8), key(1)]
    const pda = (seeds: (string | PublicKey)[]) =>
      PublicKey.findProgramAddressSync(seeds.map((s) => (typeof s === "string" ? Buffer.from(s) : s.toBuffer())), PROGRAM)
    const [distribution, bump] = distributionPda(PROGRAM, mint, authority, seed)
    expect([distribution.toBase58(), bump]).toEqual(pda(["merkle_distribution", mint, authority, seed]).map(String).map((v, i) => (i ? Number(v) : v)))
    expect(claimPda(PROGRAM, distribution, claimant)[0].equals(pda(["merkle_claim", distribution, claimant])[0])).toBe(true)
    expect(revocationPda(PROGRAM, distribution, claimant).equals(pda(["revocation", distribution, claimant])[0])).toBe(true)
    expect(eventAuthorityPda(PROGRAM).equals(pda(["event_authority"])[0])).toBe(true)
  })

  it("puts the vault under the mint's own token program, so a Token-2022 vault differs from a classic one", () => {
    const [distribution] = distributionPda(PROGRAM, TINY, key(7), key(8))
    const vault = associatedTokenAddress(distribution, TINY, TOKEN_2022_PROGRAM_ID)
    expect(vault.equals(associatedTokenAddress(distribution, TINY, TOKEN_PROGRAM_ID))).toBe(false)
    const [expected] = PublicKey.findProgramAddressSync([distribution.toBuffer(), TOKEN_2022_PROGRAM_ID.toBuffer(), TINY.toBuffer()], ASSOCIATED_TOKEN_PROGRAM_ID)
    expect(vault.equals(expected)).toBe(true)
    const round = {
      program_id: REWARDS_PROGRAM_ID,
      mint: TINY.toBase58(),
      token_program: TOKEN_2022_PROGRAM_ID.toBase58(),
      authority: key(7).toBase58(),
      distribution_seed: key(8).toBase58(),
      distributor: distribution.toBase58(),
      vault: vault.toBase58(),
    }
    expect(checkRoundAccounts(round)).toBe(true)
    expect(checkRoundAccounts({ ...round, distribution_seed: key(9).toBase58() })).toBe(false)
    expect(checkRoundAccounts({ ...round, token_program: TOKEN_PROGRAM_ID.toBase58() })).toBe(false)
  })
})

describe("the instructions", () => {
  it("encodes ClaimMerkle byte for byte as the program's Rust vector", () => {
    const data = claimMerkleData(254, AMOUNTS[0], 0n, RUST.proofs[0].map(hexToBytes))
    expect(bytesToHex(data)).toBe(RUST.claim)
  })

  it("lists ClaimMerkle's twelve accounts in the order and with the flags of claim/accounts.rs", () => {
    const claimant = key(1)
    const [distribution] = distributionPda(PROGRAM, TINY, key(7), key(8))
    const ix = claimMerkleInstruction({ programId: PROGRAM, distribution, mint: TINY, tokenProgram: TOKEN_2022_PROGRAM_ID, claimant, totalAmount: AMOUNTS[0], proof: RUST.proofs[0].map(hexToBytes) })
    const [claim, bump] = claimPda(PROGRAM, distribution, claimant)
    expect(ix.data[0]).toBe(6)
    expect(ix.data[1]).toBe(bump)
    expect(ix.keys.map((k) => [k.pubkey.toBase58(), k.isSigner, k.isWritable])).toEqual([
      [claimant.toBase58(), true, true],
      [claimant.toBase58(), true, false],
      [distribution.toBase58(), false, true],
      [claim.toBase58(), false, true],
      [revocationPda(PROGRAM, distribution, claimant).toBase58(), false, false],
      [TINY.toBase58(), false, false],
      [associatedTokenAddress(distribution, TINY, TOKEN_2022_PROGRAM_ID).toBase58(), false, true],
      [associatedTokenAddress(claimant, TINY, TOKEN_2022_PROGRAM_ID).toBase58(), false, true],
      [SystemProgram.programId.toBase58(), false, false],
      [TOKEN_2022_PROGRAM_ID.toBase58(), false, false],
      [eventAuthorityPda(PROGRAM).toBase58(), false, false],
      [REWARDS_PROGRAM_ID, false, false],
    ])
  })

  it("encodes CreateMerkleDistribution byte for byte, and its accounts in create_distribution/accounts.rs order", () => {
    const data = createMerkleDistributionData({ bump: 253, revocable: 0, amount: 3_500_042n, root: hexToBytes(RUST.root), totalAmount: 3_500_042n, clawbackTs: 1_798_000_000n })
    expect(bytesToHex(data)).toBe(RUST.create)
    const [authority, seed] = [key(7), key(8)]
    const ix = createMerkleDistributionInstruction({ programId: PROGRAM, payer: authority, authority, seed, mint: TINY, tokenProgram: TOKEN_2022_PROGRAM_ID, amount: 1n, root: hexToBytes(RUST.root), totalAmount: 1n, clawbackTs: 0n })
    const [distribution, bump] = distributionPda(PROGRAM, TINY, authority, seed)
    expect(ix.data.length).toBe(59)
    expect([ix.data[0], ix.data[1]]).toEqual([5, bump])
    expect(ix.keys.map((k) => k.pubkey.toBase58())).toEqual([
      authority, authority, seed, distribution, TINY,
      associatedTokenAddress(distribution, TINY, TOKEN_2022_PROGRAM_ID),
      associatedTokenAddress(authority, TINY, TOKEN_2022_PROGRAM_ID),
      SystemProgram.programId, TOKEN_2022_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID, eventAuthorityPda(PROGRAM), PROGRAM,
    ].map((k) => k.toBase58()))
    expect(ix.keys.slice(0, 3).map((k) => k.isSigner)).toEqual([true, true, true])
  })

  it("encodes CloseMerkleDistribution as the single byte 8 with its eight accounts", () => {
    const [distribution] = distributionPda(PROGRAM, TINY, key(7), key(8))
    const ix = closeMerkleDistributionInstruction({ programId: PROGRAM, authority: key(7), distribution, mint: TINY, tokenProgram: TOKEN_2022_PROGRAM_ID })
    expect(bytesToHex(ix.data)).toBe("08")
    expect(ix.keys).toHaveLength(8)
    expect([ix.keys[0].isSigner, ix.keys[0].isWritable]).toEqual([true, true])
  })
})

describe("reading the chain", () => {
  it("reads a MerkleDistribution account and its closed marker", () => {
    const data = new Uint8Array(162)
    data.set([2, 1, 255, 0])
    data.set(key(7).toBytes(), 10)
    data.set(TINY.toBytes(), 42)
    data.set(key(8).toBytes(), 74)
    data.set(hexToBytes(RUST.root), 106)
    new DataView(data.buffer).setBigUint64(138, 3_500_042n, true)
    new DataView(data.buffer).setBigUint64(146, 42n, true)
    new DataView(data.buffer).setBigInt64(154, 1_798_000_000n, true)
    expect(parseDistribution(data)).toEqual({
      closed: false, authority: key(7).toBase58(), mint: TINY.toBase58(), seed: key(8).toBase58(), root: RUST.root,
      totalAmount: 3_500_042n, totalClaimed: 42n, clawbackTs: 1_798_000_000n,
    })
    expect(parseDistribution(Uint8Array.of(6, 1, 255))).toEqual({ closed: true })
    expect(parseDistribution(Uint8Array.of(9, 9))).toBeUndefined()
  })

  it("reads how much a MerkleClaim account has claimed", () => {
    const data = new Uint8Array(18)
    data.set([3, 1, 254])
    new DataView(data.buffer).setBigUint64(10, 1_000_000n, true)
    expect(parseClaimedAmount(data)).toBe(1_000_000n)
    expect(parseClaimedAmount(new Uint8Array(18))).toBeUndefined()
  })
})

describe("the mint guard", () => {
  it("lets $TINY through: Token-2022 with only metadata extensions", () => {
    expect(mintExtensions(TINY_MINT_DATA)).toEqual([18, 19])
    expect(refusedMint(TOKEN_2022_PROGRAM_ID, TINY_MINT_DATA)).toBeUndefined()
    expect(refusedMint(TOKEN_PROGRAM_ID, new Uint8Array(82))).toBeUndefined()
  })

  it("refuses a mint with a transfer fee or a transfer hook, and an account no token program owns", () => {
    const withExtension = (type: number) => {
      const data = new Uint8Array(TINY_MINT_DATA.length + 4)
      data.set(TINY_MINT_DATA)
      new DataView(data.buffer).setUint16(TINY_MINT_DATA.length, type, true)
      return data
    }
    expect(refusedMint(TOKEN_2022_PROGRAM_ID, withExtension(1))).toMatch(/transfer fee/)
    expect(refusedMint(TOKEN_2022_PROGRAM_ID, withExtension(14))).toMatch(/transfer hook/)
    expect(refusedMint(SystemProgram.programId, TINY_MINT_DATA)).toMatch(/not a token mint/)
  })
})
