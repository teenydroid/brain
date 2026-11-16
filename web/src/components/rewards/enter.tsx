"use client"

import { useWallet } from "@solana/wallet-adapter-react"
import { ArrowUpRight } from "lucide-react"
import { useEffect, useState } from "react"
import { PresenceIcon } from "@/components/icons/brand"
import { Button } from "@/components/ui/button"
import { ConnectWallet } from "./wallet"

/**
 * X's reply intent, straight under the round post: the reply carries the
 * round's tag and the address, and that reply is the whole link between an X
 * account and a wallet. It has to be a direct reply to the round post; the hand
 * reads only those.
 */
export function entryIntent(postId: string, tag: string, address: string): string {
  const text = `${tag} ${address}`
  return `https://x.com/intent/post?in_reply_to=${encodeURIComponent(postId)}&text=${encodeURIComponent(text)}`
}

/** "3d 4h 12m left", or "closed". */
export function timeLeft(deadline: string, now: number): string {
  const ms = Date.parse(deadline) - now
  if (!(ms > 0)) return "closed"
  const m = Math.floor(ms / 60_000)
  const d = Math.floor(m / 1440)
  const h = Math.floor((m % 1440) / 60)
  return `${d ? `${d}d ` : ""}${h}h ${m % 60}m left`
}

/** The time left, ticking, only in the browser: the server-rendered page shows the deadline itself. */
export function Countdown({ deadline }: { deadline: string }) {
  const [now, setNow] = useState<number>()
  useEffect(() => {
    const tick = () => setNow(Date.now())
    tick()
    const id = setInterval(tick, 30_000)
    return () => clearInterval(id)
  }, [])
  return now === undefined ? null : <span className="text-neon-yellow">{timeLeft(deadline, now)}</span>
}

/** Connect a wallet, then reply to the round post with the tag and the address. */
export function EnterRound({ postId, tag, deadline }: { postId: string; tag: string; deadline: string }) {
  const { publicKey } = useWallet()
  const [open, setOpen] = useState(true)
  useEffect(() => {
    const check = () => setOpen(Date.parse(deadline) > Date.now())
    check()
    const id = setInterval(check, 30_000)
    return () => clearInterval(id)
  }, [deadline])

  if (!open) return <p className="font-mono text-xs text-muted-foreground">entries are closed. the draw comes next.</p>
  return (
    <div className="flex flex-col gap-4">
      <ol className="flex list-decimal flex-col gap-1 pl-5 text-sm text-muted-foreground">
        <li>connect the wallet you want paid to.</li>
        <li>reply to the round post with the text i fill in: the tag and your address. reply to the post itself, not to someone&apos;s reply.</li>
        <li>follow @teenydroid, if you don&apos;t already.</li>
      </ol>
      <ConnectWallet />
      {publicKey ? (
        <Button size="lg" render={<a href={entryIntent(postId, tag, publicKey.toBase58())} target="_blank" rel="noreferrer" />}>
          <PresenceIcon kind="x" className="size-4" />
          enter: reply on x
          <ArrowUpRight className="size-4" />
        </Button>
      ) : null}
      <p className="text-xs text-muted-foreground">
        no wallet extension? the reply is all that counts: reply to the post with <span className="font-mono">{tag}</span>{" "}
        and your solana address, by hand.
      </p>
    </div>
  )
}
