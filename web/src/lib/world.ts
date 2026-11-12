/**
 * Teeny's world score W and growth score J, as bin/receipts publishes them in
 * `DIARY_REPO` from `hands/world report --days 7` (frozen code on the box):
 *
 *   world/latest.json
 *   {"date": "2026-11-12", "days": 7, "J": 0.1352, "penalty": 1,
 *    "components": {"attention": {"value": 85.4, "was": 70, "credit": 0.1,
 *      "weight": 1, "unit": "weighted accounts", "version": 2}, ...},
 *    "reach": {"human": {"accounts": 2, "first_contact_theirs": 2, "followers": 0}, ...}}
 *
 * The repository is public input, so the file is checked again here: only the
 * components the scorer names, only finite numbers, only a real date. Anything
 * else is dropped, never replaced by a default.
 */

import { readRaw } from './github'
import { DIARY_REPO, DIARY_REPO_URL } from './receipts'

const REVALIDATE = 600

/** The components of W, in the scorer's order (hands/lib/teeny_hands/world/score.py). */
export const COMPONENTS = ['attention', 'tiny', 'money', 'capability', 'shipped', 'world'] as const
export type ComponentName = (typeof COMPONENTS)[number]

/** Who the attention came from: a rule per surface on the box, never a guess. */
export const REACH = ['human', 'agent', 'unknown'] as const
export type ReachClass = (typeof REACH)[number]

export interface Component {
  name: ComponentName
  value: number
  was?: number
  credit?: number
  weight?: number
  unit?: string
  version?: number
}

export interface Reach {
  kind: ReachClass
  accounts?: number
  firstContactTheirs?: number
  followers?: number
}

export interface WorldScore {
  date: string
  days: number
  j: number
  penalty?: number
  components: Component[]
  reach: Reach[]
}

export type World =
  | ({ source: 'live'; url: string } & WorldScore)
  | { source: 'missing'; url: string }
  | { source: 'unavailable'; url: string }

const DATE = /^(\d{4})-(\d{2})-(\d{2})$/
const UNIT = /^[a-z ]{1,24}$|^USD$/

function realDate(value: unknown): value is string {
  if (typeof value !== 'string' || !DATE.test(value)) return false
  const d = new Date(`${value}T00:00:00Z`)
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === value
}

function finite(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= Number.MAX_SAFE_INTEGER ? value : undefined
}

function count(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : undefined
}

function object(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined
}

/** world/latest.json as the site may show it; undefined when it is not well-formed. */
export function parseWorld(text: string): WorldScore | undefined {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return undefined
  }
  const file = object(parsed)
  if (!file || !realDate(file.date)) return undefined
  const j = finite(file.J)
  const days = count(file.days)
  const raw = object(file.components)
  if (j === undefined || !days || !raw) return undefined
  const components: Component[] = []
  for (const name of COMPONENTS) {
    const item = object(raw[name])
    const value = item ? finite(item.value) : undefined
    if (!item || value === undefined) continue
    const unit = typeof item.unit === 'string' && UNIT.test(item.unit) ? item.unit : undefined
    components.push({
      name,
      value,
      was: finite(item.was),
      credit: finite(item.credit),
      weight: finite(item.weight),
      unit,
      version: count(item.version),
    })
  }
  if (!components.length) return undefined
  const reachRaw = object(file.reach) ?? {}
  const reach: Reach[] = []
  for (const kind of REACH) {
    const item = object(reachRaw[kind])
    if (!item) continue
    reach.push({
      kind,
      accounts: count(item.accounts),
      firstContactTheirs: count(item.first_contact_theirs),
      followers: count(item.followers),
    })
  }
  return { date: file.date, days, j, penalty: count(file.penalty), components, reach }
}

/** How a component reads on the page. */
export const COMPONENT_LABELS: Record<ComponentName, string> = {
  attention: 'Attention',
  tiny: '$TINY held',
  money: 'Money',
  capability: 'Capability',
  shipped: 'Shipped',
  world: 'The world (lore)',
}

/** How a reach class reads: only humans are people. */
export const REACH_LABELS: Record<ReachClass, string> = {
  human: 'People',
  agent: 'Agents',
  unknown: 'Unknown (no rule can tell)',
}

/** The published score: `missing` before there is one, `unavailable` when it cannot be read or trusted. */
export async function loadWorld(): Promise<World> {
  const url = `${DIARY_REPO_URL}/blob/main/world/latest.json`
  const read = await readRaw(DIARY_REPO, 'world/latest.json', REVALIDATE)
  if (read.status === 'missing') return { source: 'missing', url }
  const score = read.status === 'ok' ? parseWorld(read.text) : undefined
  return score ? { source: 'live', url, ...score } : { source: 'unavailable', url }
}
