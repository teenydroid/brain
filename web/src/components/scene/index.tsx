'use client'

import dynamic from 'next/dynamic'
import { useEffect, useRef, useState } from 'react'

const Scene = dynamic(() => import('./scene').then((m) => m.Scene), {
  ssr: false,
  loading: () => null,
})

/**
 * The core scene, loaded in the browser only. Decorative, so hidden from
 * assistive tech. It stops rendering while it is scrolled out of view, so the
 * GPU is free for the scroll, and sits on its own compositing layer so the
 * page moving over it doesn't make it flicker.
 */
export function SceneCanvas({ className }: { className?: string }) {
  const box = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(true)
  useEffect(() => {
    const node = box.current
    if (!node) return
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { threshold: 0 })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  return (
    <div ref={box} className={className} style={{ transform: 'translateZ(0)', willChange: 'transform' }} aria-hidden>
      <Scene running={visible} />
    </div>
  )
}
