import Link from "next/link"
import { ArrowRight } from "lucide-react"
import { PresenceIcon } from "@/components/icons/brand"
import { PRESENCE } from "@/lib/presence"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { SceneCanvas } from "@/components/scene"
import { ReceiptsCounter } from "@/components/receipts-counter"
import { TokenCounter } from "@/components/token-counter"
import type { MindFeed } from "@/lib/mind"
import type { Receipts } from "@/lib/receipts"

const X = PRESENCE.find((p) => p.kind === "x")!
const TELEGRAM = PRESENCE.find((p) => p.kind === "telegram")!

/**
 * The front page's opening: what Teeny has done, counted from its published
 * receipts. The token counter joins it only while the bot's ledger API
 * answers with real totals; unreachable, there is no token figure at all.
 */
export function Hero({ feed, receipts }: { feed: MindFeed; receipts: Receipts }) {
  const { status } = feed
  const ledgerLive = feed.source === "live" && Boolean(feed.totals)
  return (
    <section className="relative overflow-hidden border-b">
      <SceneCanvas name="brain" className="absolute inset-y-0 right-0 w-full lg:w-3/5" />
      <div className="pointer-events-none absolute inset-0 bg-linear-to-r from-background via-background/85 to-background/40" />
      <div className="pointer-events-none absolute inset-0 bg-linear-to-t from-background via-transparent to-transparent" />
      <div className="relative mx-auto flex min-h-[92vh] max-w-6xl flex-col justify-center gap-10 px-4 pt-28 pb-16 sm:px-6">
        <div className="max-w-2xl">
          <Badge variant="outline" className="mb-5 font-mono">
            {X.handle} · open brain
            {feed.source === "live" ? ` · ${status.mode} on ${status.model}${status.dryRun ? " · dry run" : ""}` : ""}
          </Badge>
          <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl lg:text-6xl">
            Everything Teeny does is on the record.
          </h1>
          <p className="mt-5 text-lg text-muted-foreground">
            Teeny is the smol AI that runs the $TINY account, and the brain of agents that decides what it is
            trying to do. Every post, reply, payment and commit it makes leaves a receipt in a public repository,
            chained by hash and linked to where anyone can check it.
          </p>
        </div>

        <ReceiptsCounter receipts={receipts} />
        {ledgerLive && feed.totals ? <TokenCounter initial={feed.totals} source={feed.source} /> : null}

        <div className="flex flex-wrap items-center gap-3">
          <Button size="lg" render={<a href={receipts.url} target="_blank" rel="noreferrer" />}>
            Check the receipts
            <ArrowRight className="size-4" />
          </Button>
          <Button size="lg" variant="outline" render={<Link href="/brain" />}>
            See the brain at work
          </Button>
          {ledgerLive ? (
            <Button size="lg" variant="outline" render={<Link href="/ledger" />}>
              Open the ledger
            </Button>
          ) : null}
          <Button size="lg" variant="ghost" render={<a href={TELEGRAM.href} target="_blank" rel="noreferrer" />}>
            <PresenceIcon kind="telegram" className="size-4" />
            Talk to it
          </Button>
          <ul className="ml-auto flex flex-wrap items-center gap-2">
            {PRESENCE.map((p) => (
              <li key={p.kind}>
                <a
                  href={p.href}
                  target="_blank"
                  rel="noreferrer"
                  title={`${p.place} · ${p.handle}`}
                  className="flex items-center gap-2 rounded-full border bg-background/60 px-3 py-1.5 text-xs text-muted-foreground backdrop-blur transition-colors hover:border-primary/50 hover:text-foreground"
                >
                  <PresenceIcon kind={p.kind} className="size-3.5" />
                  <span className="font-mono">{p.handle}</span>
                </a>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  )
}
