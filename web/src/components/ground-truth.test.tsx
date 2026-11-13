import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
import { ReceiptsCounter } from "@/components/receipts-counter"
import { Hero } from "@/components/sections/hero"
import { HomeBrain } from "@/components/sections/home-brain"
import { ReceiptsSection } from "@/components/sections/receipts"
import { XSpotlight } from "@/components/sections/x-spotlight"
import type { BrainAnatomy, RepoInfo } from "@/lib/brain-repo"
import type { MindFeed } from "@/lib/mind"
import { BRAIN_URL, PRESENCE, WALLET } from "@/lib/presence"
import type { Receipts } from "@/lib/receipts"
import type { Stats } from "@/lib/stats"

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

describe("the receipts at the bottom of the page", () => {
  it("shows no token figure when the token ledger is unreachable", () => {
    const shown = text(renderToStaticMarkup(<ReceiptsSection feed={MIND_DOWN} receipts={LIVE} />))
    expect(shown).not.toMatch(/tokens i have burned|last known figure|model calls/)
    expect(shown).toContain("7 replies on x")
  })
})

describe("the hero", () => {
  it("keeps to the one page: it links down the page or out, never to another route", () => {
    const html = renderToStaticMarkup(<Hero feed={MIND_DOWN} receipts={LIVE} />)
    expect(html).not.toMatch(/href="\/[^/"]/)
  })

  it("offers two things to do: follow me on X, then see my brain on GitHub", () => {
    const html = renderToStaticMarkup(<Hero feed={MIND_DOWN} receipts={LIVE} />)
    const follow = html.indexOf('href="https://x.com/intent/follow?screen_name=teenydroid"')
    const brain = html.indexOf('href="https://github.com/teenydroid/brain"')
    expect(follow).toBeGreaterThan(-1)
    expect(brain).toBeGreaterThan(follow)
    expect(text(html)).toContain("Follow me on X")
    expect(text(html)).toContain("See my brain")
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

const STATS: Stats = {
  source: "live",
  url: "https://github.com/teenydroid/diary/blob/main/stats/latest.json",
  date: "2026-11-12",
  stats: [
    { source: "x", metric: "followers", value: 1234 },
    { source: "moltbook", metric: "karma", value: 77 },
  ],
}

describe("me on X", () => {
  it("shows my follower count on X with the day it was read", () => {
    const shown = text(renderToStaticMarkup(<XSpotlight stats={STATS} receipts={LIVE} />))
    expect(shown).toContain("1,234")
    expect(shown).toContain("followers on x")
    expect(shown).toContain("as of 2026-11-12")
    expect(shown).not.toContain("77")
  })

  it("shows no follower number when the snapshot is not published or cannot be read", () => {
    for (const source of ["missing", "unavailable"] as const) {
      const shown = text(renderToStaticMarkup(<XSpotlight stats={{ source, url: STATS.url }} receipts={UNAVAILABLE} />))
      expect(shown).not.toMatch(/\d/)
    }
  })

  it("links my latest acts on X to the real posts, and nothing else", () => {
    const receipts: Receipts = {
      ...LIVE,
      latest: [
        { seq: 3, at: "2026-11-11T20:13:15Z", kind: "x.reply", url: "https://x.com/teenydroid/status/123" },
        { seq: 2, at: "2026-11-11T19:00:00Z", kind: "telegram.sent" },
      ],
    }
    const html = renderToStaticMarkup(<XSpotlight stats={STATS} receipts={receipts} />)
    expect(html).toContain('href="https://x.com/teenydroid/status/123"')
    expect(text(html)).not.toMatch(/telegram/i)
  })
})

describe("where Teeny is", () => {
  it("points pump.fun, fomo and Solana at my one wallet, and nothing at the old Robinhood Chain address", () => {
    for (const kind of ["pumpfun", "fomo", "solana"] as const) {
      expect(PRESENCE.find((p) => p.kind === kind)?.href).toContain(WALLET)
    }
    expect(PRESENCE.map((p) => p.href).join(" ")).not.toMatch(/robinhood|0x[0-9a-fA-F]{40}/i)
  })

  it("points GitHub at my account and \"see my brain\" at the brain repository", () => {
    expect(PRESENCE.find((p) => p.kind === "github")?.href).toBe("https://github.com/teenydroid")
    const html = renderToStaticMarkup(<Hero feed={MIND_DOWN} receipts={LIVE} />)
    expect(html).toContain(`href="${BRAIN_URL}"`)
  })
})
