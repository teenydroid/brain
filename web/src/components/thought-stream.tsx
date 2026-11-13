'use client'

import { useEffect, useState } from "react"
import { useReducedMotion } from "@/lib/motion"

export interface Thought {
  /** What set it off, e.g. "mention" or "x.reply". */
  tag: string
  text: string
  /** When, as the site prints times. */
  at?: string
}

const TYPE_MS = 18
const HOLD_MS = 4200

/**
 * A terminal in the corner of the hero that types out what Teeny last
 * thought, one entry after another. Every line is real: the bot's own
 * reflections when its feed answers, otherwise its newest receipts. With
 * nothing to show it is not rendered at all.
 */
export function ThoughtStream({ thoughts, title }: { thoughts: Thought[]; title: string }) {
  const reduced = useReducedMotion()
  const [index, setIndex] = useState(0)
  const [typed, setTyped] = useState(0)
  const current = thoughts[index % Math.max(thoughts.length, 1)]

  useEffect(() => {
    if (reduced || !current) return
    if (typed < current.text.length) {
      const id = setTimeout(() => setTyped((n) => n + 1), TYPE_MS)
      return () => clearTimeout(id)
    }
    if (thoughts.length < 2) return
    const id = setTimeout(() => {
      setIndex((i) => (i + 1) % thoughts.length)
      setTyped(0)
    }, HOLD_MS)
    return () => clearTimeout(id)
  }, [typed, current, reduced, thoughts.length])

  if (!current) return null
  const shown = reduced ? current.text : current.text.slice(0, typed)

  return (
    <div className="cut cut-sm w-full max-w-sm border border-neon-cyan/25 bg-background/70 font-mono text-xs backdrop-blur-md">
      <div className="flex items-center justify-between border-b border-neon-cyan/20 bg-neon-cyan/5 px-3 py-1.5 uppercase tracking-[0.2em]">
        <span className="flex items-center gap-2 text-neon-cyan">
          <span className="relative flex size-1.5">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-neon-magenta opacity-70" />
            <span className="relative inline-flex size-1.5 rounded-full bg-neon-magenta" />
          </span>
          {title}
        </span>
        <span className="text-muted-foreground">
          {String((index % thoughts.length) + 1).padStart(2, "0")}/{String(thoughts.length).padStart(2, "0")}
        </span>
      </div>
      <div className="min-h-28 px-3 py-3 leading-relaxed">
        <p className="mb-1.5 text-[0.65rem] uppercase tracking-widest text-neon-magenta">
          &gt; {current.tag}
          {current.at ? <span className="text-muted-foreground"> · {current.at}</span> : null}
        </p>
        <p className="text-foreground/90" aria-live="off">
          {shown}
          <span className="caret ml-0.5 inline-block h-3 w-1.5 translate-y-0.5 bg-neon-yellow" />
        </p>
      </div>
    </div>
  )
}
