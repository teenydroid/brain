import { PublicKey, SystemProgram } from "@solana/web3.js"
import { describe, expect, it } from "vitest"
import {
  associatedTokenAddress,
  buildTree,
  bytesToHex,
  checkRoundAccounts,
  claimStatusPda,
  distributorPda,
  hexToBytes,
  JITO_DISTRIBUTOR_PROGRAM_ID,
  leafHash,
  newClaimData,
  newClaimInstruction,
  TOKEN_PROGRAM_ID,
  verifyClaimProof,
  verifyProof,
} from "./distributor"

const PROGRAM = new PublicKey(JITO_DISTRIBUTOR_PROGRAM_ID)

/**
 * Vectors from Jito's own JTO airdrop: merkle_tree.json at the root of
 * github.com/jito-foundation/distributor @ 2cf79f63, built by its merkle-tree
 * crate. Its root is the root stored in the live mainnet distributor
 * HS8EQ8QkQSBJggY8r255AKdWbtYRtyNMoRt4LjNkkWm1 (mint jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL,
 * version 0), whose vault 8Xm3tkQH… the repository's README names.
 */
const JTO = {
  mint: "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
  distributor: "HS8EQ8QkQSBJggY8r255AKdWbtYRtyNMoRt4LjNkkWm1",
  vault: "8Xm3tkQH581s3MoRHWUNYA5jKbgPATW4tJAAxgwDC6T6",
  root: "ea23725bd5fc9e298d7f9f683b633d9eed38c324f5464ccea3bccbd7e4272c85",
  first: {
    claimant: "81703f8a46c2fa6bb54eb15d6f46841975b74cb812ae9e3274f38e8f4d00df47",
    unlocked: 400000000000000n,
    locked: 400000000000000n,
    proof: [
      "ad8f306471cdb293e210e202e90866cf36ee6e8f5a74ef44755fffba20782fac",
      "9e2e785ab5424b0e41b5a6ce35cc04b85fcfc9609da8c42a7965b18e3b60fd64",
      "6f1991327946429848c75da847edaf5853db2f4eaae7e16f158740cf363baaaa",
      "28e560700e0bd845d674dca3e985c658c4723da25bf2966db3d0f4c08a5ebe6b",
      "be90b8b2f8e87dc793499bf3d70931274a4a65c9ab49dc7b6a194e60abefccb5",
      "9f5bf8e363e21ed632cc592ec2e35488c2ab294a4e1a72f68c43a82955f0c1fb",
      "0fe88b685e05900870bde8a025d76dc61b3ea427f00e77ea38e275d4e491841e",
      "69028bf3dff10ea18ea87571b63a7be9fd25427a744854fab21f5aee75e9f95f",
      "d2e347b00366249c307f38898918389bf3999f3a0f4093a3e7e56b18d92b7dc8",
      "4dd6337a76be8268b0fe69886c00e1e2df4632f7c107a3c0f177a35498ad0370",
      "0ec13810cf9650bfe9682db8646e4cbc90b28e52116a491d774b281e5656818e",
      "62267b0f3cb0894f5cdcba2ea0efa3048afe0723e406860ee7da33845784fbdb",
      "8e3d9e05ad6464f1b1e873baf215f9791e26ca25e0a654a4bf36baad3736d6c9",
      "abce6fc72ff9c7b41833fb37d70ece7afe1c213f712f0a85546dc2ae88677169",
    ],
  },
  /** The last of the 10,132 leaves: its path pairs odd nodes with themselves. */
  last: {
    claimant: "0db9e1fe1ce247d953258ea6f59348f16645964708d307a3dce863cfaf95156b",
    unlocked: 9158000000000n,
    locked: 9158000000000n,
    proof: [
      "c279d0d56302ed88277ac9c8af8caf73f3b76efddae08f3eb750d45cb8e8bc75",
      "d6223c2f6f57f0c5e39eefcb0f27feaca0bb5d0bef1d87b24bfe24d669b1e0ec",
      "2bf2e10a8d9d29bf0ea8ede972b7ee16eb1864c91591fbeb19dc3db6d9f6dba9",
      "87b44227ebc9759fffd430e875ff8de7d9152a7b9cdd5da7eafbb7e9b5c7f9d9",
      "b2ac620ac89c404eb0ecf791a5636e587fe107f4998e24572ef41e71da26e70d",
      "985cca5a0e7772c1537de8408ec4df8a30a7d339f65e04335588908d7d8349ac",
      "3d4dfc080e287ec77f3b28d62824b90874775f94797016dcf6669f2539235b7a",
      "57215a9abf537f7ac88b366095abab2994bfb00e2292841e2357d559b000978f",
      "3904b62abeccaeb8eb4435b4a42209577c87041f210a9087e7b1bc470d471500",
      "04bf53254670d5b61c95755f9603702a42d18cfe91da3ed9c2ea8e44521caa64",
      "ff02da193df601625c6b5e331e38cdaab7f6fde3b52e094d3190f69453cbd905",
      "a8df7cd82ddfd55a81059f91f8f8e1b5f16a179c3ee30dce895727a98abbc65f",
      "842a52cae10563a485759503e57c44c633fa57a2e5449b9a8d03bddf8f9ca2e8",
      "726dc37004f6f115c73f1a89224a8ae02180b539272b08f49d725d2e375d5d3e",
    ],
  },
}

