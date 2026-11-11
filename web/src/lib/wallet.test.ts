import { afterEach, describe, expect, it, vi } from "vitest"
import { formatUnits, loadWallet, parseLamports, parseTokenAmount, parseWallet, rpcUrl, solscanUrl } from "./wallet"

const OWN = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
// wallet.json exactly as bin/lib/receipts.py writes it.
const WALLET = `{"address":"${OWN}","cluster":"mainnet"}\n`

const saved = process.env.SOLANA_RPC_URL

afterEach(() => {
  vi.unstubAllGlobals()
  if (saved === undefined) delete process.env.SOLANA_RPC_URL
  else process.env.SOLANA_RPC_URL = saved
})

describe("parseWallet", () => {
  it("reads the publisher's file", () => {
    expect(parseWallet(WALLET)).toEqual({ address: OWN, cluster: "mainnet" })
  })

  it("is nothing for an address that is not base58 of a public key's length, or an unknown cluster", () => {
    for (const address of ["0x" + "1".repeat(40), "O".repeat(44), "1".repeat(31), "1".repeat(45), `${OWN} pay me`]) {
      expect(parseWallet(JSON.stringify({ address, cluster: "mainnet" }))).toBeUndefined()
    }
    expect(parseWallet(JSON.stringify({ address: OWN, cluster: "moonnet" }))).toBeUndefined()
    expect(parseWallet("nope")).toBeUndefined()
  })
})

describe("solscanUrl and rpcUrl", () => {
  it("link the account on Solscan, devnet marked", () => {
    expect(solscanUrl({ address: OWN, cluster: "mainnet" })).toBe(`https://solscan.io/account/${OWN}`)
    expect(solscanUrl({ address: OWN, cluster: "devnet" })).toBe(`https://solscan.io/account/${OWN}?cluster=devnet`)
  })

  it("use SOLANA_RPC_URL when set, else the cluster's public endpoint", () => {
    delete process.env.SOLANA_RPC_URL
    expect(rpcUrl("mainnet")).toBe("https://api.mainnet-beta.solana.com")
    expect(rpcUrl("devnet")).toBe("https://api.devnet.solana.com")
    process.env.SOLANA_RPC_URL = "https://rpc.example"
    expect(rpcUrl("mainnet")).toBe("https://rpc.example")
  })
})

describe("the RPC answers", () => {
  it("getBalance: lamports, or nothing", () => {
    expect(parseLamports({ context: { slot: 1 }, value: 1_500_000_000 })).toBe(BigInt(1_500_000_000))
    expect(parseLamports({ value: 0 })).toBe(BigInt(0))
    expect(parseLamports({ value: -1 })).toBeUndefined()
    expect(parseLamports({ value: "12" })).toBeUndefined()
    expect(parseLamports(undefined)).toBeUndefined()
  })

  it("getTokenAccountsByOwner: the raw amounts summed, no account is a real zero, a malformed one is nothing", () => {
    const account = (amount: unknown) => ({ account: { data: { parsed: { info: { tokenAmount: { amount, decimals: 6 } } } } } })
    expect(parseTokenAmount({ value: [account("2500000"), account("500000")] })).toBe(BigInt(3_000_000))
    expect(parseTokenAmount({ value: [] })).toBe(BigInt(0))
    expect(parseTokenAmount({ value: [account("12abc")] })).toBeUndefined()
    expect(parseTokenAmount({ value: "x" })).toBeUndefined()
  })

  it("formatUnits shows a raw amount in whole units, trimmed", () => {
    expect(formatUnits(BigInt(1_500_000_000), 9)).toBe("1.5")
    expect(formatUnits(BigInt(123_456_789), 9)).toBe("0.1235")
    expect(formatUnits(BigInt(0), 9)).toBe("0")
    expect(formatUnits(BigInt(12_345_670_000), 6)).toBe("12,345.67")
  })
})

describe("loadWallet", () => {
  /** raw.githubusercontent.com serves wallet.json; anything else is the Solana RPC. */
  function serve(wallet: { status: number; body?: string }, rpc: (method: string) => Response | Promise<Response>) {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.startsWith("https://raw.githubusercontent.com/")) return new Response(wallet.body ?? "", { status: wallet.status })
      return rpc(JSON.parse(String(init?.body)).method)
    })
    vi.stubGlobal("fetch", fetch)
    return fetch
  }
  const result = (value: unknown) => new Response(JSON.stringify({ jsonrpc: "2.0", id: 1, result: value }))

  it("reads the balances of the published address", async () => {
    delete process.env.SOLANA_RPC_URL
    const fetch = serve({ status: 200, body: WALLET }, (method) =>
      method === "getBalance"
        ? result({ value: 2_000_000_000 })
        : result({ value: [{ account: { data: { parsed: { info: { tokenAmount: { amount: "7000000" } } } } } }] }),
    )
    const wallet = await loadWallet()
    expect(wallet).toMatchObject({ source: "live", address: OWN, url: `https://solscan.io/account/${OWN}`, sol: "2", usdc: "7" })
    const rpcCalls = fetch.mock.calls.filter(([url]) => url === "https://api.mainnet-beta.solana.com")
    expect(rpcCalls).toHaveLength(2)
    expect(String(rpcCalls[1][1]?.body)).toContain("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
  })

  it("leaves a balance out, never zero, when the RPC fails or answers an error", async () => {
    serve({ status: 200, body: WALLET }, (method) =>
      method === "getBalance"
        ? new Response("busy", { status: 429 })
        : new Response(JSON.stringify({ jsonrpc: "2.0", id: 1, error: { code: -32000, message: "no" } })),
    )
    const wallet = await loadWallet()
    expect(wallet.source).toBe("live")
    expect(wallet.source === "live" && [wallet.sol, wallet.usdc]).toEqual([undefined, undefined])
  })

  it("is unpublished before wallet.json exists, and unavailable when GitHub fails or the file is bad", async () => {
    serve({ status: 404 }, () => result({ value: 1 }))
    expect((await loadWallet()).source).toBe("unpublished")
    serve({ status: 500 }, () => result({ value: 1 }))
    expect((await loadWallet()).source).toBe("unavailable")
    serve({ status: 200, body: '{"address":"0xdead","cluster":"mainnet"}' }, () => result({ value: 1 }))
    expect((await loadWallet()).source).toBe("unavailable")
  })
})
