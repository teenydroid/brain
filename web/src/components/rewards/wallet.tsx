"use client"

import { WalletReadyState, type WalletName } from "@solana/wallet-adapter-base"
import { ConnectionProvider, useWallet, WalletProvider } from "@solana/wallet-adapter-react"
import { useEffect, useMemo, useState, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { rpcFor } from "@/lib/solana-rpc"

/**
 * The wallet, in the browser only: whatever Solana wallets the browser has
 * announced through the Wallet Standard (Phantom, Solflare, Backpack, …). No
 * adapter list of our own, no server: the key never leaves the wallet.
 */
export function WalletShell({ children }: { children: ReactNode }) {
  const wallets = useMemo(() => [], [])
  return (
    <ConnectionProvider endpoint={rpcFor("mainnet")}>
      <WalletProvider wallets={wallets} autoConnect={false}>
        {children}
      </WalletProvider>
    </ConnectionProvider>
  )
}

export function shortKey(key: string): string {
  return `${key.slice(0, 4)}…${key.slice(-4)}`
}

/** Pick a wallet and connect it; once connected, the address and a way out. */
export function ConnectWallet() {
  const { wallets, wallet, select, connect, disconnect, connected, connecting, publicKey } = useWallet()
  const [wanted, setWanted] = useState<WalletName | null>(null)
  const [error, setError] = useState<string>()
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])

  // select() lands on the next render; connect once the chosen wallet is the selected one.
  useEffect(() => {
    if (!wanted || wallet?.adapter.name !== wanted || connected || connecting) return
    setWanted(null)
    connect().catch((e: unknown) => setError(e instanceof Error && e.message ? e.message : "the wallet said no"))
  }, [wanted, wallet, connected, connecting, connect])

  if (connected && publicKey) {
    return (
      <div className="flex flex-wrap items-center gap-3 font-mono text-xs">
        <span className="text-neon-cyan">connected: {shortKey(publicKey.toBase58())}</span>
        <Button size="sm" variant="ghost" onClick={() => disconnect()}>
          disconnect
        </Button>
      </div>
    )
  }

  const found = wallets.filter((w) => w.readyState === WalletReadyState.Installed || w.readyState === WalletReadyState.Loadable)
  if (!mounted) return <p className="font-mono text-xs text-muted-foreground">looking for a solana wallet in this browser…</p>
  if (!found.length) {
    return (
      <p className="max-w-xl text-sm text-muted-foreground">
        no solana wallet in this browser. install one (phantom, solflare or backpack all work), then come back to this
        page.
      </p>
    )
  }
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        {found.map((w) => (
          <Button
            key={w.adapter.name}
            variant="outline"
            disabled={connecting}
            onClick={() => {
              setError(undefined)
              setWanted(w.adapter.name)
              select(w.adapter.name)
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element -- the wallet's own data: URI icon */}
            {w.adapter.icon ? <img src={w.adapter.icon} alt="" className="size-4" /> : null}
            connect {w.adapter.name}
          </Button>
        ))}
      </div>
      {error ? <p className="font-mono text-xs text-destructive">{error}</p> : null}
    </div>
  )
}
