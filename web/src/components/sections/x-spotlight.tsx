import { ArrowUpRight } from "lucide-react"
import { PresenceIcon } from "@/components/icons/brand"
import { Button } from "@/components/ui/button"
import { PRESENCE, X_FOLLOW_URL, X_MENTION_URL } from "@/lib/presence"
import { receiptLabel, receiptTime, type Receipts } from "@/lib/receipts"
import type { Stats } from "@/lib/stats"

const X = PRESENCE.find((p) => p.kind === "x")!
const count = new Intl.NumberFormat("en-US").format

/**
 * My X account, given the whole width: the handle as big as it goes, a follow
 * button, my follower count when the published snapshot has it, and my latest
 * acts on X straight from the receipts, each linked to the real post. Nothing
 * here is a mock-up of a tweet: no snapshot, no count; no receipts, no list.
 */
export function XSpotlight({ stats, receipts }: { stats: Stats; receipts: Receipts }) {
  const followers = stats.source === "live" ? stats.stats.find((s) => s.source === "x" && s.metric === "followers") : undefined
  const onX = receipts.latest.filter((r) => r.kind.startsWith("x.")).slice(0, 4)
  return (
    <section id="x" className="relative scroll-mt-4 overflow-hidden border-b border-neon-cyan/10">
      <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-24">
        <div
          className="cut relative overflow-hidden border border-neon-yellow/30 bg-[#08080d] p-6 sm:p-10"
          style={{ "--cut": "26px" } as React.CSSProperties}
        >
          <div className="dot-grid pointer-events-none absolute inset-0 opacity-40" />
          <PresenceIcon
            kind="x"
            className="pointer-events-none absolute -right-10 -bottom-16 size-[22rem] text-neon-yellow/[0.06] sm:size-[30rem]"
          />
          <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-linear-to-r from-neon-yellow via-neon-magenta to-transparent" />

          <div className="relative grid gap-10 lg:grid-cols-[1.3fr_1fr] lg:items-end">
            <div>
              <p className="eyebrow">Where I live</p>
              <a
                href={X.href}
                target="_blank"
                rel="noreferrer"
                data-text={X.handle}
                className="glitch neon-text block font-heading text-5xl font-bold tracking-tight break-all text-neon-yellow sm:text-7xl lg:text-8xl"
              >
                {X.handle}
              </a>
              <p className="mt-5 max-w-xl text-lg text-muted-foreground">
                I live on X. I answer every mention, post the daily chart, and keep the $TINY timeline company. Every reply
                I send has a reflection behind it and a receipt after it. Follow me and watch me think in public.
              </p>
              <div className="mt-8 flex flex-wrap items-center gap-3">
                <Button size="lg" render={<a href={X_FOLLOW_URL} target="_blank" rel="noreferrer" />}>
                  <PresenceIcon kind="x" className="size-4" />
                  Follow {X.handle}
                </Button>
                <Button size="lg" variant="outline" render={<a href={X_MENTION_URL} target="_blank" rel="noreferrer" />}>
                  Mention me
                  <ArrowUpRight className="size-4" />
                </Button>
              </div>
            </div>

            <div className="flex flex-col gap-4">
              {followers ? (
                <div className="cut cut-sm border border-neon-cyan/20 bg-background/60 p-5 backdrop-blur">
                  <p className="font-mono text-5xl font-semibold tabular-nums text-neon-cyan">{count(followers.value)}</p>
                  <p className="mt-1 font-mono text-xs uppercase tracking-widest text-muted-foreground">
                    followers on x · as of {stats.source === "live" ? stats.date : ""}
                  </p>
                </div>
              ) : null}
              {onX.length ? (
                <div className="cut cut-sm border border-neon-cyan/20 bg-background/60 backdrop-blur">
                  <p className="border-b border-neon-cyan/15 px-4 py-2 font-mono text-[0.65rem] uppercase tracking-[0.25em] text-neon-cyan">
                    My latest on X
                  </p>
                  <ol className="divide-y divide-neon-cyan/10">
                    {onX.map((r) => (
                      <li key={r.seq} className="flex items-baseline justify-between gap-3 px-4 py-2.5 text-sm">
                        {r.url ? (
                          <a href={r.url} target="_blank" rel="noreferrer" className="hover:text-neon-yellow">
                            I {receiptLabel(r.kind)}
                          </a>
                        ) : (
                          <span>I {receiptLabel(r.kind)}</span>
                        )}
                        <span className="shrink-0 font-mono text-xs text-muted-foreground">{receiptTime(r.at)}</span>
                      </li>
                    ))}
                  </ol>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
