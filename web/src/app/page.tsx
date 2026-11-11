import { Hero } from "@/components/sections/hero"
import { HomeBrain } from "@/components/sections/home-brain"
import { HomeLedger } from "@/components/sections/home-ledger"
import { Overview } from "@/components/sections/overview"
import { PresenceSection } from "@/components/sections/presence"
import { loadAnatomy, loadRepo } from "@/lib/brain-repo"
import { loadLedger } from "@/lib/ledger"
import { loadMind } from "@/lib/mind"
import { loadReceipts } from "@/lib/receipts"

// Everything is read at render time and the page is rebuilt at most once a
// minute; each fetch keeps its own cache (GitHub reads for ten minutes to an
// hour, the bot's feeds for a minute).
export const revalidate = 60

export default async function Home() {
  const [feed, ledger, anatomy, repo, receipts] = await Promise.all([
    loadMind(),
    loadLedger({ limit: 8 }),
    loadAnatomy(),
    loadRepo(),
    loadReceipts(),
  ])
  return (
    <>
      <Hero feed={feed} receipts={receipts} />
      <HomeBrain anatomy={anatomy} repo={repo} receipts={receipts} />
      {/* The last few model calls, only while the bot's ledger answers: unreachable, the section is left out. */}
      {ledger.source === "live" ? <HomeLedger ledger={ledger} /> : null}
      <PresenceSection />
      <Overview />
    </>
  )
}
