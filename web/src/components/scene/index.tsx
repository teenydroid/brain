'use client'

import dynamic from 'next/dynamic'

const Scene = dynamic(() => import('./scene').then((m) => m.Scene), {
  ssr: false,
  loading: () => null,
})

/** The core scene, loaded in the browser only. Decorative, so hidden from assistive tech. */
export function SceneCanvas({ className }: { className?: string }) {
  return (
    <div className={className} aria-hidden>
      <Scene />
    </div>
  )
}
