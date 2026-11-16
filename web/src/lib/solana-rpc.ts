/**
 * Which Solana RPC the browser talks to for a round's cluster. Both are build
 * args (Next inlines NEXT_PUBLIC_* at `next build`), so each is read by its
 * literal name. Unset, the public cluster endpoints are used; they are rate
 * limited, so a deployment should set its own.
 */

import type { Cluster } from './drops'

export function rpcFor(cluster: Cluster): string {
  if (cluster === 'devnet') return process.env.NEXT_PUBLIC_SOLANA_RPC_DEVNET || 'https://api.devnet.solana.com'
  return process.env.NEXT_PUBLIC_SOLANA_RPC || 'https://api.mainnet-beta.solana.com'
}
