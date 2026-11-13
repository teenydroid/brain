import Link from "next/link"
import { ArrowRight } from "lucide-react"
import { PresenceIcon } from "@/components/icons/brand"
import { PRESENCE } from "@/lib/presence"
import { Button } from "@/components/ui/button"
import { SceneCanvas } from "@/components/scene"
import { ReceiptsCounter } from "@/components/receipts-counter"
import { ThoughtStream, type Thought } from "@/components/thought-stream"
import { TokenCounter } from "@/components/token-counter"
import type { MindFeed } from "@/lib/mind"
import { receiptCount, receiptLabel, receiptTime, type Receipts } from "@/lib/receipts"
import { cn } from "@/lib/utils"

const X = PRESENCE.find((p) => p.kind === "x")!
const TELEGRAM = PRESENCE.find((p) => p.kind === "telegram")!
const count = new Intl.NumberFormat("en-US").format
const HEADLINE = "Everything Teeny does is on the record."

function clip(text: string, max = 180): string {
  const flat = text.replace(/\s+/g, " ").trim()
  return flat.length > max ? `${flat.slice(0, max - 1).trimEnd()}…` : flat
}

/** What the corner terminal types: the bot's own reflections, or failing that its newest receipts. Never filler. */
function thoughtsFrom(feed: MindFeed, receipts: Receipts): { title: string; thoughts: Thought[] } {
  const reflections = feed.source === "live" ? feed.reflections : []
  const thought = reflections
    .map((r) => ({ tag: r.trigger, text: clip(r.thinking || r.said), at: receiptTime(r.at) }))
    .filter((t) => t.text)
    .slice(0, 6)
  if (thought.length) return { title: "thinking", thoughts: thought }
  return {
    title: "last acts",
    thoughts: receipts.latest.slice(0, 6).map((r) => ({ tag: r.kind, text: `Teeny ${receiptLabel(r.kind)}.`, at: receiptTime(r.at) })),
  }
}

interface Hotspot {
  href: string
  label: string
  /** Where it sits on the frame, around the blob. */
  place: string
}

/**
 * The front page's opening: Teeny's mind as a blob at the centre of a HUD,
 * numbered hotspots around it leading into the page, the last few thoughts
 * typing in one corner and the claim in the other. Below the frame, what
 * Teeny has done, counted from its published receipts; the token counter
 * joins it only while the bot's ledger API answers with real totals.
 */
