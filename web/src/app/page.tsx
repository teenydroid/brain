import { Hero } from "@/components/sections/hero"
import { HomeBrain } from "@/components/sections/home-brain"
import { HomeNumbers } from "@/components/sections/home-numbers"
import { Overview } from "@/components/sections/overview"
import { PresenceSection } from "@/components/sections/presence"
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
      <div id="numbers" className="scroll-mt-16">
        <HomeNumbers stats={stats} wallet={wallet} />
      </div>
      <div id="brain" className="scroll-mt-16">
        <HomeBrain anatomy={anatomy} repo={repo} receipts={receipts} />
      </div>
      <div id="find" className="scroll-mt-16">
        <PresenceSection />
      </div>
      <Overview />
    </>
  )
}
