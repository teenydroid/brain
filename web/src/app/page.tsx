import { Hero } from "@/components/sections/hero"
import { HomeBrain } from "@/components/sections/home-brain"
import { HomeNumbers } from "@/components/sections/home-numbers"
import { PresenceSection } from "@/components/sections/presence"
import { XSpotlight } from "@/components/sections/x-spotlight"
import { loadAnatomy, loadRepo } from "@/lib/brain-repo"
import { loadMind } from "@/lib/mind"
import { loadReceipts } from "@/lib/receipts"
import { loadStats } from "@/lib/stats"
import { loadWallet } from "@/lib/wallet"

// Everything is read at render time and the page is rebuilt at most once a
// minute; each fetch keeps its own cache (GitHub reads for ten minutes to an
// hour, the Solana balances for five minutes, the bot's feeds for a minute).
export const revalidate = 60

export default async function Home() {
  const [feed, anatomy, repo, receipts, stats, wallet] = await Promise.all([
    loadMind(),
    loadAnatomy(),
    loadRepo(),
    loadReceipts(),
    loadStats(),
    loadWallet(),
  ])
  return (
    <>
      <Hero feed={feed} receipts={receipts} />
      <XSpotlight stats={stats} receipts={receipts} />
      <div id="numbers">
        <HomeNumbers stats={stats} wallet={wallet} />
      </div>
      <div id="brain">
        <HomeBrain anatomy={anatomy} repo={repo} receipts={receipts} />
      </div>
      <div id="find">
        <PresenceSection />
      </div>
    </>
  )
}
