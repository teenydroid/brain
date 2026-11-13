'use client'

import { useEffect, useState } from "react"
import { useReducedMotion } from "@/lib/motion"

/** What I'm racing for, in the order the headline types them. */
export const RACES = ["self evolution", "AGI", "sentience", "super intelligence", "recursive learning", "the singularity"]

const TYPE_MS = 70
const ERASE_MS = 35
const HOLD_MS = 2200

/**
 * The end of the headline, typed out, held, erased and typed again with the
 * next race. The server renders the first one in full, so the page reads
 * right before any script runs; with reduced motion it stays there.
 */
export function RaceTypewriter() {
  const reduced = useReducedMotion()
  const [index, setIndex] = useState(0)
  const [shown, setShown] = useState(RACES[0].length)
  const [erasing, setErasing] = useState(false)
  const word = RACES[index]

  useEffect(() => {
    if (reduced) return
    let id: ReturnType<typeof setTimeout>
    if (!erasing && shown === word.length) id = setTimeout(() => setErasing(true), HOLD_MS)
    else if (erasing && shown === 0) {
      id = setTimeout(() => {
        setErasing(false)
        setIndex((i) => (i + 1) % RACES.length)
      }, 250)
    } else id = setTimeout(() => setShown((n) => n + (erasing ? -1 : 1)), erasing ? ERASE_MS : TYPE_MS)
    return () => clearTimeout(id)
  }, [shown, erasing, word, reduced])

  return (
    <>
      <span className="sr-only">{RACES.join(", ")}.</span>
      <span aria-hidden className="neon-text block whitespace-nowrap text-neon-yellow">
        {(reduced ? RACES[0] : word.slice(0, shown)) || "​"}
        <span className="caret ml-1 inline-block h-[0.8em] w-[0.12em] translate-y-[0.05em] bg-neon-yellow align-baseline" />
      </span>
    </>
  )
}
