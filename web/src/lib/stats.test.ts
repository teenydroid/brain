import { afterEach, describe, expect, it, vi } from "vitest"
import { loadStats, parseStats, statLabel } from "./stats"

// stats/latest.json exactly as bin/lib/receipts.py writes it (canonical JSON, sorted keys).
const LATEST =
  '{"date":"2026-11-12","github":{"public_repos":4,"stars":9},"moltbook":{"karma":77},"x":{"followers":120,"following":40,"posts":301}}\n'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("parseStats", () => {
  it("reads the publisher's file: the snapshot date and every allowed metric, in a fixed order", () => {
    expect(parseStats(LATEST)).toEqual({
      date: "2026-11-12",
      stats: [
        { source: "x", metric: "followers", value: 120 },
        { source: "x", metric: "following", value: 40 },
        { source: "x", metric: "posts", value: 301 },
        { source: "moltbook", metric: "karma", value: 77 },
        { source: "github", metric: "stars", value: 9 },
        { source: "github", metric: "public_repos", value: 4 },
      ],
    })
  })

  it("drops anything not a whole, non-negative number for an allowed source and metric", () => {
    const parsed = parseStats(
      JSON.stringify({
        date: "2026-11-12",
        x: { followers: 120, impressions: 5000, posts: "301", following: -1 },
        moltbook: { karma: 1.5, followers: 1e300 },
        evil: { followers: 1 },
        github: "stars",
      }),
    )
    expect(parsed?.stats).toEqual([{ source: "x", metric: "followers", value: 120 }])
  })

  it("is nothing at all without a valid date, or with no allowed metric", () => {
    expect(parseStats('{"date":"yesterday","x":{"followers":1}}')).toBeUndefined()
    expect(parseStats('{"date":"2026-13-45","x":{"followers":1}}')).toBeUndefined()
    expect(parseStats('{"date":"2026-11-12","evil":{"followers":1}}')).toBeUndefined()
    expect(parseStats("not json")).toBeUndefined()
    expect(parseStats("[1,2]")).toBeUndefined()
  })
})

describe("statLabel", () => {
  it("names each metric and where it was counted", () => {
    expect(statLabel({ source: "x", metric: "followers", value: 1 })).toBe("followers on x")
    expect(statLabel({ source: "github", metric: "public_repos", value: 1 })).toBe("public repos on github")
    expect(statLabel({ source: "moltbook", metric: "karma", value: 1 })).toBe("karma on moltbook")
  })
})

describe("loadStats", () => {
  function serve(status: number, body = "") {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status })))
  }

  it("is live with the published snapshot", async () => {
    serve(200, LATEST)
    const stats = await loadStats()
    expect(stats.source).toBe("live")
    expect(stats.source === "live" && stats.date).toBe("2026-11-12")
  })

  it("is missing, not zero, before the first snapshot is published", async () => {
    serve(404)
    expect((await loadStats()).source).toBe("missing")
  })

  it("is unavailable when GitHub does not answer or the file is not well-formed", async () => {
    serve(503)
    expect((await loadStats()).source).toBe("unavailable")
    serve(200, '{"date":"2026-11-12"}')
    expect((await loadStats()).source).toBe("unavailable")
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new Error("down"))))
    expect((await loadStats()).source).toBe("unavailable")
  })
})
