import { afterEach, describe, expect, it } from "vitest"
import { loadLedger, loadTotals } from "./ledger"
import { loadMind } from "./mind"

const saved = { api: process.env.MIND_API_URL, feed: process.env.MIND_FEED_URL }

afterEach(() => {
  process.env.MIND_API_URL = saved.api
  process.env.MIND_FEED_URL = saved.feed
  if (saved.api === undefined) delete process.env.MIND_API_URL
  if (saved.feed === undefined) delete process.env.MIND_FEED_URL
})

function unset() {
  delete process.env.MIND_API_URL
  delete process.env.MIND_FEED_URL
}

describe("token totals when the ledger API is unreachable", () => {
  it("loadTotals reports no totals, never zeros, when MIND_API_URL is unset", async () => {
    unset()
    const result = await loadTotals()
    expect(result.source).toBe("unavailable")
    expect(result.totals).toBeUndefined()
  })

  it("loadTotals reports no totals when the API refuses the connection", async () => {
    process.env.MIND_API_URL = "http://127.0.0.1:9"
    const result = await loadTotals()
    expect(result.source).toBe("unavailable")
    expect(result.totals).toBeUndefined()
  })

  it("loadLedger reports no totals and no rows when unreachable", async () => {
    unset()
    const feed = await loadLedger()
    expect(feed.source).toBe("unavailable")
    expect(feed.totals).toBeUndefined()
    expect(feed.entries).toEqual([])
  })

  it("loadMind reports no totals when unreachable", async () => {
    unset()
    const feed = await loadMind()
    expect(feed.source).toBe("unavailable")
    expect(feed.totals).toBeUndefined()
  })
})
