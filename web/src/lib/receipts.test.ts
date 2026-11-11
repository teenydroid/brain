import { describe, expect, it } from "vitest"
import { assemble, parseReceipts, receiptFiles, receiptUrl } from "./receipts"

// Three lines exactly as bin/lib/receipts.py writes them (canonical JSON,
// sorted keys), taken from teenydroid/diary receipts/2026/11/2026-11-11.jsonl.
const DAY_ONE = [
  '{"at":"2026-11-11T13:15:25Z","commit":"915754754d68afbc26de75229a0662880a00d700e34e8419c00f1834c6de4ce5","kind":"x.post","prev":"0000000000000000000000000000000000000000000000000000000000000000","ref":{"id":"2120505677848174620"},"seq":1}',
  '{"at":"2026-11-11T13:25:10Z","commit":"11155bef67a09ed1c76c6c7db9c3e80495490e34f6f82a327cf8dace67bdfbc2","kind":"x.reply","prev":"514e5c6cd57281f892e483a074b4af183eb3ede0eb67acbd3dc6a4b894ece50f","ref":{"id":"2120508132581339383","in_reply_to":"2120505987031355735"},"seq":2}',
  '{"at":"2026-11-11T19:58:41Z","commit":"862c8a36e1b5a8efab64f50adad9a603c94fb5ba4e7f75bcb2a4dc5e02c55da6","kind":"telegram.sent","prev":"b2dc031b5e6febe78d80fb2dac16e843a83a7ae8676a994bf72f783364891953","ref":{},"seq":3}',
].join("\n") + "\n"

const DAY_TWO = [
  '{"at":"2026-11-12T08:00:00Z","commit":"aa","kind":"x.reply","prev":"bb","ref":{"id":"2120600000000000001","in_reply_to":"2120600000000000000"},"seq":4}',
  '{"at":"2026-11-12T09:00:00Z","commit":"cc","kind":"moltbook.upvote","prev":"dd","ref":{"comment":"4cee8487-7368-4d0c-951e-cb583a10c2f9"},"seq":5}',
].join("\n") + "\n"

describe("receiptFiles", () => {
  it("keeps only the day files under receipts/, newest first", () => {
    const tree = [
      "2026",
      "2026/11",
      "2026/11/2026-11-11.jsonl",
      "2026/11/2026-11-12.jsonl",
      "2026/10",
      "2026/10/2026-10-31.jsonl",
      "2026/11/notes.md",
      "2026/11/2026-11-13.json",
    ]
    expect(receiptFiles(tree)).toEqual([
      "receipts/2026/11/2026-11-12.jsonl",
      "receipts/2026/11/2026-11-11.jsonl",
      "receipts/2026/10/2026-10-31.jsonl",
    ])
  })
})

describe("parseReceipts", () => {
  it("reads the publisher's format, newest first, each linked where it can be checked", () => {
    const receipts = parseReceipts(DAY_ONE)
    expect(receipts.map((r) => r.seq)).toEqual([3, 2, 1])
    expect(receipts[0]).toEqual({ seq: 3, at: "2026-11-11T19:58:41Z", kind: "telegram.sent", url: undefined })
    expect(receipts[1].url).toBe("https://x.com/i/status/2120508132581339383")
    expect(receipts[2].url).toBe("https://x.com/i/status/2120505677848174620")
  })

  it("skips lines that are not receipts", () => {
    expect(parseReceipts('not json\n{"seq":"1","at":"x","kind":"x.post"}\n{"seq":1,"at":"x","kind":"BAD KIND"}\n')).toEqual([])
  })
})

describe("receiptUrl", () => {
  it("refuses a ref that fails its pattern", () => {
    expect(receiptUrl("x.post", { id: "javascript:alert(1)" })).toBeUndefined()
    expect(receiptUrl("wallet.paid", { signature: "short" })).toBeUndefined()
  })

  it("links a payment to Solscan on the cluster it went out on, and an unknown cluster nowhere", () => {
    const signature = "5".repeat(40) + "abcdefghijkmnopqrstuvwxyzABCDEFGH"
    for (const cluster of ["mainnet", "mainnet-beta"]) {
      expect(receiptUrl("wallet.paid", { signature, cluster })).toBe(`https://solscan.io/tx/${signature}`)
    }
    expect(receiptUrl("wallet.paid", { signature, cluster: "devnet" })).toBe(`https://solscan.io/tx/${signature}?cluster=devnet`)
    for (const cluster of ["moonnet", undefined, "devnet&x=1", "toString"]) {
      expect(receiptUrl("wallet.paid", { signature, cluster })).toBeUndefined()
    }
  })

  it("links a comment upvote nowhere, since its post is not in the receipt", () => {
    expect(receiptUrl("moltbook.upvote", { comment: "4cee8487-7368-4d0c-951e-cb583a10c2f9" })).toBeUndefined()
  })
})

describe("assemble", () => {
  it("counts every receipt by kind and says the count is complete when it reaches the first receipt", () => {
    const result = assemble(5, [DAY_TWO, DAY_ONE], 3)
    expect(result.head).toBe(5)
    expect(result.latest.map((r) => r.seq)).toEqual([5, 4, 3])
    expect(result.tally).toEqual([
      { kind: "x.reply", count: 2 },
      { kind: "moltbook.upvote", count: 1 },
      { kind: "telegram.sent", count: 1 },
      { kind: "x.post", count: 1 },
    ])
    expect(result.window).toEqual({ from: "2026-11-11T13:15:25Z", count: 5, complete: true })
  })

  it("says the count covers only the files read when they do not reach the first receipt", () => {
    const result = assemble(5, [DAY_TWO], 5)
    expect(result.window).toEqual({ from: "2026-11-12T08:00:00Z", count: 2, complete: false })
  })

  it("never reports fewer receipts than the newest one it read (a head cached older than the files)", () => {
    expect(assemble(3, [DAY_TWO, DAY_ONE], 5).head).toBe(5)
  })

  it("has nothing to count when no file could be read", () => {
    const result = assemble(17, [], 5)
    expect(result.head).toBe(17)
    expect(result.latest).toEqual([])
    expect(result.tally).toEqual([])
    expect(result.window).toBeUndefined()
  })
})
