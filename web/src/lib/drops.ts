/**
 * Tiny Drops: Teeny's public $TINY prize rounds, as the giveaway hand
 * (hands/giveaway) publishes them to the diary repository (`DIARY_REPO`,
 * teenydroid/diary) under `giveaways/`.
 *
 * THIS FILE IS THE CONTRACT between that hand and the /rewards page; the same
 * contract is written out in docs/drops-schema.md at the repository root. Change
 * both together. The repository is public input, so every file is checked
 * again here, field by field: a round that does not parse is not shown, and
 * nothing is ever filled in with a default number.
 *
 *   giveaways/index.json
 *     { "rounds": [ { "id": 3, "tag": "drop #3", "status": "open", "deadline": "2026-11-20T18:00:00Z" } ] }
 *
 *   giveaways/drop-<id>.json   one round, the `Round` type below
 *
 * The draw, stated once here and on the page, and implemented the same way by
 * the hand:
 *   seed.sha256 = hex(sha256(utf8("<id>:<blockhash>")))   blockhash of `seed.slot`,
 *                 the first finalized slot after the deadline (base58, as RPC returns it)
 *   draw_order  = every entrant with eligible = true, sorted ascending by
 *                 hex(sha256(bytes(seed.sha256) || utf8(lowercase handle)))
 *                 (bytes(...) is the 32 bytes the hex encodes; ties by handle)
 *   the `random` winners are the first `winners` handles of draw_order.
 */

import { sha256 } from '@noble/hashes/sha2'
import { bytesToHex, hexToBytes, utf8ToBytes } from '@noble/hashes/utils'
import { readRaw } from './github'
import { DIARY_REPO, DIARY_REPO_URL } from './receipts'

const REVALIDATE = 600
/** The newest rounds the page reads; older ones stay in the repository. */
const MAX_ROUNDS = 12

export const DROPS_URL = `${DIARY_REPO_URL}/tree/main/giveaways`

export type RoundStatus = 'open' | 'drawn' | 'settled' | 'clawed_back'
export type RoundFormat = 'drop' | 'milestone' | 'challenge'
export type Cluster = 'devnet' | 'mainnet'

/** One line of giveaways/index.json. */
export interface RoundSummary {
  id: number
  /** "drop #<id>": the tag every post about the round carries. */
  tag: string
  status: RoundStatus
  /** ISO 8601, UTC. */
  deadline: string
}

/** One person who replied to the round post, and whether they could win. */
export interface Entrant {
  /** X handle, lowercase, no "@". */
  handle: string
  eligible: boolean
  /** Why: "ok", "account under 90 days", "no address", "does not follow", … */
  reason: string
}

/**
 * One leaf of the round's Merkle tree. The program's leaf is
 * (claimant, amount_unlocked, amount_locked); Tiny Drops locks nothing, so
 * amount_unlocked = amount_atomic and amount_locked = 0. One claim per
 * address: a person who wins twice in a round gets one leaf with the sum.
 */
export interface Claim {
  /** The leaf's position in the tree the hand built. */
  index: number
  handle: string
  kind: 'random' | 'best_reply'
  /** The winner's wallet (base58); it signs the claim. */
  address: string
  /** u64 in the mint's smallest unit, as a decimal string. */
  amount_atomic: string
  /** Sibling hashes from leaf to root, 64 hex characters each. */
  proof: string[]
}

export interface Seed {
  slot: number
  /** base58, as RPC returns it. */
  blockhash: string
  /** hex(sha256(utf8("<id>:<blockhash>"))) */
  sha256: string
}

/** giveaways/drop-<id>.json */
export interface Round {
  id: number
  tag: string
  status: RoundStatus
  format: RoundFormat
  /** The whole pool in whole $TINY, as a decimal string ("250000" or "1234.5"). */
  pool_tiny: string
  /** How many random winners. */
  winners: number
  /** The judged best-reply prize in whole $TINY, decimal string; "0" for none. */
  best_reply_tiny: string
  /** The hook: what the round asks people to reply with. */
  prompt: string
  deadline: string
  /** The round post on X; entries are direct replies to it. */
  post_id: string
  post_url: string
  cluster: Cluster
  mint: string
  decimals: number
  /** The merkle-distributor program the round is paid through. */
  program_id: string
  /** The distributor PDA, once settled. */
  distributor: string | null
  /** The distributor's token vault (its associated token account), once settled. */
  vault: string | null
  /** 64 hex characters, once settled. */
  merkle_root: string | null
  /** When unclaimed tokens go back to the giveaway wallet. */
  clawback_at: string | null
  seed: Seed | null
  entrants: Entrant[]
  draw_order: string[]
  claims: Claim[]
  txs: { create?: string; fund?: string; clawback?: string }
}

