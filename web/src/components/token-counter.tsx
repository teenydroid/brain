'use client'

import { useEffect, useRef, useState } from "react"
import {
  LANE_META,
  LANES,
  compactTokens,
  countingSince,
  formatTokens,
  grandTotal,
  lastCall,
  type Lane,
  type Totals,
} from "@/lib/ledger"
import { useReducedMotion } from "@/lib/motion"
import { cn } from "@/lib/utils"

/**
 * The counter: every token Teeny has ever burned, as one figure. It polls the
 * bot's totals through /api/pulse and rolls the figure from where it was to
 * where it is now, so a row landing in the ledger shows up here within half
 * a minute as the number moving. The figure is never extrapolated: between
 * polls it holds still, and it starts from the first call the ledger saw.
 */

const POLL_MS = 20_000
const ROLL_MS = 1_400

function useRolling(target: number): number {
  const reduced = useReducedMotion()
  const [shown, setShown] = useState(target)
  const from = useRef(target)
  const start = useRef<number | null>(null)
  useEffect(() => {
    if (reduced) return
    const origin = from.current
    if (origin === target) return
    start.current = null
    let frame = 0
    const step = (now: number) => {
      if (start.current === null) start.current = now
      const t = Math.min((now - start.current) / ROLL_MS, 1)
      const eased = 1 - Math.pow(1 - t, 3)
      const value = Math.round(origin + (target - origin) * eased)
      setShown(value)
      if (t < 1) frame = requestAnimationFrame(step)
      else from.current = target
    }
    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [target, reduced])
  return reduced ? target : shown
}

function Digits({ value, className }: { value: number; className?: string }) {
  const shown = useRolling(value)
  return (
    <span className={cn("font-mono tabular-nums tracking-tight", className)} aria-live="off">
      {formatTokens(shown)}
    </span>
  )
}

function ago(iso: string | undefined, now: number): string {
  if (!iso) return "no call yet"
  const ms = now - Date.parse(iso)
  if (!Number.isFinite(ms) || ms < 0) return "just now"
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function since(iso: string | undefined): string {
  if (!iso) return ""
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? "" : d.toISOString().slice(0, 16).replace("T", " ") + " UTC"
}

/** Every source with a call, biggest first, so the figure has a shape. */
function bySource(totals: Totals): Array<{ key: string; lane: Lane; source: string; tokens: number; calls: number }> {
  return Object.entries(totals.sources)
    .map(([key, t]) => {
      const [lane, ...rest] = key.split("/")
      return { key, lane: (lane === "brain" ? "brain" : "mascot") as Lane, source: rest.join("/"), tokens: t.tokens.total, calls: t.calls }
    })
    .filter((row) => row.calls > 0)
    .sort((a, b) => b.tokens - a.tokens)
}

export function TokenCounter({ initial, source }: { initial: Totals; source: "live" | "unavailable" }) {
  const [totals, setTotals] = useState(initial)
  const [live, setLive] = useState(source === "live")
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const response = await fetch("/api/pulse", { cache: "no-store" })
        if (!response.ok) return
        const body = (await response.json()) as { source: "live" | "unavailable"; totals: Totals }
        if (cancelled) return
        setLive(body.source === "live")
        if (body.source === "live") setTotals(body.totals)
        setNow(Date.now())
      } catch {
        // The last figure stands. A poll that fails is not a token unburned.
      }
    }
    const poll = setInterval(tick, POLL_MS)
    const clock = setInterval(() => setNow(Date.now()), 10_000)
    return () => {
      cancelled = true
      clearInterval(poll)
      clearInterval(clock)
    }
  }, [])

  const all = grandTotal(totals)
  const first = countingSince(totals)
  const last = lastCall(totals)
  const sources = bySource(totals)
  const thinking = all.tokens.reasoning
  const answered = Math.max(all.tokens.output - thinking, 0)

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-2">
        <p className="flex items-center gap-2 font-mono text-xs uppercase tracking-widest text-muted-foreground">
          <span className="relative flex size-2">
            <span className={cn("absolute inline-flex size-full animate-ping rounded-full opacity-60", live ? "bg-primary" : "bg-destructive")} />
            <span className={cn("relative inline-flex size-2 rounded-full", live ? "bg-primary" : "bg-destructive")} />
          </span>
          {live ? "tokens teeny has burned" : "the bot is not reachable right now · last known figure"}
          {last ? <span className="normal-case tracking-normal">· last call {ago(last, now)}</span> : null}
        </p>
        <Digits value={all.tokens.total} className="text-6xl font-semibold sm:text-7xl lg:text-8xl" />
        <p className="text-sm text-muted-foreground">
          across {formatTokens(all.calls)} model calls
          {first ? `, counting since ${since(first)}` : ", counting from the first call the ledger sees"} · every one is a row in the bot's ledger
        </p>
      </div>

      <dl className="grid max-w-xl grid-cols-2 gap-x-6 gap-y-3 border-t pt-4 font-mono text-xs sm:grid-cols-4">
        <div>
          <dt className="text-muted-foreground">in</dt>
          <dd className="text-lg text-foreground">{compactTokens(all.tokens.input)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">cached</dt>
          <dd className="text-lg text-foreground">{compactTokens(all.tokens.cached)}</dd>
        </div>
        <div>
          <dt className="text-sky-400">thinking</dt>
          <dd className="text-lg text-foreground">{compactTokens(thinking)}</dd>
        </div>
        <div>
          <dt className="text-emerald-400">out</dt>
          <dd className="text-lg text-foreground">{compactTokens(answered)}</dd>
        </div>
      </dl>

      {sources.length ? (
        <ul className="flex flex-wrap gap-2" aria-label="tokens by source">
          {sources.map((row) => (
            <li
              key={row.key}
              title={`${LANE_META[row.lane].name} · ${formatTokens(row.calls)} calls · ${formatTokens(row.tokens)} tokens`}
              className={cn(
                "flex items-center gap-2 rounded-full border bg-background/60 px-3 py-1 font-mono text-xs backdrop-blur",
                row.lane === "brain" ? "border-sky-400/30" : "border-primary/30",
              )}
            >
              <span>{row.source}</span>
              <span className="text-muted-foreground">{compactTokens(row.tokens)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="max-w-xl text-sm text-muted-foreground">
          {live
            ? "The ledger began counting with this deploy and no call has run since. The next reply, chart or morning post is the first row."
            : "Nothing to show until the bot answers."}
        </p>
      )}
      <p className="sr-only">
        {LANES.map((lane) => `${LANE_META[lane].name}: ${formatTokens(totals.lanes[lane].tokens.total)} tokens`).join("; ")}
      </p>
    </div>
  )
}
