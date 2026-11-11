/**
 * Teeny's wallet, as anyone can see it: the public Solana address bin/receipts
 * publishes as wallet.json in `DIARY_REPO`
 *
 *   {"address": "<base58 public key>", "cluster": "mainnet"}
 *
 * and its SOL and USDC balances, read server-side from a public Solana RPC
 * (`SOLANA_RPC_URL`, else the cluster's public endpoint) and cached. A
 * balance that cannot be read is left out, never shown as zero; no token
 * account for USDC is a real zero, read from the chain.
 */

import { readRaw } from './github'
import { DIARY_REPO, DIARY_REPO_URL } from './receipts'

const REVALIDATE = 600
const BALANCE_REVALIDATE = 300

export type Cluster = 'mainnet' | 'devnet'

export interface PublicWallet {
  address: string
  cluster: Cluster
}

const RPC_DEFAULTS: Record<Cluster, string> = {
  mainnet: 'https://api.mainnet-beta.solana.com',
  devnet: 'https://api.devnet.solana.com',
}

/** The USDC mint per cluster, as the wallet hand pins it (hands/lib/teeny_hands/wallet/caps.py). */
const USDC_MINTS: Record<Cluster, string> = {
  mainnet: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
  devnet: '4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU',
}

const ADDRESS = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/

export type Wallet =
  | ({ source: 'live'; url: string; sol?: string; usdc?: string } & PublicWallet)
  | { source: 'unpublished' }
  | { source: 'unavailable' }

/** wallet.json as the site may use it; undefined unless it is one base58 address and a known cluster. */
export function parseWallet(text: string): PublicWallet | undefined {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return undefined
  }
  if (!parsed || typeof parsed !== 'object') return undefined
  const { address, cluster } = parsed as Record<string, unknown>
  if (typeof address !== 'string' || !ADDRESS.test(address)) return undefined
  if (cluster !== 'mainnet' && cluster !== 'devnet') return undefined
  return { address, cluster }
}

export function solscanUrl(wallet: PublicWallet): string {
  return `https://solscan.io/account/${wallet.address}${wallet.cluster === 'devnet' ? '?cluster=devnet' : ''}`
}

export function rpcUrl(cluster: Cluster): string {
  return process.env.SOLANA_RPC_URL || RPC_DEFAULTS[cluster]
}

/** getBalance's result as lamports, or undefined. */
export function parseLamports(result: unknown): bigint | undefined {
  const value = (result as { value?: unknown } | undefined)?.value
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? BigInt(value) : undefined
}

/** getTokenAccountsByOwner's (jsonParsed) result as the summed raw amount, or undefined if any account is malformed. */
export function parseTokenAmount(result: unknown): bigint | undefined {
  const value = (result as { value?: unknown } | undefined)?.value
  if (!Array.isArray(value)) return undefined
  let total = 0n
  for (const entry of value) {
    const amount = entry?.account?.data?.parsed?.info?.tokenAmount?.amount
    if (typeof amount !== 'string' || !/^\d{1,30}$/.test(amount)) return undefined
    total += BigInt(amount)
  }
  return total
}

const grouped = new Intl.NumberFormat('en-US')

/** A raw amount in whole units, at most four decimals, trailing zeros dropped: 1500000000n, 9 -> "1.5". */
export function formatUnits(raw: bigint, decimals: number, places = 4): string {
  const scale = 10n ** BigInt(decimals)
  const step = 10n ** BigInt(Math.max(decimals - places, 0))
  const rounded = ((raw + step / 2n) / step) * step
  const whole = rounded / scale
  const fraction = (rounded % scale).toString().padStart(decimals, '0').slice(0, places).replace(/0+$/, '')
  return grouped.format(whole) + (fraction ? `.${fraction}` : '')
}

async function rpc(cluster: Cluster, method: string, params: unknown[]): Promise<unknown> {
  try {
    const response = await fetch(rpcUrl(cluster), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
      cache: 'force-cache',
      next: { revalidate: BALANCE_REVALIDATE },
      signal: AbortSignal.timeout(6_000),
    })
    if (!response.ok) return undefined
    const body = (await response.json()) as { result?: unknown; error?: unknown }
    return body.error ? undefined : body.result
  } catch {
    return undefined
  }
}

/** The published wallet and what it holds: `unpublished` before wallet.json exists, `unavailable` when it cannot be read or trusted. */
export async function loadWallet(): Promise<Wallet> {
  const read = await readRaw(DIARY_REPO, 'wallet.json', REVALIDATE)
  if (read.status === 'missing') return { source: 'unpublished' }
  const wallet = read.status === 'ok' ? parseWallet(read.text) : undefined
  if (!wallet) return { source: 'unavailable' }
  const [lamports, usdc] = await Promise.all([
    rpc(wallet.cluster, 'getBalance', [wallet.address, { commitment: 'confirmed' }]).then(parseLamports),
    rpc(wallet.cluster, 'getTokenAccountsByOwner', [
      wallet.address,
      { mint: USDC_MINTS[wallet.cluster] },
      { encoding: 'jsonParsed', commitment: 'confirmed' },
    ]).then(parseTokenAmount),
  ])
  return {
    source: 'live',
    ...wallet,
    url: solscanUrl(wallet),
    sol: lamports === undefined ? undefined : formatUnits(lamports, 9),
    usdc: usdc === undefined ? undefined : formatUnits(usdc, 6),
  }
}

/** Where the address is published, for a "source" link. */
export const WALLET_SOURCE_URL = `${DIARY_REPO_URL}/blob/main/wallet.json`
