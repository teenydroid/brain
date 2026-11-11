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

export function receiptFiles(tree: string[]): string[] {
  void tree
  return []
}

export interface Assembled {
  head: number
  latest: Receipt[]
  tally: Array<{ kind: string; count: number }>
  window?: { from: string; count: number; complete: boolean }
}

export function assemble(head: number, files: string[], latest: number): Assembled {
  void files
  void latest
  return { head, latest: [], tally: [] }
}

export { listTree, readRaw, REVALIDATE }
