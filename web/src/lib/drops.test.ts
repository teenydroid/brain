import { PublicKey } from "@solana/web3.js"
import { describe, expect, it } from "vitest"
import {
  associatedTokenAddress,
  buildTree,
  bytesToHex,
  checkRoundAccounts,
  distributionPda,
  leafHash,
  REWARDS_PROGRAM_ID,
  TOKEN_2022_PROGRAM_ID,
  verifyClaimProof,
} from "./distributor"
import {
  checkDraw,
  drawOrder,
  DropsError,
  findClaim,
  formatTiny,
  liveRound,
  parseIndex,
  parseRound,
  seedHash,
  toAtomic,
  type Round,
} from "./drops"

const MINT = "2AF7CqwieUjUPALL7icuZtL3X7wENdjUjGBMmfV2pump"
const AUTHORITY = new PublicKey("B2nP9ufqx8e2ER5hYKc5zy6CiXwHmyTYAbwC6rxnWKhE")
const DIST_SEED = new PublicKey("Fq1o1MkiE3ae9gNXgQhfJb1vuuMCDT4emUbwp8bPGBYf")
const BLOCKHASH = "4sGjMW1sUnHzSxGspuhpqLDx6wiyjNtZAMdL4VZHirAn"
const ALICE = new PublicKey("9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM")
const CAROL = new PublicKey("HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH")

/** A settled round #7 as the hand would publish it, with a real tree over its two leaves. */
function settled(): Record<string, unknown> {
  const seed = seedHash(7, BLOCKHASH)
  const handles = ["alice", "bob", "carol", "dave", "eve"]
  const order = drawOrder(seed, handles.filter((h) => h !== "dave"))
  const leaves = [
    { handle: "carol", kind: "random", address: CAROL, amount: 60_000_000_000n },
    { handle: "alice", kind: "best_reply", address: ALICE, amount: 40_000_000_000n },
  ]
  const tree = buildTree(leaves.map((l) => leafHash(l.address, l.amount)))
  const program = new PublicKey(REWARDS_PROGRAM_ID)
  const [distributor] = distributionPda(program, new PublicKey(MINT), AUTHORITY, DIST_SEED)
  return {
    id: 7,
    tag: "drop #7",
    status: "settled",
    format: "drop",
    pool_tiny: "100000",
    winners: 1,
    best_reply_tiny: "40000",
    prompt: "tell me the worst advice you ever took",
    deadline: "2026-11-20T18:00:00Z",
    post_id: "1990000000000000007",
    post_url: "https://x.com/teenydroid/status/1990000000000000007",
    cluster: "devnet",
    mint: MINT,
    decimals: 6,
    token_program: TOKEN_2022_PROGRAM_ID.toBase58(),
    program_id: REWARDS_PROGRAM_ID,
    authority: AUTHORITY.toBase58(),
    distribution_seed: DIST_SEED.toBase58(),
    distributor: distributor.toBase58(),
    vault: associatedTokenAddress(distributor, new PublicKey(MINT), TOKEN_2022_PROGRAM_ID).toBase58(),
    merkle_root: bytesToHex(tree.root),
    clawback_at: "2026-12-20T18:00:00Z",
    seed: { slot: 123456789, blockhash: BLOCKHASH, sha256: seed },
    entrants: handles.map((handle) => ({
      handle,
      eligible: handle !== "dave",
      reason: handle === "dave" ? "account under 90 days" : "ok",
    })),
    draw_order: order,
    claims: leaves.map((l, index) => ({
      index,
      handle: l.handle,
      kind: l.kind,
      address: l.address.toBase58(),
      amount_atomic: l.amount.toString(),
      proof: tree.proofs[index].map(bytesToHex),
    })),
    txs: {
      create: "5VERv8NMvzbJMEkV8xnrLkEaWRtSz9CosKDYjCJjBRnbJLgp8uirBgmQpjKhoR4tjF3ZpRzrFmBV6UjKdiSZkQUW",
    },
  }
}

const parse = (round: Record<string, unknown>) => parseRound(JSON.stringify(round))

describe("giveaways/index.json", () => {
  it("reads the rounds, newest first", () => {
    const rounds = parseIndex(
      JSON.stringify({
        rounds: [
          { id: 1, tag: "drop #1", status: "settled", deadline: "2026-11-01T18:00:00Z" },
          { id: 2, tag: "drop #2", status: "open", deadline: "2026-11-20T18:00:00Z" },
        ],
      }),
    )
    expect(rounds.map((r) => r.id)).toEqual([2, 1])
  })

  it("refuses an index that is not the contract", () => {
    for (const bad of [
      "not json",
      "{}",
      JSON.stringify({ rounds: [{ id: 1, tag: "drop 1", status: "open", deadline: "2026-11-20T18:00:00Z" }] }),
      JSON.stringify({ rounds: [{ id: 1, tag: "drop #1", status: "live", deadline: "2026-11-20T18:00:00Z" }] }),
      JSON.stringify({ rounds: [{ id: 1, tag: "drop #1", status: "open", deadline: "next friday" }] }),
      JSON.stringify({
        rounds: [
          { id: 1, tag: "drop #1", status: "open", deadline: "2026-11-20T18:00:00Z" },
          { id: 1, tag: "drop #1", status: "open", deadline: "2026-11-20T18:00:00Z" },
        ],
      }),
    ]) {
      expect(() => parseIndex(bad)).toThrow(DropsError)
    }
  })
})

