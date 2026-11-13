'use client'

import { useEffect, useState } from 'react'

/** 1 normally, 0 when the viewer asked for reduced motion. Scenes multiply their speed by it. */
export function useMotion(): number {
  const [motion, setMotion] = useState(1)
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const apply = () => setMotion(query.matches ? 0 : 1)
    apply()
    query.addEventListener('change', apply)
    return () => query.removeEventListener('change', apply)
  }, [])
  return motion
}

/** The neon palette, matched to the CSS tokens in globals.css. */
export const CYAN = '#19f0ff'
export const MAGENTA = '#ff2a6d'
export const YELLOW = '#f3e600'
export const VOID = '#06060b'