export type Drops =
  | { source: 'live'; url: string; rounds: Round[]; skipped: number }
  /** No giveaways/index.json: there has never been a round. */
  | { source: 'missing'; url: string }
  /** GitHub did not answer, or the index is not well-formed. */
  | { source: 'unavailable'; url: string }

// --- validation --------------------------------------------------------------

/** What went wrong, for tests and logs; the page only shows "could not be read". */
export class DropsError extends Error {}

const BASE58_KEY = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/
const BASE58_SIG = /^[1-9A-HJ-NP-Za-km-z]{64,90}$/
const HEX32 = /^[0-9a-f]{64}$/
const X_ID = /^\d{1,25}$/
const HANDLE = /^[a-z0-9_]{1,15}$/
const DECIMAL = /^(0|[1-9]\d{0,24})(\.\d{1,18})?$/
const ATOMIC = /^(0|[1-9]\d{0,19})$/
const U64_MAX = (1n << 64n) - 1n
const STATUSES: readonly RoundStatus[] = ['open', 'drawn', 'settled', 'clawed_back']
const FORMATS: readonly RoundFormat[] = ['drop', 'milestone', 'challenge']
const CLUSTERS: readonly Cluster[] = ['devnet', 'mainnet']

type Obj = Record<string, unknown>

function obj(value: unknown, where: string): Obj {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new DropsError(`${where}: not an object`)
  return value as Obj
}

function str(o: Obj, key: string, pattern?: RegExp, max = 2000): string {
  const value = o[key]
  if (typeof value !== 'string' || value.length > max || (pattern && !pattern.test(value))) {
    throw new DropsError(`${key}: not a valid string`)
  }
  return value
}

function nullable<T>(o: Obj, key: string, read: () => T): T | null {
  return o[key] === null || o[key] === undefined ? null : read()
}

function int(o: Obj, key: string, min = 0, max = Number.MAX_SAFE_INTEGER): number {
  const value = o[key]
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < min || value > max) {
    throw new DropsError(`${key}: not an integer in range`)
  }
  return value
}

function oneOf<T extends string>(o: Obj, key: string, allowed: readonly T[]): T {
  const value = o[key]
  if (typeof value !== 'string' || !allowed.includes(value as T)) throw new DropsError(`${key}: not one of ${allowed.join(', ')}`)
  return value as T
}

function iso(o: Obj, key: string): string {
  const value = str(o, key, undefined, 40)
  if (!/^\d{4}-\d{2}-\d{2}T/.test(value) || Number.isNaN(Date.parse(value))) throw new DropsError(`${key}: not an ISO time`)
  return value
}

function array(o: Obj, key: string, max: number): unknown[] {
  const value = o[key]
  if (!Array.isArray(value) || value.length > max) throw new DropsError(`${key}: not a list`)
  return value
}

/** "drop #<id>", exactly. */
export function roundTag(id: number): string {
  return `drop #${id}`
}

function summary(value: unknown): RoundSummary {
  const o = obj(value, 'round')
  const id = int(o, 'id', 1)
  const tag = str(o, 'tag')
  if (tag !== roundTag(id)) throw new DropsError(`tag: expected "${roundTag(id)}"`)
  return { id, tag, status: oneOf(o, 'status', STATUSES), deadline: iso(o, 'deadline') }
}

/** giveaways/index.json, newest round first; throws DropsError when it is not well-formed. */
export function parseIndex(text: string): RoundSummary[] {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    throw new DropsError('index: not JSON')
  }
  const rounds = array(obj(parsed, 'index'), 'rounds', 10_000).map(summary)
  if (new Set(rounds.map((r) => r.id)).size !== rounds.length) throw new DropsError('index: duplicate round id')
  return rounds.sort((a, b) => b.id - a.id)
}

/** Whole $TINY ("1234.5") in the mint's smallest unit, or undefined when it has more decimals than the mint. */
export function toAtomic(tiny: string, decimals: number): bigint | undefined {
  if (!DECIMAL.test(tiny)) return undefined
  const [whole, frac = ''] = tiny.split('.')
  if (frac.length > decimals) return undefined
  return BigInt(whole) * 10n ** BigInt(decimals) + BigInt(frac.padEnd(decimals, '0') || '0')
}

/** The mint's smallest unit as whole $TINY, without float rounding: 1234500000, 6 → "1,234.5". */
export function formatTiny(atomic: bigint, decimals: number): string {
  const base = 10n ** BigInt(decimals)
  const whole = (atomic / base).toLocaleString('en-US')
  const frac = (atomic % base).toString().padStart(decimals, '0').replace(/0+$/, '')
  return frac ? `${whole}.${frac}` : whole
}