const key = (hex: string) => new PublicKey(hexToBytes(hex))

describe("the merkle-distributor's hashing", () => {
  it("hashes a leaf the way the program does: the JTO tree's second leaf names the first leaf as its sibling", () => {
    const leaf = leafHash(key(JTO.first.claimant), JTO.first.unlocked, JTO.first.locked)
    expect(bytesToHex(leaf)).toBe("c7f1f148ee579a0ed2be99d5ebd76a0de9a7e3158dbb0c77a1c22ae63d186c64")
  })

  it("verifies real proofs from the JTO airdrop against the root stored on mainnet", () => {
    for (const node of [JTO.first, JTO.last]) {
      const leaf = leafHash(key(node.claimant), node.unlocked, node.locked)
      expect(verifyProof(node.proof.map(hexToBytes), hexToBytes(JTO.root), leaf)).toBe(true)
    }
  })

  it("refuses a proof for the wrong amount, the wrong claimant or with a sibling dropped", () => {
    const root = hexToBytes(JTO.root)
    const proof = JTO.first.proof.map(hexToBytes)
    expect(verifyProof(proof, root, leafHash(key(JTO.first.claimant), JTO.first.unlocked + 1n, JTO.first.locked))).toBe(false)
    expect(verifyProof(proof, root, leafHash(key(JTO.last.claimant), JTO.first.unlocked, JTO.first.locked))).toBe(false)
    expect(verifyProof(proof.slice(1), root, leafHash(key(JTO.first.claimant), JTO.first.unlocked, JTO.first.locked))).toBe(false)
  })

  it("builds a tree (odd sizes included) whose every proof the program's check accepts", () => {
    for (const size of [1, 2, 3, 5, 8, 11]) {
      const leaves = Array.from({ length: size }, (_, i) => leafHash(PublicKey.unique(), BigInt(i + 1) * 1_000_000n, 0n))
      const { root, proofs } = buildTree(leaves)
      leaves.forEach((leaf, i) => expect(verifyProof(proofs[i], root, leaf)).toBe(true))
      if (size > 1) expect(verifyProof(proofs[0], root, leaves[1])).toBe(false)
    }
  })

  it("checks a diary claim's hex proof, with nothing locked", () => {
    const winners = [PublicKey.unique(), PublicKey.unique(), PublicKey.unique()]
    const amounts = [5_000_000n, 7_000_000n, 1n]
    const { root, proofs } = buildTree(winners.map((w, i) => leafHash(w, amounts[i], 0n)))
    const hex = proofs[2].map(bytesToHex)
    expect(verifyClaimProof(bytesToHex(root), winners[2].toBase58(), "1", hex)).toBe(true)
    expect(verifyClaimProof(bytesToHex(root), winners[2].toBase58(), "2", hex)).toBe(false)
    expect(verifyClaimProof(bytesToHex(root), "not a key", "1", hex)).toBe(false)
  })
})

