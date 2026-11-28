'use client'

import { useEffect, useState } from 'react'
import { RewardsView } from '@/components/rewards/rewards-view'
import type { DropsSnapshot } from '@/lib/snapshot'
import { useSnapshot } from '@/lib/use-snapshot'

/**
 * /rewards: built with one snapshot, refreshed from the published one. Which
 * rounds still pay out is decided against the moment the rounds were read,
 * and once the page is open, against the visitor's clock.
 */
export function RewardsLive({ initial }: { initial: DropsSnapshot }) {
  const { drops, generatedAt } = useSnapshot('drops', initial)
  const [now, setNow] = useState(() => Date.parse(generatedAt))
  useEffect(() => {
    const tick = () => setNow(Date.now())
    const first = setTimeout(tick, 0)
    const timer = setInterval(tick, 60_000)
    return () => {
      clearTimeout(first)
      clearInterval(timer)
    }
  }, [generatedAt])
  return <RewardsView drops={drops} now={now} />
}
