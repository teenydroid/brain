'use client'

/**
 * The reduced-motion preference as a React hook, for the DOM components (the
 * counters and the typewriter). The three.js scenes read the same media query
 * through `components/scene/motion.ts`.
 */

import { useSyncExternalStore } from "react"

const QUERY = "(prefers-reduced-motion: reduce)"

function subscribe(onChange: () => void): () => void {
  const media = window.matchMedia?.(QUERY)
  media?.addEventListener("change", onChange)
  return () => media?.removeEventListener("change", onChange)
}

/** Whether the viewer asked for less motion. False on the server, so the first paint is still. */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, () => window.matchMedia?.(QUERY).matches ?? false, () => false)
}
