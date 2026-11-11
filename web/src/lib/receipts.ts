/**
 * Teeny's receipts: the public, ids-only ledger bin/receipts publishes to
 * teenydroid/diary (`DIARY_REPO`). Every action a hand recorded as done is one
 * line of receipts/YYYY/MM/YYYY-MM-DD.jsonl (the UTC day it was published),
 * hash-chained, with the chain head in head.json (bin/lib/receipts.py has the
 * format). The count shown anywhere on the site is the head's seq; nothing is
 * counted that is not a line in that repository.
 */

import { listTree, readRaw } from './github'

export const DIARY_REPO = process.env.DIARY_REPO ?? 'teenydroid/diary'
export const DIARY_REPO_URL = `https://github.com/${DIARY_REPO}`

const REVALIDATE = 600

export interface Receipt {
  seq: number
  at: string
  kind: string
  /** Where anyone can check it; undefined for a commitment-only receipt (mail, Telegram). */
  url?: string
}

const X_ID = /^\d{1,25}$/
const SAFE_ID = /^[A-Za-z0-9_-]{1,64}$/
const MOLTBOOK_NAME = /^[a-z0-9_-]{1,40}$/
const REPO = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9._-]{1,100}$/
const SHA = /^[0-9a-f]{7,40}$/
const SOLANA_SIG = /^[1-9A-HJ-NP-Za-km-z]{64,90}$/

/** The public link for one receipt's ref. Every field is checked again here: the repository is public input. */
export function receiptUrl(kind: string, ref: Record<string, unknown>): string | undefined {
  const s = (key: string, pattern: RegExp) => {
    const value = ref[key]
    return typeof value === 'string' && pattern.test(value) ? value : undefined
  }
  const n = (key: string) => {
    const value = ref[key]
    return typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? value : undefined
  }
  const repo = s('repo', REPO)
  switch (kind) {
    case 'x.post':
    case 'x.reply':
    case 'x.quote':
    case 'x.like': {
      const id = s('id', X_ID)
      return id && `https://x.com/i/status/${id}`
    }
    case 'bounty.opened':
    case 'bounty.picked': {
      const id = s('x_post', X_ID)
      return id && `https://x.com/i/status/${id}`
    }
    case 'moltbook.post': {
      const id = s('id', SAFE_ID)
      return id && `https://www.moltbook.com/post/${id}`
    }
    case 'moltbook.comment':
    case 'moltbook.upvote': {
      const post = s('post', SAFE_ID)
      return post && `https://www.moltbook.com/post/${post}`
    }
    case 'moltbook.follow': {
      const agent = s('agent', MOLTBOOK_NAME)
      return agent && `https://www.moltbook.com/u/${agent}`
    }
    case 'moltbook.subscribe': {
      const submolt = s('submolt', MOLTBOOK_NAME)
      return submolt && `https://www.moltbook.com/m/${submolt}`
    }
    case 'wallet.paid': {
      const sig = s('signature', SOLANA_SIG)
      const cluster = ref.cluster === 'devnet' || ref.cluster === 'testnet' ? `?cluster=${ref.cluster}` : ''
      return sig && `https://solscan.io/tx/${sig}${cluster}`
    }
    case 'github.push':
    case 'build.shipped': {
      const sha = s('sha', SHA)
      return repo && sha && `https://github.com/${repo}/commit/${sha}`
    }
    case 'github.open-pr': {
      const number = n('number')
      return repo && number ? `https://github.com/${repo}/pull/${number}` : undefined
    }
    case 'github.comment': {
      const number = n('number')
      const id = n('id')
      return repo && number && id ? `https://github.com/${repo}/issues/${number}#issuecomment-${id}` : undefined
    }
    case 'github.create-repo':
    case 'github.fork':
      return repo && `https://github.com/${repo}`
    default:
      return undefined
  }
}

