import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
import { Dashboard } from "@/components/sections/dashboard"
import type { Receipts } from "@/lib/receipts"
import type { Stats } from "@/lib/stats"
import { parseWorld, type World } from "@/lib/world"

function text(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/\s+/g, " ")
}

const URL = "https://github.com/teenydroid/diary/blob/main/world/latest.json"
const SCORE = parseWorld(
  JSON.stringify({
    date: "2026-11-12",
    days: 7,
    J: 0.1352,
    penalty: 1,
    components: { attention: { value: 85.4, was: 70, unit: "weighted accounts" }, tiny: { value: 50000, unit: "tokens" } },
    reach: { human: { accounts: 2, first_contact_theirs: 2, followers: 0 }, unknown: { accounts: 1, followers: 115 } },
  }),
)!
const LIVE: World = { source: "live", url: URL, ...SCORE }
const STATS: Stats = { source: "live", url: "u", date: "2026-11-12", stats: [{ source: "x", metric: "followers", value: 115 }] }
const RECEIPTS: Receipts = { source: "live", url: "r", head: 17, latest: [], tally: [{ kind: "x.reply", count: 7 }] }

/** Every number in the visible text. */
function numbers(shown: string): string[] {
  return (shown.match(/-?\d[\d,]*(?:\.\d+)?/g) ?? []).map((n) => n.replace(/,/g, ""))
}

describe("the dashboard", () => {
  it("shows no number that is not in the published ledger", () => {
    const shown = text(renderToStaticMarkup(<Dashboard world={LIVE} stats={STATS} receipts={RECEIPTS} />))
    const ledger = new Set(["0.1352", "1", "85.4", "70", "50000", "2", "0", "115", "17", "7", "2026", "11", "12", "-11", "-12"])
    for (const n of numbers(shown)) expect(ledger, `${n} is not in the ledger`).toContain(n)
    expect(shown).toContain("J 0.1352")
    expect(shown).toContain("People")
    expect(shown).toContain("Unknown (no rule can tell)")
    expect(shown).toContain("7 replies on x")
  })

  it("shows nothing at all for what is not published, and says so", () => {
    const shown = text(
      renderToStaticMarkup(
        <Dashboard
          world={{ source: "missing", url: URL }}
          stats={{ source: "unavailable", url: "u" }}
          receipts={{ source: "unavailable", url: "r", latest: [], tally: [] }}
        />,
      ),
    )
    expect(numbers(shown)).toEqual([])
    expect(shown).toContain("Not published yet")
    expect(shown).toMatch(/Generations and their verdicts Not published yet/)
    expect(shown).toMatch(/Costs against income Not published yet/)
  })
})