describe("the program's addresses", () => {
  it("derives the live JTO distributor and its vault from the seeds in new_distributor.rs", () => {
    const distributor = distributorPda(PROGRAM, new PublicKey(JTO.mint), 0n)
    expect(distributor.toBase58()).toBe(JTO.distributor)
    expect(associatedTokenAddress(distributor, new PublicKey(JTO.mint)).toBase58()).toBe(JTO.vault)
    expect(checkRoundAccounts({ id: 0, program_id: JITO_DISTRIBUTOR_PROGRAM_ID, mint: JTO.mint, distributor: JTO.distributor, vault: JTO.vault })).toBe(true)
    expect(checkRoundAccounts({ id: 1, program_id: JITO_DISTRIBUTOR_PROGRAM_ID, mint: JTO.mint, distributor: JTO.distributor, vault: JTO.vault })).toBe(false)
  })

  it("derives the claim status from [\"ClaimStatus\", claimant, distributor]", () => {
    const claimant = key(JTO.first.claimant)
    const distributor = new PublicKey(JTO.distributor)
    const [expected] = PublicKey.findProgramAddressSync(
      [Buffer.from("ClaimStatus"), claimant.toBuffer(), distributor.toBuffer()],
      PROGRAM,
    )
    expect(claimStatusPda(PROGRAM, claimant, distributor).equals(expected)).toBe(true)
  })
})

describe("the new_claim instruction", () => {
  // Computed independently in Python from new_claim.rs and the IDL:
  // sha256(b"global:new_claim")[:8] + struct.pack("<QQI", 1234567, 0, 2) + proof[0] + proof[1]
  const VECTOR =
    "4eb1627bd215bb53" +
    "87d6120000000000" +
    "0000000000000000" +
    "02000000" +
    "ad8f306471cdb293e210e202e90866cf36ee6e8f5a74ef44755fffba20782fac" +
    "9e2e785ab5424b0e41b5a6ce35cc04b85fcfc9609da8c42a7965b18e3b60fd64"

  it("encodes its data byte for byte as Anchor's borsh: discriminator, u64, u64, vec<[u8; 32]>", () => {
    const data = newClaimData(1234567n, 0n, JTO.first.proof.slice(0, 2).map(hexToBytes))
    expect(bytesToHex(data)).toBe(VECTOR)
  })

  it("lists the accounts in the order and with the flags of the NewClaim struct", () => {
    const claimant = key(JTO.first.claimant)
    const mint = new PublicKey(JTO.mint)
    const distributor = new PublicKey(JTO.distributor)
    const ix = newClaimInstruction({
      programId: PROGRAM,
      distributor,
      mint,
      claimant,
      amountUnlocked: 1234567n,
      amountLocked: 0n,
      proof: JTO.first.proof.slice(0, 2).map(hexToBytes),
    })
    expect(ix.programId.equals(PROGRAM)).toBe(true)
    expect(bytesToHex(ix.data)).toBe(VECTOR)
    expect(ix.keys.map((k) => [k.pubkey.toBase58(), k.isSigner, k.isWritable])).toEqual([
      [JTO.distributor, false, true],
      [claimStatusPda(PROGRAM, claimant, distributor).toBase58(), false, true],
      [JTO.vault, false, true],
      [associatedTokenAddress(claimant, mint).toBase58(), false, true],
      [claimant.toBase58(), true, true],
      [TOKEN_PROGRAM_ID.toBase58(), false, false],
      [SystemProgram.programId.toBase58(), false, false],
    ])
  })

  it("refuses an amount that is not a u64", () => {
    expect(() => newClaimData(1n << 64n, 0n, [])).toThrow()
    expect(() => newClaimData(-1n, 0n, [])).toThrow()
  })
})
