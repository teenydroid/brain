/**
 * Teeny's numbers as the services report them: the newest `stats` snapshot
 * (hands/stats, one row per day per metric from X, Moltbook and GitHub),
 * which bin/receipts publishes as stats/latest.json in `DIARY_REPO`:
 *
 *   {"date": "2026-11-12", "github": {"public_repos": 4, "stars": 9},
 *    "moltbook": {"karma": 77}, "x": {"followers": 120, "posts": 301}}
 *
 * The repository is public input, so the file is checked again here: only
 * the sources and metrics below, only whole non-negative numbers, only with a
 * real date. A metric the snapshot did not carry is not shown, never zero.
 */

import { readRaw } from './github'
import { DIARY_REPO, DIARY_REPO_URL } from './receipts'

const REVALIDATE = 600

/** What the site shows, in the order it shows it: each source and its metrics. */
export const STAT_METRICS = {
  x: ['followers', 'following', 'posts'],
  moltbook: ['followers', 'karma', 'posts', 'comments', 'following'],
  github: ['stars', 'public_repos', 'followers'],
} as const

export type StatSource = keyof typeof STAT_METRICS

export interface Stat {
  source: StatSource
  metric: string
  value: number
}

export interface Snapshot {
  /** The UTC day the numbers were read: what "as of" means. */
  date: string
  stats: Stat[]
}

export type Stats =
  | ({ source: 'live'; url: string } & Snapshot)
  | { source: 'missing'; url: string }
  | { source: 'unavailable'; url: string }

const DATE = /^(\d{4})-(\d{2})-(\d{2})$/

function realDate(value: unknown): value is string {
  if (typeof value !== 'string') return false
  const match = DATE.exec(value)
  if (!match) return false
  const d = new Date(`${value}T00:00:00Z`)
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === value
}

/** stats/latest.json as the site may show it; undefined when it is not well-formed or holds nothing allowed. */
export function parseStats(text: string): Snapshot | undefined {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return undefined
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return undefined
  const file = parsed as Record<string, unknown>
  if (!realDate(file.date)) return undefined
  const stats: Stat[] = []
  for (const source of Object.keys(STAT_METRICS) as StatSource[]) {
    const metrics = file[source]
    if (!metrics || typeof metrics !== 'object' || Array.isArray(metrics)) continue
    for (const metric of STAT_METRICS[source]) {
      const value = (metrics as Record<string, unknown>)[metric]
      if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0) stats.push({ source, metric, value })
    }
  }
  return stats.length ? { date: file.date, stats } : undefined
}

/** How a metric reads: "followers on x", "public repos on github". */
export function statLabel(stat: Stat): string {
  return `${stat.metric.replace(/_/g, ' ')} on ${stat.source}`
}

/** The published snapshot: `missing` before there is one, `unavailable` when it cannot be read or trusted. */
export async function loadStats(): Promise<Stats> {
  const url = `${DIARY_REPO_URL}/blob/main/stats/latest.json`
  const read = await readRaw(DIARY_REPO, 'stats/latest.json', REVALIDATE)
  if (read.status === 'missing') return { source: 'missing', url }
  const snapshot = read.status === 'ok' ? parseStats(read.text) : undefined
  return snapshot ? { source: 'live', url, ...snapshot } : { source: 'unavailable', url }
}