function claim(value: unknown): Claim {
  const o = obj(value, 'claim')
  const amount = str(o, 'amount_atomic', ATOMIC)
  if (BigInt(amount) === 0n || BigInt(amount) > U64_MAX) throw new DropsError('amount_atomic: not a positive u64')
  const proof = array(o, 'proof', 64).map((p) => {
    if (typeof p !== 'string' || !HEX32.test(p)) throw new DropsError('proof: not 32-byte hex')
    return p
  })
  return {
    index: int(o, 'index'),
    handle: str(o, 'handle', HANDLE),
    kind: oneOf(o, 'kind', ['random', 'best_reply'] as const),
    address: str(o, 'address', BASE58_KEY),
    amount_atomic: amount,
    proof,
  }
}

/** giveaways/drop-<id>.json; throws DropsError naming the first field that is wrong. */
export function parseRound(text: string): Round {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    throw new DropsError('round: not JSON')
  }
  const o = obj(parsed, 'round')
  const head = summary(o)
  const decimals = int(o, 'decimals', 0, 18)
  const pool = str(o, 'pool_tiny', DECIMAL)
  const best = str(o, 'best_reply_tiny', DECIMAL)
  const poolAtomic = toAtomic(pool, decimals)
  const bestAtomic = toAtomic(best, decimals)
  if (poolAtomic === undefined || bestAtomic === undefined) throw new DropsError('pool_tiny: more decimals than the mint')
  if (bestAtomic > poolAtomic) throw new DropsError('best_reply_tiny: more than the pool')

  const seed = nullable(o, 'seed', () => {
    const s = obj(o.seed, 'seed')
    return { slot: int(s, 'slot'), blockhash: str(s, 'blockhash', BASE58_KEY), sha256: str(s, 'sha256', HEX32) }
  })
  const entrants = array(o, 'entrants', 100_000).map((value) => {
    const e = obj(value, 'entrant')
    if (typeof e.eligible !== 'boolean') throw new DropsError('eligible: not a boolean')
    return { handle: str(e, 'handle', HANDLE), eligible: e.eligible, reason: str(e, 'reason', undefined, 200) }
  })
  if (new Set(entrants.map((e) => e.handle)).size !== entrants.length) throw new DropsError('entrants: one entry per account')
  const drawOrder = array(o, 'draw_order', 100_000).map((h) => {
    if (typeof h !== 'string' || !HANDLE.test(h)) throw new DropsError('draw_order: not a handle')
    return h
  })
  const claims = array(o, 'claims', 10_000).map(claim)
  if (new Set(claims.map((c) => c.address)).size !== claims.length) throw new DropsError('claims: one leaf per address')
  if (new Set(claims.map((c) => c.index)).size !== claims.length) throw new DropsError('claims: duplicate index')
  const total = claims.reduce((sum, c) => sum + BigInt(c.amount_atomic), 0n)
  if (total > poolAtomic) throw new DropsError('claims: more than the pool')

  const t = obj(o.txs ?? {}, 'txs')
  const txs: Round['txs'] = {}
  for (const key of ['create', 'fund', 'clawback'] as const) {
    if (t[key] !== undefined && t[key] !== null) txs[key] = str(t, key, BASE58_SIG)
  }

  const round: Round = {
    ...head,
    format: oneOf(o, 'format', FORMATS),
    pool_tiny: pool,
    winners: int(o, 'winners', 0, 10_000),
    best_reply_tiny: best,
    prompt: str(o, 'prompt', undefined, 1000),
    post_id: str(o, 'post_id', X_ID),
    post_url: str(o, 'post_url', /^https:\/\/(x|twitter)\.com\/[A-Za-z0-9_]{1,15}\/status\/\d{1,25}$/),
    cluster: oneOf(o, 'cluster', CLUSTERS),
    mint: str(o, 'mint', BASE58_KEY),
    decimals,
    program_id: str(o, 'program_id', BASE58_KEY),
    distributor: nullable(o, 'distributor', () => str(o, 'distributor', BASE58_KEY)),
    vault: nullable(o, 'vault', () => str(o, 'vault', BASE58_KEY)),
    merkle_root: nullable(o, 'merkle_root', () => str(o, 'merkle_root', HEX32)),
    clawback_at: nullable(o, 'clawback_at', () => iso(o, 'clawback_at')),
    seed,
    entrants,
    draw_order: drawOrder,
    claims,
    txs,
  }
  if (!round.post_url.endsWith(`/status/${round.post_id}`)) throw new DropsError('post_url: not the round post')
  if (round.status !== 'open' && !round.seed) throw new DropsError('seed: required once drawn')
  if ((round.status === 'settled' || round.status === 'clawed_back') && (!round.distributor || !round.vault || !round.merkle_root || !round.clawback_at)) {
    throw new DropsError('distributor, vault, merkle_root and clawback_at: required once settled')
  }
  return round
}