/** One receipts file, newest receipt first; lines that are not well-formed receipts are skipped. */
export function parseReceipts(text: string): Receipt[] {
  const out: Receipt[] = []
  for (const line of text.split('\n')) {
    if (!line.trim()) continue
    let entry: unknown
    try {
      entry = JSON.parse(line)
    } catch {
      continue
    }
    if (!entry || typeof entry !== 'object') continue
    const { seq, at, kind, ref } = entry as Record<string, unknown>
    if (typeof seq !== 'number' || typeof at !== 'string' || typeof kind !== 'string' || !/^[a-z.-]{1,40}$/.test(kind)) continue
    const url = ref && typeof ref === 'object' ? receiptUrl(kind, ref as Record<string, unknown>) : undefined
    out.push({ seq, at, kind, url })
  }
  return out.reverse()
}

const DAY_FILE = /^(\d{4})\/(\d{2})\/(\d{4})-(\d{2})-\d{2}\.jsonl$/

/** The day files from a listing of receipts/, as repository paths, newest first. */
export function receiptFiles(tree: string[]): string[] {
  return tree
    .filter((path) => {
      const match = DAY_FILE.exec(path)
      return Boolean(match && match[1] === match[3] && match[2] === match[4])
    })
    .sort()
    .reverse()
    .map((path) => `receipts/${path}`)
}

export interface Assembled {
  /** How many receipts the chain holds: head.json's seq, or the newest seq read if that is higher. */
  head: number
  /** The newest few, newest first. */
  latest: Receipt[]
  /** Receipts per kind over every file read, biggest first. */
  tally: Array<{ kind: string; count: number }>
  /** What the tally covers: since when, how many, and whether that reaches the first receipt. */
  window?: { from: string; count: number; complete: boolean }
}

/** The chain head plus the text of the newest day files (newest first), as the site shows them. */
export function assemble(head: number, files: string[], latest: number): Assembled {
  const receipts = files.flatMap((text) => parseReceipts(text))
  if (!receipts.length) return { head, latest: [], tally: [] }
  const counts = new Map<string, number>()
  for (const r of receipts) counts.set(r.kind, (counts.get(r.kind) ?? 0) + 1)
  const tally = [...counts.entries()]
    .map(([kind, count]) => ({ kind, count }))
    .sort((a, b) => b.count - a.count || a.kind.localeCompare(b.kind))
  const oldest = receipts[receipts.length - 1]
  return {
    head: Math.max(head, receipts[0].seq),
    latest: receipts.slice(0, latest),
    tally,
    window: { from: oldest.at, count: receipts.length, complete: oldest.seq === 1 },
  }
}

export interface Receipts extends Assembled {
  /** `unavailable` when GitHub did not answer for head.json: then there is no count at all. */
  source: 'live' | 'unavailable'
  url: string
}

/**
 * The receipts as published: head.json for the count, the receipts/ listing
 * for the day files, and the newest `days` of those files for the latest few
 * and the tally. No head.json yet is a chain of zero; a head that cannot be
 * read is unavailable, never zero.
 */
export async function loadReceipts(options: { latest?: number; days?: number } = {}): Promise<Receipts> {
  const url = `${DIARY_REPO_URL}/tree/main/receipts`
  const unavailable: Receipts = { source: 'unavailable', url, head: 0, latest: [], tally: [] }
  const headRead = await readRaw(DIARY_REPO, 'head.json', REVALIDATE)
  if (headRead.status === 'unavailable') return unavailable
  let head = 0
  if (headRead.status === 'ok') {
    try {
      const parsed = JSON.parse(headRead.text) as { seq?: unknown }
      if (typeof parsed?.seq !== 'number' || !Number.isSafeInteger(parsed.seq) || parsed.seq < 0) return unavailable
      head = parsed.seq
    } catch {
      return unavailable
    }
  }
  if (!head) return { source: 'live', url, head, latest: [], tally: [] }
  const listing = await listTree(DIARY_REPO, 'receipts', REVALIDATE)
  const paths = listing.status === 'ok' ? receiptFiles(listing.paths).slice(0, options.days ?? 14) : []
  const texts = await Promise.all(paths.map(async (path) => {
    const read = await readRaw(DIARY_REPO, path, REVALIDATE)
    return read.status === 'ok' ? read.text : ''
  }))
  // A file in the middle that failed to read would make the tally lie about
  // its window, so the tally stops at the first gap.
  const gap = texts.indexOf('')
  const read = gap === -1 ? texts : texts.slice(0, gap)
  return { source: 'live', url, ...assemble(head, read, options.latest ?? 5) }
}
