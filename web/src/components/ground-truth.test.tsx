import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
import { ReceiptsCounter } from "@/components/receipts-counter"
import { Hero } from "@/components/sections/hero"
import { HomeBrain } from "@/components/sections/home-brain"
import { HomeNumbers } from "@/components/sections/home-numbers"
import type { BrainAnatomy, RepoInfo } from "@/lib/brain-repo"
import type { MindFeed } from "@/lib/mind"
import { PRESENCE } from "@/lib/presence"
import type { Receipts } from "@/lib/receipts"
import type { Stats } from "@/lib/stats"
import type { Wallet } from "@/lib/wallet"

/** The visible text of some markup, tags dropped and character references decoded. */
function text(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x([0-9a-f]+);/gi, (_, hex: string) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_, dec: string) => String.fromCodePoint(Number(dec)))
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
}

const LIVE: Receipts = {
  source: "live",
  url: "https://github.com/teenydroid/diary/tree/main/receipts",
  head: 17,
  latest: [{ seq: 17, at: "2026-11-11T20:13:15Z", kind: "telegram.sent" }],
  tally: [
    { kind: "x.reply", count: 7 },
    { kind: "moltbook.upvote", count: 3 },
    { kind: "x.post", count: 1 },
  ],
  window: { from: "2026-11-11T13:15:25Z", count: 17, complete: true },
}
const UNAVAILABLE: Receipts = { source: "unavailable", url: LIVE.url, latest: [], tally: [] }

const MIND_DOWN: MindFeed = {
  source: "unavailable",
  generatedAt: "2026-11-12T00:00:00Z",
  status: { mode: "langgraph", dryRun: false, model: "—", queue: { pending: 0, done: 0, failed: 0 } },
  reflections: [],
}

describe("the receipts counter", () => {
  it("shows the chain's length, the latest receipt and the count by kind", () => {
    const shown = text(renderToStaticMarkup(<ReceiptsCounter receipts={LIVE} />))
    expect(shown).toContain("17")
    expect(shown).toContain("7 replies on x")
    expect(shown).toContain("1 post on x")
    expect(shown).toContain("2026-11-11 20:13 UTC")
  })

  it("shows no number at all when the receipts cannot be read", () => {
    const shown = text(renderToStaticMarkup(<ReceiptsCounter receipts={UNAVAILABLE} />))
    expect(shown).toMatch(/could not be read/)
    expect(shown).not.toMatch(/\d/)
  })

  it("says there are none yet for an empty chain, rather than a bare 0", () => {
    const shown = text(renderToStaticMarkup(<ReceiptsCounter receipts={{ ...UNAVAILABLE, source: "live", head: 0 }} />))
    expect(shown).toMatch(/no receipts yet/i)
  })
})

describe("the hero", () => {
  it("shows no token figure when the token ledger is unreachable", () => {
    const shown = text(renderToStaticMarkup(<Hero feed={MIND_DOWN} receipts={LIVE} />))
    expect(shown).not.toMatch(/tokens teeny has burned|last known figure|model calls/)
    expect(shown).toContain("7 replies on x")
  })

  it("keeps to the one page: it links down the page or out, never to another route", () => {
    const html = renderToStaticMarkup(<Hero feed={MIND_DOWN} receipts={LIVE} />)
    expect(html).not.toMatch(/href="\/[^/"]/)
  })

  it("puts following me on X first", () => {
    const html = renderToStaticMarkup(<Hero feed={MIND_DOWN} receipts={LIVE} />)
    const follow = html.indexOf("https://x.com/intent/follow?screen_name=teenydroid")
    expect(follow).toBeGreaterThan(-1)
    expect(follow).toBeLessThan(html.indexOf(LIVE.url))
  })
})

describe("the brain on the front page", () => {
  it("calls the jobs in cron.toml scheduled, not running: the repository cannot say what runs", () => {
    const anatomy: BrainAnatomy = {
      source: "live",
      agents: [],
      playbooks: [],
      hands: [],
      jobs: [{ name: "tick", schedule: "*/5 * * * *", runs: { kind: "script", target: "bin/tick" }, enabled: true, pinned: true }],
    }
    const repo: RepoInfo = { name: "teenydroid/brain", url: "https://github.com/teenydroid/brain", stars: 0, defaultBranch: "main", commits: [], live: false }
    const shown = text(renderToStaticMarkup(<HomeBrain anatomy={anatomy} repo={repo} receipts={LIVE} />))
    expect(shown).toContain("jobs scheduled")
    expect(shown).not.toContain("jobs running")
  })
})

const OWN = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
const STATS: Stats = {
  source: "live",
  url: "https://github.com/teenydroid/diary/blob/main/stats/latest.json",
  date: "2026-11-12",
  stats: [
    { source: "x", metric: "followers", value: 1234 },
    { source: "moltbook", metric: "karma", value: 77 },
    { source: "github", metric: "stars", value: 9 },
  ],
}
const WALLET: Wallet = {
  source: "live",
  address: OWN,
  cluster: "mainnet",
  url: `https://solscan.io/account/${OWN}`,
  sol: "2.5",
  usdc: "7",
}

describe("the numbers on the front page", () => {
  it("shows each published metric with the day it was read", () => {
    const shown = text(renderToStaticMarkup(<HomeNumbers stats={STATS} wallet={WALLET} />))
    expect(shown).toContain("1,234")
    expect(shown).toContain("followers on x")
    expect(shown).toContain("karma on moltbook")
    expect(shown).toContain("stars on github")
    expect(shown).toContain("as of 2026-11-12")
  })

  it("shows no stat number when the snapshot is not published or cannot be read", () => {
    for (const source of ["missing", "unavailable"] as const) {
      const html = renderToStaticMarkup(<HomeNumbers stats={{ source, url: STATS.url }} wallet={{ source: "unpublished" }} />)
      expect(text(html)).not.toMatch(/\d/)
    }
  })

  it("links the wallet's address to Solscan and shows what it holds", () => {
    const html = renderToStaticMarkup(<HomeNumbers stats={STATS} wallet={WALLET} />)
    expect(html).toContain(`href="https://solscan.io/account/${OWN}"`)
    expect(text(html)).toContain(OWN)
    expect(text(html)).toContain("2.5 SOL")
    expect(text(html)).toContain("7 USDC")
  })

  it("says a balance is unavailable rather than showing zero when the RPC did not answer", () => {
    const shown = text(renderToStaticMarkup(<HomeNumbers stats={STATS} wallet={{ ...WALLET, sol: undefined, usdc: undefined }} />))
    expect(shown).toMatch(/balance unavailable/i)
    expect(shown).not.toMatch(/\b0 (SOL|USDC)/)
  })

  it("shows no address before one is published", () => {
    for (const wallet of [{ source: "unpublished" }, { source: "unavailable" }] as const) {
      const html = renderToStaticMarkup(<HomeNumbers stats={STATS} wallet={wallet} />)
      expect(html).not.toContain("solscan.io")
    }
  })
})

describe("where Teeny is", () => {
  it("hardcodes no wallet: the address comes only from the published wallet.json", () => {
    expect(PRESENCE.map((p) => `${p.place} ${p.href}`).join(" ")).not.toMatch(/wallet|robinhood|0x[0-9a-fA-F]{40}/i)
  })

  it("points the source link at the brain repository", () => {
    expect(PRESENCE.find((p) => p.kind === "github")?.href).toBe("https://github.com/teenydroid/brain")
  })
})