describe("giveaways/drop-<id>.json", () => {
  it("reads a settled round whose every proof checks against its root and whose accounts the program derives", () => {
    const round = parse(settled())
    expect(checkRoundAccounts(round)).toBe(true)
    expect(round.tag).toBe("drop #7")
    expect(round.claims).toHaveLength(2)
    for (const c of round.claims) expect(verifyClaimProof(round.merkle_root!, c.address, c.amount_atomic, c.proof)).toBe(true)
  })

  it("reads an open round with nothing drawn yet", () => {
    const open = {
      ...settled(),
      status: "open",
      distributor: null,
      vault: null,
      merkle_root: null,
      clawback_at: null,
      seed: null,
      entrants: [],
      draw_order: [],
      claims: [],
      txs: {},
    }
    expect(parse(open).status).toBe("open")
  })

  const broken: Array<[string, (r: Record<string, unknown>) => void]> = [
    ["a tag that is not drop #<id>", (r) => (r.tag = "drop #8")],
    ["an unknown format", (r) => (r.format = "lottery")],
    ["a pool with more decimals than the mint", (r) => (r.pool_tiny = "1.0000001")],
    ["a pool that is not a number", (r) => (r.pool_tiny = "lots")],
    ["a best-reply prize bigger than the pool", (r) => (r.best_reply_tiny = "100001")],
    ["claims that add up to more than the pool", (r) => ((r.claims as Array<Record<string, unknown>>)[0].amount_atomic = "60000000001")],
    ["a claim with a bad address", (r) => ((r.claims as Array<Record<string, unknown>>)[0].address = "0xdeadbeef")],
    ["a claim with an amount over u64", (r) => ((r.claims as Array<Record<string, unknown>>)[0].amount_atomic = "18446744073709551616")],
    ["a proof element that is not 32 bytes", (r) => ((r.claims as Array<Record<string, unknown>>)[0].proof = ["abcd"])],
    ["two leaves for one address", (r) => ((r.claims as Array<Record<string, unknown>>)[1].address = CAROL.toBase58())],
    ["one account entered twice", (r) => (r.entrants as unknown[]).push({ handle: "alice", eligible: true, reason: "ok" })],
    ["a post url for another post", (r) => (r.post_url = "https://x.com/teenydroid/status/1")],
    ["a settled round with no distributor", (r) => (r.distributor = null)],
    ["a settled round with no distribution seed", (r) => (r.distribution_seed = null)],
    ["a token program that is not SPL Token or Token-2022", (r) => (r.token_program = "11111111111111111111111111111111")],
    ["a drawn round with no seed", (r) => (r.seed = null)],
    ["a seed that is not hex", (r) => (r.seed = { slot: 1, blockhash: BLOCKHASH, sha256: "zz" })],
    ["an unknown cluster", (r) => (r.cluster = "testnet")],
    ["a transaction that is not a signature", (r) => (r.txs = { fund: "nope" })],
  ]
  for (const [name, breakIt] of broken) {
    it(`refuses ${name}`, () => {
      const round = settled()
      breakIt(round)
      expect(() => parse(round)).toThrow(DropsError)
    })
  }

  it("refuses text that is not JSON at all", () => {
    expect(() => parseRound("<html>rate limited</html>")).toThrow(DropsError)
  })
})

describe("finding my leaf", () => {
  it("finds the connected wallet's claim and nobody else's", () => {
    const round = parse(settled())
    expect(findClaim(round, ALICE.toBase58())?.handle).toBe("alice")
    expect(findClaim(round, PublicKey.unique().toBase58())).toBeUndefined()
  })

  it("takes the newest open round as the live one", () => {
    const old = parse(settled())
    const open: Round = { ...old, id: 8, tag: "drop #8", status: "open" }
    expect(liveRound([open, old])?.id).toBe(8)
    expect(liveRound([old])).toBeUndefined()
  })
})

describe("the draw anyone can re-run", () => {
  it("matches the order computed independently in Python's hashlib", () => {
    // sha256("7:<blockhash>"), then handles sorted by sha256(seed bytes || handle).
    const seed = seedHash(7, BLOCKHASH)
    expect(seed).toBe("b52000ab6bf32a45edcbd06d2b3167774efb1fbdd0a53cc8d89f68ffbd5a4aa1")
    expect(drawOrder(seed, ["alice", "bob", "carol", "dave", "eve"])).toEqual(["carol", "alice", "bob", "dave", "eve"])
    expect(drawOrder(seed, ["Eve", "ALICE"])).toEqual(["alice", "eve"])
  })

  it("confirms a published draw, and catches one that was shuffled", () => {
    const round = parse(settled())
    expect(checkDraw(round)).toEqual({ seed: true, order: true, winners: true })
    const shuffled = { ...round, draw_order: [...round.draw_order].reverse() }
    expect(checkDraw(shuffled)?.order).toBe(false)
    const wrongSeed = { ...round, seed: { ...round.seed!, blockhash: "11111111111111111111111111111111" } }
    expect(checkDraw(wrongSeed)?.seed).toBe(false)
  })
})

describe("amounts", () => {
  it("converts whole $TINY to the smallest unit and back without float rounding", () => {
    expect(toAtomic("1234.5", 6)).toBe(1_234_500_000n)
    expect(toAtomic("0.0000001", 6)).toBeUndefined()
    expect(formatTiny(1_234_500_000n, 6)).toBe("1,234.5")
    expect(formatTiny(18_446_744_073_709_551_615n, 6)).toBe("18,446,744,073,709.551615")
  })
})
