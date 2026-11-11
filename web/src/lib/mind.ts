/**
 * The journal, as the bot serves it at GET /mind: one reflection per turn.
 * Nothing here is invented: when the bot cannot be reached the page says so
 * and shows no turns. The lessons below are the exceptions that are not a
 * feed at all -- each is an incident documented in the repo's own history.
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

export interface Lesson {
  date: string
  title: string
  whatHappened: string
  whatChanged: string
  /** Where in the code the lesson now lives. */
  where: string
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

export const LESSONS: Lesson[] = [
  {
    date: '2026-09-08',
    title: 'The wallet worked. The speed did not.',
    whatHappened:
      'An airdrop tool went in and came out the same day. The caps held, one wallet could not farm many accounts, 106 airdrops went out with nothing overpaid. What went wrong was a hundred near-identical reply-and-pay posts in an hour, which is the shape of spam whatever the intent. X throttled the account for it.',
    whatChanged:
      'The tool is gone, the ledger and policy code stayed. A capabilities test asserts the exact tool list, so a wallet can only ever come back as a deliberate decision. Replies are now paced on a random gap.',
    where: 'test/capabilities.test.ts, src/jobs/reply-job.ts',
  },
  {
    date: '2026-09-09',
    title: '"I am just a mascot who does not read charts"',
    whatHappened:
      'Asked "much higher for tiny?", the account played dumb about the market, hours after publishing a chart of its own token. It had no idea what it had posted, because nothing ever told it.',
    whatChanged:
      'Every reply now recalls what the account itself has been doing lately, not only what is relevant to the post. It answers with what the market has already done, and never with what it will do.',
    where: 'src/harness/reply-harness.ts, CONTEXT.md',
  },
  {
    date: '2026-09-09',
    title: 'Mistaking its own token for a shill',
    whatHappened:
      'Somebody replied to one of the account\'s posts suggesting it buy more $TINY, with the contract address. Seeing only that reply, the account called it a shill. The root post was the whole story and nothing had fetched it.',
    whatChanged:
      'Reading the thread is deterministic now, not a tool the model may or may not reach for. Contract addresses are read against the account\'s own list before anything else.',
    where: 'src/harness/reply-harness.ts, src/agent/own-token.ts',
  },
  {
    date: '2026-09-10',
    title: 'Sour posts get an answer, never a memory',
    whatHappened:
      'Memory is what the account carries into every future conversation. An insult stored today coloured what it said to someone else next week.',
    whatChanged:
      'Negative and hostile mentions are answered but not captured. There is a forget command for anything that got through.',
    where: 'src/harness/reply-harness.ts, src/scripts/forget.ts',
  },
  {
    date: '2026-09-11',
    title: 'A number from memory is a wrong number',
    whatHappened: 'Price, market cap and burn totals move constantly, and a figure recalled from a previous turn was stale by the time it was repeated.',
    whatChanged:
      'Those numbers are deliberately absent from what the account is allowed to treat as known. It looks them up with a tool, or says it does not know.',
    where: 'CONTEXT.md, src/agent/tools/market.ts',
  },
  {
    date: '2026-09-12',
    title: 'Lead with the fact, never the disclaimer',
    whatHappened: 'Replies were opening with "I cannot predict prices, but" before saying anything useful, which reads as a press release.',
    whatChanged: 'The fact comes first. The disclaimer, if needed at all, comes after and stays short.',
    where: 'src/agent/prompt.ts',
  },
]

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
