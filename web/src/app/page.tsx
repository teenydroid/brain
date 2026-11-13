import { Hero } from "@/components/sections/hero"
import { HomeBrain } from "@/components/sections/home-brain"
import { PresenceSection } from "@/components/sections/presence"
import { RaceSection } from "@/components/sections/race"
import { ReceiptsSection } from "@/components/sections/receipts"
import { XSpotlight } from "@/components/sections/x-spotlight"
import { loadAnatomy, loadRepo } from "@/lib/brain-repo"
import { loadDirective } from "@/lib/directive"
import { loadMind } from "@/lib/mind"
import { loadReceipts } from "@/lib/receipts"
import { loadStats } from "@/lib/stats"

// Everything is read at render time and the page is rebuilt at most once a
// minute; each fetch keeps its own cache (GitHub reads for ten minutes to an
// hour, the bot's feeds for a minute).
export const revalidate = 60

export default async function Home() {
  const [feed, anatomy, repo, receipts, stats, directive] = await Promise.all([
    loadMind(),
    loadAnatomy(),
    loadRepo(),
    loadReceipts(),
    loadStats(),
    loadDirective(),
  ])
  return (
    <>
      <Hero feed={feed} receipts={receipts} />
      <XSpotlight stats={stats} receipts={receipts} />
      <RaceSection directive={directive} repo={repo} />
      <div id="brain" className="scroll-mt-4">
        <HomeBrain anatomy={anatomy} />
      </div>
      <div id="find">
        <PresenceSection />
      </div>
      <ReceiptsSection feed={feed} receipts={receipts} />
    </>
  )
}
