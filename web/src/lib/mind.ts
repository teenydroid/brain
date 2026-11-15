/**
 * The journal, as the bot serves it at GET /mind: one reflection per turn.
 * Nothing here is invented: when the bot cannot be reached the page says so
 * and shows no turns.
 */

import { readApi } from './api'
import type { Totals } from './ledger'

export type Trigger = 'mention' | 'chart' | 'morning' | 'moon' | 'calendar' | 'task' | 'narrative' | 'article' | 'quote' | 'lore'
export type Tone = 'friendly' | 'neutral' | 'negative' | 'hostile' | 'confused'
export type Outcome = 'posted' | 'dry-run' | 'skipped' | 'held' | 'failed'

export interface ToolCall {
  name: string
  /** One line on what came back, never the raw payload. */
  result: string
}

export interface Reflection {
  id: string
  at: string
  trigger: Trigger
  /** What arrived. Handles are left out on purpose. */
  incoming: string
  tone: Tone
  /** What memory said about the person and the topic, summarised. */
  recalled: string[]
  tools: ToolCall[]
  /** The reasoning, in the account's own words. */
  thinking: string
  /** The reply, chart caption, or task output. */
  said: string
  /** Things it decided against, and why. */
  declined: string[]
  outcome: Outcome
  outcomeNote?: string
}

export interface MindFeed {
  /** `unavailable` when the bot could not be reached; the rest is then empty. */
  source: 'live' | 'unavailable'
  generatedAt: string
  status: {
    mode: 'langgraph' | 'simple'
    dryRun: boolean
    model: string
    queue: { pending: number; done: number; failed: number }
  }
  /** The running token count, when the bot has a ledger; undefined when unknown, never zeros. */
  totals?: Totals
  reflections: Reflection[]
}

/** The live feed is GET /mind on the bot. Unreachable, it is empty and says so. */
export async function loadMind(): Promise<MindFeed> {
  const feed = await readApi<MindFeed>('/mind', {
    check: (body) => Array.isArray((body as MindFeed).reflections),
  })
  if (!feed) {
    return {
      source: 'unavailable',
      generatedAt: new Date().toISOString(),
      status: { mode: 'langgraph', dryRun: false, model: '—', queue: { pending: 0, done: 0, failed: 0 } },
      reflections: [],
    }
  }
  return { ...feed, source: 'live' }
}
