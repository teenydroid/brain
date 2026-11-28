/**
 * The site's data as JSON snapshots. GitHub Pages serves the page as plain
 * files, so nothing is read at request time any more: the pages render once
 * at build time, and the browser then refreshes them from these snapshots.
 *
 * teenydroid/teenydroid.github.io's build workflow runs scripts/snapshot.ts
 * every fifteen minutes and commits its output there under data/. The browser
 * reads it straight from GitHub (raw.githubusercontent.com answers any origin
 * and caches for about five minutes).
 *
 * A snapshot is public input like everything else the page reads: one that
 * does not have the expected shape is ignored and the page keeps what it was
 * built with.
 */

import type { BrainAnatomy, RepoInfo } from './brain-repo'
import type { Drops } from './drops'
import type { Receipts } from './receipts'
import type { Stats } from './stats'

export const PAGES_REPO = 'teenydroid/teenydroid.github.io'

/** Where the snapshots live. `NEXT_PUBLIC_DATA_URL` overrides it (inlined at build time). */
export const DATA_BASE =
  process.env.NEXT_PUBLIC_DATA_URL ?? `https://raw.githubusercontent.com/${PAGES_REPO}/main/data`

/** How often an open page reads the snapshot again. */
export const REFRESH_MS = 5 * 60_000

export type HomeSnapshot = {
  generatedAt: string
  anatomy: BrainAnatomy
  repo: RepoInfo
  receipts: Receipts
  stats: Stats
}

export type DropsSnapshot = {
  generatedAt: string
  drops: Drops
}

export type Snapshots = { home: HomeSnapshot; drops: DropsSnapshot }
export type SnapshotName = keyof Snapshots

/** The top-level keys each snapshot must carry, every one an object. */
const KEYS: { [K in SnapshotName]: (keyof Snapshots[K])[] } = {
  home: ['anatomy', 'repo', 'receipts', 'stats'],
  drops: ['drops'],
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** The snapshot if it has the expected shape, otherwise undefined. */
export function checkSnapshot<K extends SnapshotName>(name: K, body: unknown): Snapshots[K] | undefined {
  if (!isObject(body) || typeof body.generatedAt !== 'string' || Number.isNaN(Date.parse(body.generatedAt))) {
    return undefined
  }
  for (const key of KEYS[name]) {
    if (!isObject(body[key as string])) return undefined
  }
  return body as Snapshots[K]
}

export function snapshotUrl(name: SnapshotName): string {
  return `${DATA_BASE.replace(/\/$/, '')}/${name}.json`
}

/** One read of a snapshot: undefined on any failure, never a throw. */
export async function fetchSnapshot<K extends SnapshotName>(
  name: K,
  fetcher: typeof fetch = fetch,
): Promise<Snapshots[K] | undefined> {
  try {
    const response = await fetcher(snapshotUrl(name), { cache: 'no-store', signal: AbortSignal.timeout(8_000) })
    if (!response.ok) return undefined
    return checkSnapshot(name, (await response.json()) as unknown)
  } catch {
    return undefined
  }
}

/**
 * The newer of two snapshots. A stale CDN copy must never replace the data
 * the page was built with.
 */
export function newer<T extends { generatedAt: string }>(current: T, next: T | undefined): T {
  if (!next) return current
  return Date.parse(next.generatedAt) > Date.parse(current.generatedAt) ? next : current
}
