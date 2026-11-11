/**
 * The inference ledger: one row per model call, and the running token count.
 *
 * The bot writes a row for every call any of its surfaces makes and folds the
 * brain's samples in beside them (`src/ledger/` in the repo). Each row is what
 * the model was shown short of the system prompt, what it reasoned, what came
 * back, and what it cost. The prompt is committed to by digest and never
 * served. Nothing here is invented: when the bot cannot be reached the page
 * says so and shows nothing.
 */

import { readApi } from './api'

/** Who spent the tokens. */
export type Lane = 'mascot' | 'brain'

export interface Tokens {
  input: number
  output: number
  /** Output tokens the provider reported as reasoning. Part of `output`. */
  reasoning: number
  /** Input tokens served from cache. Part of `input`. */
  cached: number
  total: number
}

export interface LedgerMessage {
  role: 'user' | 'assistant' | 'tool'
  tool?: string
  text: string
}

export interface Inference {
  id: string
  at: string
  lane: Lane
  /** A surface on the mascot's lane (`reply`, `chart`, `judge`...), an agent id on the brain's. */
  source: string
  /** The brain's own word for the call. */
  kind?: string
  model: string
  input?: LedgerMessage[]
  reasoning?: string
  output?: string
  tokens: Tokens
  responseId?: string
  digest?: string
  promptDigest?: string
  durationMs?: number
}

export interface LaneTotals {
  calls: number
  tokens: Tokens
  first?: string
  last?: string
}

export interface Totals {
  lanes: Record<Lane, LaneTotals>
  sources: Record<string, LaneTotals>
}

export interface LedgerFeed {
  /** `unavailable` when the bot could not be reached: no totals, no rows. */
  source: 'live' | 'unavailable'
  generatedAt: string
  /** Undefined when unavailable: an unknown count is not a count of zero. */
  totals?: Totals
  entries: Inference[]
}

/**
 * The two lanes the bot keeps: `mascot` is the account itself -- every reply,
 * chart, morning post, judge and critic call -- and `brain` is the
 * brain's own agents and scheduled playbooks. The
 * counter shows them as one figure; the ledger tells them apart by source.
 */
export const LANE_META: Record<Lane, { name: string; blurb: string }> = {
  mascot: { name: 'the account', blurb: 'replies, charts, the morning post, the judge and the critic' },
  brain: { name: 'the brain', blurb: 'the agents behind the playbooks and the scheduled jobs' },
}

export const LANES: Lane[] = ['mascot', 'brain']

export function zeroTokens(): Tokens {
  return { input: 0, output: 0, reasoning: 0, cached: 0, total: 0 }
}

export function emptyTotals(): Totals {
  return {
    lanes: { mascot: { calls: 0, tokens: zeroTokens() }, brain: { calls: 0, tokens: zeroTokens() } },
    sources: {},
  }
}

/** Every token in every lane, as one figure. */
export function grandTotal(totals: Totals): { calls: number; tokens: Tokens } {
  const calls = LANES.reduce((sum, lane) => sum + totals.lanes[lane].calls, 0)
  const tokens = LANES.reduce<Tokens>(
    (sum, lane) => {
      const t = totals.lanes[lane].tokens
      return {
        input: sum.input + t.input,
        output: sum.output + t.output,
        reasoning: sum.reasoning + t.reasoning,
        cached: sum.cached + t.cached,
        total: sum.total + t.total,
      }
    },
    zeroTokens(),
  )
  return { calls, tokens }
}

/** The oldest call counted, across lanes; undefined until there is one. */
export function countingSince(totals: Totals): string | undefined {
  return LANES.map((lane) => totals.lanes[lane].first)
    .filter((v): v is string => Boolean(v))
    .sort()[0]
}

/** The newest call counted, across lanes. */
export function lastCall(totals: Totals): string | undefined {
  return LANES.map((lane) => totals.lanes[lane].last)
    .filter((v): v is string => Boolean(v))
    .sort()
    .reverse()[0]
}

/** The live ledger is GET /ledger on the bot. Unreachable, it is empty and says so. */
export async function loadLedger(options: { limit?: number; lane?: Lane } = {}): Promise<LedgerFeed> {
  const query = new URLSearchParams({ limit: String(options.limit ?? 60) })
  if (options.lane) query.set('lane', options.lane)
  const feed = await readApi<LedgerFeed>(`/ledger?${query}`, {
    check: (body) => Array.isArray((body as LedgerFeed).entries) && Boolean((body as LedgerFeed).totals),
  })
  if (!feed) return { source: 'unavailable', generatedAt: new Date().toISOString(), entries: [] }
  return { ...feed, source: 'live' }
}

export type TotalsReading = { source: 'live'; totals: Totals } | { source: 'unavailable'; totals?: undefined }

/** The totals alone, never cached: what the counter polls. Unreachable, there is no figure at all. */
export async function loadTotals(): Promise<TotalsReading> {
  const feed = await readApi<LedgerFeed>('/ledger?limit=1', {
    revalidate: 0,
    check: (body) => Boolean((body as LedgerFeed).totals),
  })
  return feed?.totals ? { source: 'live', totals: feed.totals } : { source: 'unavailable' }
}

export function formatTokens(n: number): string {
  return new Intl.NumberFormat('en-US').format(Math.round(n))
}

/** 1.2M, 38.9M, 412k: for a place where the full number would not fit. */
export function compactTokens(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 10_000) return `${Math.round(n / 1_000)}k`
  return formatTokens(n)
}
