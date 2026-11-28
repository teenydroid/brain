import { afterEach, describe, expect, it, vi } from "vitest"
import { loadDropsSnapshot, loadHomeSnapshot } from "./load-snapshot"
import { checkSnapshot, fetchSnapshot, newer, snapshotUrl } from "./snapshot"

const HOME = {
  generatedAt: "2026-11-28T12:00:00.000Z",
  anatomy: { source: "live" },
  repo: { source: "live" },
  receipts: { source: "live", latest: [], tally: [] },
  stats: { source: "live" },
}

function respond(body: unknown, status = 200): typeof fetch {
  return (async () => new Response(JSON.stringify(body), { status })) as typeof fetch
}

afterEach(() => vi.unstubAllGlobals())

describe("checkSnapshot", () => {
  it("takes a snapshot with every section", () => {
    expect(checkSnapshot("home", HOME)).toEqual(HOME)
    expect(checkSnapshot("drops", { generatedAt: HOME.generatedAt, drops: { source: "missing" } })).toBeTruthy()
  })

  it("refuses one with a section missing, the wrong type, or no valid time", () => {
    const noStats = Object.fromEntries(Object.entries(HOME).filter(([key]) => key !== "stats"))
    expect(checkSnapshot("home", noStats)).toBeUndefined()
    expect(checkSnapshot("home", { ...HOME, receipts: [] })).toBeUndefined()
    expect(checkSnapshot("home", { ...HOME, generatedAt: "yesterday" })).toBeUndefined()
    expect(checkSnapshot("home", null)).toBeUndefined()
    expect(checkSnapshot("drops", HOME)).toBeUndefined()
  })
})

describe("fetchSnapshot", () => {
  it("reads the published snapshot from the Pages repository", async () => {
    const seen: string[] = []
    const fetcher = (async (url: string) => {
      seen.push(url)
      return new Response(JSON.stringify(HOME))
    }) as unknown as typeof fetch
    expect(await fetchSnapshot("home", fetcher)).toEqual(HOME)
    expect(seen).toEqual([snapshotUrl("home")])
    expect(snapshotUrl("home")).toBe("https://raw.githubusercontent.com/teenydroid/teenydroid.github.io/main/data/home.json")
  })

  it("gives nothing, and never throws, on a failed, broken or malformed read", async () => {
    expect(await fetchSnapshot("home", respond(HOME, 404))).toBeUndefined()
    expect(await fetchSnapshot("home", respond({ generatedAt: HOME.generatedAt }))).toBeUndefined()
    const broken = (async () => new Response("<html>")) as unknown as typeof fetch
    expect(await fetchSnapshot("home", broken)).toBeUndefined()
    const down = (async () => {
      throw new TypeError("network")
    }) as unknown as typeof fetch
    expect(await fetchSnapshot("home", down)).toBeUndefined()
  })
})

describe("newer", () => {
  it("keeps what the page was built with unless the snapshot is newer", () => {
    const later = { ...HOME, generatedAt: "2026-11-28T12:15:00.000Z" }
    const earlier = { ...HOME, generatedAt: "2026-11-28T11:45:00.000Z" }
    expect(newer(HOME, later)).toBe(later)
    expect(newer(HOME, earlier)).toBe(HOME)
    expect(newer(HOME, undefined)).toBe(HOME)
  })
})

describe("building the snapshots", () => {
  it("always produces a well-formed snapshot, even with GitHub unreachable", async () => {
    vi.stubGlobal("fetch", async () => new Response("", { status: 503 }))
    const home = await loadHomeSnapshot()
    expect(checkSnapshot("home", JSON.parse(JSON.stringify(home)))).toBeTruthy()
    expect(home.receipts.source).toBe("unavailable")
    const drops = await loadDropsSnapshot()
    expect(checkSnapshot("drops", JSON.parse(JSON.stringify(drops)))).toBeTruthy()
    expect(drops.drops.source).toBe("unavailable")
  })
})
