/**
 * Builds the snapshots from the same loaders the pages used to call on every
 * request. Runs at build time (the pages' first render) and in
 * scripts/snapshot.ts (the JSON the browser refreshes from).
 */

import { loadAnatomy, loadRepo } from './brain-repo'
import { loadDrops } from './drops'
import { loadReceipts } from './receipts'
import type { DropsSnapshot, HomeSnapshot } from './snapshot'
import { loadStats } from './stats'

export async function loadHomeSnapshot(): Promise<HomeSnapshot> {
  const [anatomy, repo, receipts, stats] = await Promise.all([loadAnatomy(), loadRepo(), loadReceipts(), loadStats()])
  return { generatedAt: new Date().toISOString(), anatomy, repo, receipts, stats }
}

export async function loadDropsSnapshot(): Promise<DropsSnapshot> {
  return { generatedAt: new Date().toISOString(), drops: await loadDrops() }
}