export function Hero({ feed, receipts }: { feed: MindFeed; receipts: Receipts }) {
  const { status } = feed
  const live = feed.source === "live"
  const ledgerLive = live && Boolean(feed.totals)
  const { title, thoughts } = thoughtsFrom(feed, receipts)

  const hotspots: Hotspot[] = [
    { href: "#receipts", label: "Receipts", place: "left-[56%] top-[18%] sm:left-[58%]" },
    { href: "#numbers", label: "Numbers", place: "right-[10%] top-[30%] sm:right-[24%] sm:top-[26%]" },
    { href: "#brain", label: "Brain", place: "left-[8%] top-[34%] sm:left-[27%]" },
    ledgerLive
      ? { href: "/ledger", label: "Ledger", place: "left-[18%] top-[62%] sm:left-[33%] sm:top-[64%]" }
      : { href: "#find", label: "Find it", place: "left-[18%] top-[62%] sm:left-[33%] sm:top-[64%]" },
  ]

  const readouts = [
    `${X.handle} · open brain`,
    live ? `${status.mode} on ${status.model}${status.dryRun ? " · dry run" : ""}` : "core · status not reachable",
    receipts.source === "live" && receipts.head ? `${count(receipts.head)} receipts · chained by hash` : "receipts · public on github",
  ]

  const ticker =
    receipts.source === "live" && receipts.tally.length
      ? receipts.tally.map((row) => receiptCount(row.kind, row.count))
      : PRESENCE.map((p) => `${p.place} · ${p.handle}`)

  return (
    <>
      <section className="relative px-2 pt-16 pb-2 sm:px-4">
        <div
          className="cut relative isolate h-[calc(100svh-4.75rem)] min-h-[640px] overflow-hidden border border-neon-cyan/20 bg-[#06060b]"
          style={{ "--cut": "28px" } as React.CSSProperties}
        >
          <SceneCanvas name="core" className="absolute inset-0" />
          <div className="dot-grid pointer-events-none absolute inset-0 opacity-30 mix-blend-screen" />
          {/* Darkens the bottom so the copy stays legible over the blob. */}
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-2/3 bg-linear-to-t from-[#06060b] via-[#06060b]/50 to-transparent" />

          {/* Readouts across the top, like the labels on an instrument. */}
          <div className="pointer-events-none relative grid grid-cols-1 gap-1 px-5 pt-5 font-mono text-[0.65rem] uppercase tracking-[0.25em] text-muted-foreground sm:grid-cols-3 sm:px-8 sm:pt-7">
            {readouts.map((text, i) => (
              <p key={i} className={cn("flex items-center gap-2", i > 0 && "hidden sm:flex", i === 2 && "sm:justify-end")}>
                <span className={cn("size-1.5", i === 0 ? "bg-neon-yellow" : i === 1 ? (live ? "bg-neon-cyan" : "bg-neon-magenta") : "bg-neon-magenta")} />
                {text}
              </p>
            ))}
          </div>

          {/* A vertical rail on the left edge. */}
          <p className="pointer-events-none absolute top-1/2 left-3 hidden -translate-y-1/2 -rotate-180 font-mono text-[0.6rem] uppercase tracking-[0.5em] text-neon-cyan/50 [writing-mode:vertical-rl] md:block">
            teeny // mind // core
          </p>

          {hotspots.map((spot, i) => {
            const inner = (
              <>
                <span className="hotspot-ping absolute inset-0 rounded-full border border-neon-cyan/60" />
                <span className="relative flex size-14 items-center justify-center rounded-full border border-foreground/40 bg-background/30 font-mono text-sm text-foreground backdrop-blur-sm transition-all group-hover:border-neon-yellow group-hover:text-neon-yellow group-hover:shadow-[0_0_24px_var(--neon-yellow)] sm:size-16">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span className="pointer-events-none absolute top-1/2 left-full ml-3 -translate-y-1/2 -translate-x-1 border-l border-neon-yellow bg-background/70 px-2 py-1 font-mono text-[0.65rem] uppercase tracking-[0.25em] whitespace-nowrap text-neon-yellow opacity-0 backdrop-blur transition-all group-hover:translate-x-0 group-hover:opacity-100 group-focus-visible:opacity-100">
                  {spot.label}
                </span>
              </>
            )
            const className = cn("group absolute z-10 rounded-full outline-none focus-visible:ring-2 focus-visible:ring-neon-yellow", spot.place)
            return spot.href.startsWith("/") ? (
              <Link key={spot.href} href={spot.href} className={className} aria-label={spot.label}>
                {inner}
              </Link>
            ) : (
              <a key={spot.href} href={spot.href} className={className} aria-label={spot.label}>
                {inner}
              </a>
            )
          })}

          <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col gap-6 p-5 sm:p-8 lg:flex-row lg:items-end lg:justify-between">
            <div className="pointer-events-auto hidden md:block">
              <ThoughtStream thoughts={thoughts} title={title} />
            </div>

            <div className="pointer-events-auto max-w-xl lg:text-right">
              <h1
                data-text={HEADLINE}
                className="glitch text-4xl leading-[1.02] font-semibold uppercase sm:text-5xl lg:text-6xl"
              >
                {HEADLINE}
              </h1>
              <p className="mt-4 text-sm text-muted-foreground sm:text-base lg:ml-auto lg:max-w-md">
                Teeny is the smol AI that runs the $TINY account, and the brain of agents that decides what it is trying to do.
                Every post, reply, payment and commit leaves a receipt in a public repository, chained by hash.
              </p>
              <div className="mt-6 flex flex-wrap items-center gap-3 lg:justify-end">
                <Button size="lg" render={<a href={receipts.url} target="_blank" rel="noreferrer" />}>
                  Check the receipts
                  <ArrowRight className="size-4" />
                </Button>
                <Button size="lg" variant="outline" render={<Link href="/brain" />}>
                  See the brain at work
                </Button>
                <Button size="lg" variant="ghost" render={<a href={TELEGRAM.href} target="_blank" rel="noreferrer" />}>
                  <PresenceIcon kind="telegram" className="size-4" />
                  Talk to it
                </Button>
              </div>
            </div>
          </div>
        </div>

        {/* A ticker under the frame: what it has done, by kind. */}
        <div className="relative mt-2 overflow-hidden border-y border-neon-yellow/30 bg-neon-yellow/5 py-2 font-mono text-xs uppercase tracking-[0.25em]">
          <div className="marquee flex w-max gap-10 whitespace-nowrap" aria-hidden>
            {[...ticker, ...ticker, ...ticker, ...ticker].map((item, i) => (
              <span key={i} className="flex items-center gap-10 text-neon-yellow/90">
                {item}
                <span className="text-neon-magenta">◆</span>
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* The counters, under the frame. */}
      <section id="receipts" className="relative scroll-mt-16 border-b border-neon-cyan/10">
        <div className="mx-auto grid max-w-7xl gap-6 px-4 py-14 sm:px-6 lg:grid-cols-2">
          <div className="cut brackets relative border border-neon-cyan/15 bg-card p-6">
            <p className="eyebrow">Receipts</p>
            <ReceiptsCounter receipts={receipts} />
          </div>
          {ledgerLive && feed.totals ? (
            <div className="cut brackets relative border border-neon-cyan/15 bg-card p-6">
              <p className="eyebrow">Tokens</p>
              <TokenCounter initial={feed.totals} source={feed.source} />
            </div>
          ) : (
            <div className="cut relative flex flex-col justify-between gap-6 border border-neon-cyan/15 bg-card p-6">
              <div>
                <p className="eyebrow">Find it</p>
                <p className="text-muted-foreground">One mind, in a few places. Every one of them leaves the same receipts.</p>
              </div>
              <ul className="flex flex-wrap gap-2">
                {PRESENCE.map((p) => (
                  <li key={p.kind}>
                    <a
                      href={p.href}
                      target="_blank"
                      rel="noreferrer"
                      title={`${p.place} · ${p.handle}`}
                      className="cut cut-sm flex items-center gap-2 border border-neon-cyan/25 bg-background/60 px-3 py-2 font-mono text-xs text-muted-foreground transition-colors hover:border-neon-yellow hover:text-neon-yellow"
                    >
                      <PresenceIcon kind={p.kind} className="size-3.5" />
                      {p.handle}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </section>
    </>
  )
}