// --- the draw ----------------------------------------------------------------

/** hex(sha256(utf8("<id>:<blockhash>"))): the seed the round's draw uses. */
export function seedHash(id: number, blockhash: string): string {
  return bytesToHex(sha256(utf8ToBytes(`${id}:${blockhash}`)))
}

/** One handle's place in the draw: hex(sha256(seed bytes || utf8(lowercase handle))). */
export function drawKey(seedHex: string, handle: string): string {
  const seed = hexToBytes(seedHex)
  const name = utf8ToBytes(handle.toLowerCase())
  const joined = new Uint8Array(seed.length + name.length)
  joined.set(seed)
  joined.set(name, seed.length)
  return bytesToHex(sha256(joined))
}

/** The eligible handles in draw order: ascending by drawKey, ties by handle. */
export function drawOrder(seedHex: string, handles: string[]): string[] {
  return handles
    .map((handle) => ({ handle: handle.toLowerCase(), key: drawKey(seedHex, handle) }))
    .sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : a.handle < b.handle ? -1 : a.handle > b.handle ? 1 : 0))
    .map((entry) => entry.handle)
}

/** What anyone can check about a round's draw from the published file alone. */
export interface DrawCheck {
  /** seed.sha256 is sha256("<id>:<blockhash>"). */
  seed: boolean
  /** draw_order is the eligible entrants sorted by the formula. */
  order: boolean
  /** The random winners are the first `winners` of draw_order. */
  winners: boolean
}

export function checkDraw(round: Round): DrawCheck | undefined {
  if (!round.seed) return undefined
  const eligible = round.entrants.filter((e) => e.eligible).map((e) => e.handle)
  const expected = drawOrder(round.seed.sha256, eligible)
  const random = round.claims.filter((c) => c.kind === 'random').map((c) => c.handle).sort()
  const first = round.draw_order.slice(0, round.winners)
  return {
    seed: seedHash(round.id, round.seed.blockhash) === round.seed.sha256,
    order: expected.length === round.draw_order.length && expected.every((h, i) => h === round.draw_order[i]),
    // A best-reply winner can also be drawn; that person's one leaf then says best_reply.
    winners: random.every((h) => first.includes(h)) && random.length <= round.winners,
  }
}

// --- reading -----------------------------------------------------------------

/** The connected wallet's leaf in a round, if it won. */
export function findClaim(round: Round, address: string): Claim | undefined {
  return round.claims.find((c) => c.address === address)
}

/** The round people can enter now: the newest open one. */
export function liveRound(rounds: Round[]): Round | undefined {
  return rounds.find((r) => r.status === 'open')
}

/** Solscan for a transaction or an account on the round's cluster. */
export function solscan(kind: 'tx' | 'account' | 'token', id: string, cluster: Cluster): string {
  return `https://solscan.io/${kind}/${id}${cluster === 'devnet' ? '?cluster=devnet' : ''}`
}

/**
 * The index and the newest rounds, read server-side and cached. A round file
 * that is missing or does not parse is skipped and counted, never guessed at.
 */
export async function loadDrops(): Promise<Drops> {
  const url = DROPS_URL
  const indexRead = await readRaw(DIARY_REPO, 'giveaways/index.json', REVALIDATE)
  if (indexRead.status === 'missing') return { source: 'missing', url }
  if (indexRead.status === 'unavailable') return { source: 'unavailable', url }
  let index: RoundSummary[]
  try {
    index = parseIndex(indexRead.text)
  } catch {
    return { source: 'unavailable', url }
  }
  const wanted = index.slice(0, MAX_ROUNDS)
  const rounds = await Promise.all(
    wanted.map(async (s) => {
      const read = await readRaw(DIARY_REPO, `giveaways/drop-${s.id}.json`, REVALIDATE)
      if (read.status !== 'ok') return undefined
      try {
        const round = parseRound(read.text)
        return round.id === s.id ? round : undefined
      } catch {
        return undefined
      }
    }),
  )
  const ok = rounds.filter((r): r is Round => Boolean(r))
  return { source: 'live', url, rounds: ok, skipped: wanted.length - ok.length }
}
