'use client'

import { Different } from '@/components/sections/different'
import { Hero } from '@/components/sections/hero'
import { HomeBrain } from '@/components/sections/home-brain'
import { PresenceSection } from '@/components/sections/presence'
import { ReceiptsSection } from '@/components/sections/receipts'
import { XSpotlight } from '@/components/sections/x-spotlight'
import type { HomeSnapshot } from '@/lib/snapshot'
import { useSnapshot } from '@/lib/use-snapshot'

/** The home page: built with one snapshot, refreshed from the published one. */
export function HomeLive({ initial }: { initial: HomeSnapshot }) {
  const { anatomy, repo, receipts, stats } = useSnapshot('home', initial)
  return (
    <>
      <Hero receipts={receipts} />
      <XSpotlight stats={stats} receipts={receipts} />
      <Different />
      <div id="brain" className="scroll-mt-4">
        <HomeBrain anatomy={anatomy} repo={repo} />
      </div>
      <div id="find">
        <PresenceSection />
      </div>
      <ReceiptsSection receipts={receipts} />
    </>
  )
}
