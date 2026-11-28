'use client'

import { useEffect, useState } from 'react'
import { fetchSnapshot, newer, REFRESH_MS, type SnapshotName, type Snapshots } from './snapshot'

/**
 * The data a page was built with, replaced by the published snapshot once it
 * loads (and every few minutes after) whenever that snapshot is newer.
 */
export function useSnapshot<K extends SnapshotName>(name: K, initial: Snapshots[K]): Snapshots[K] {
  const [data, setData] = useState(initial)
  useEffect(() => {
    let live = true
    const read = async () => {
      const next = await fetchSnapshot(name)
      if (live) setData((current) => newer(current, next))
    }
    void read()
    const timer = setInterval(read, REFRESH_MS)
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [name])
  return data
}
