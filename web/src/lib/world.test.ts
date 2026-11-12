import { afterEach, describe, expect, it, vi } from "vitest"
import { loadWorld, parseWorld } from "./world"

// world/latest.json exactly as bin/lib/receipts.py writes it (canonical JSON, sorted keys).
const LATEST =
  '{"J":0.1352,"components":{"attention":{"credit":0.1,"unit":"weighted accounts","value":85.4,"version":2,"was":70,"weight":1.0},' +
  '"money":{"unit":"USD","value":-112.5,"was":-80.25,"weight":0.6},"tiny":{"unit":"tokens","value":50000,"weight":0.8}},' +
  '"date":"2026-11-12","days":7,"penalty":1,"reach":{"agent":{"accounts":1,"first_contact_theirs":0,"followers":49},' +
  '"human":{"accounts":2,"first_contact_theirs":2,"followers":0},"unknown":{"accounts":1,"first_contact_theirs":1,"followers":115}}}\n'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("parseWorld", () => {
  it("reads the publisher's file: J, the components in the scorer's order, and the reach", () => {
    const got = parseWorld(LATEST)
    expect(got?.j).toBe(0.1352)
    expect(got?.penalty).toBe(1)
    expect(got?.components.map((c) => c.name)).toEqual(["attention", "tiny", "money"])
    expect(got?.components[0]).toEqual({ name: "attention", value: 85.4, was: 70, credit: 0.1, weight: 1, unit: "weighted accounts", version: 2 })
    expect(got?.reach.map((r) => [r.kind, r.accounts, r.firstContactTheirs])).toEqual([
      ["human", 2, 2],
      ["agent", 1, 0],
      ["unknown", 1, 1],
    ])
  })

  it("drops what the scorer does not name and any number that is not finite", () => {
    const got = parseWorld(
      JSON.stringify({
        date: "2026-11-12",
        days: 7,
        J: 0.1,
        components: { attention: { value: "85" }, evil: { value: 1 }, tiny: { value: 5, unit: "<script>" } },
        reach: { human: { accounts: -1 }, bots: { accounts: 9 } },
      }),
    )
    expect(got?.components).toEqual([{ name: "tiny", value: 5, unit: undefined, was: undefined, credit: undefined, weight: undefined, version: undefined }])
    expect(got?.reach).toEqual([{ kind: "human", accounts: undefined, firstContactTheirs: undefined, followers: undefined }])
  })

  it("is nothing at all without a date, a J or any component", () => {
    expect(parseWorld('{"date":"2026-11-12","days":7,"components":{"tiny":{"value":1}}}')).toBeUndefined()
    expect(parseWorld('{"date":"yesterday","days":7,"J":1,"components":{"tiny":{"value":1}}}')).toBeUndefined()
    expect(parseWorld('{"date":"2026-11-12","days":7,"J":1,"components":{}}')).toBeUndefined()
    expect(parseWorld("not json")).toBeUndefined()
  })
})

describe("loadWorld", () => {
  function serve(status: number, body = "") {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status })))
  }

  it("is live from the published file, missing before one, unavailable when unreadable", async () => {
    serve(200, LATEST)
    expect((await loadWorld()).source).toBe("live")
    serve(404)
    expect((await loadWorld()).source).toBe("missing")
    serve(200, "{}")
    expect((await loadWorld()).source).toBe("unavailable")
  })
})
