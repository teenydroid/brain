import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
import { ReceiptsCounter } from "@/components/receipts-counter"
import { Hero } from "@/components/sections/hero"
import { HomeBrain } from "@/components/sections/home-brain"
import { Ledger } from "@/components/sections/ledger"
import type { BrainAnatomy, RepoInfo } from "@/lib/brain-repo"
import type { LedgerFeed } from "@/lib/ledger"
import type { MindFeed } from "@/lib/mind"
import { PRESENCE } from "@/lib/presence"
import type { Receipts } from "@/lib/receipts"

/** The visible text of some markup, tags dropped. */
function text(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ")
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
const LEDGER_DOWN: LedgerFeed = { source: "unavailable", generatedAt: "2026-11-12T00:00:00Z", entries: [] }

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

  it("does not lead with the ledger while the ledger is unreachable", () => {
    const html = renderToStaticMarkup(<Hero feed={MIND_DOWN} receipts={LIVE} />)
    expect(html).not.toContain('href="/ledger"')
  })
})

describe("the ledger page", () => {
  it("puts no counts on its tabs when the bot is unreachable", () => {
    const shown = text(renderToStaticMarkup(<Ledger ledger={LEDGER_DOWN} mind={MIND_DOWN} />))
    expect(shown).not.toMatch(/Calls · 0|The account · 0|The brain · 0|Turns · 0/)
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

describe("where Teeny is", () => {
  it("lists no wallet the wallet hand does not use (its Solana address is not published)", () => {
    expect(PRESENCE.map((p) => `${p.place} ${p.href}`).join(" ")).not.toMatch(/wallet|robinhood|0x[0-9a-fA-F]{40}/i)
  })

  it("points the source link at the brain repository", () => {
    expect(PRESENCE.find((p) => p.kind === "github")?.href).toBe("https://github.com/teenydroid/brain")
  })
})
