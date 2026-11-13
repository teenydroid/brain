'use client'

import { Canvas } from '@react-three/fiber'
import { MiniScene, type MiniKind } from './scenes/mini'

/** A small transparent canvas for one of the feature blobs. */
export function MiniCanvasInner({ kind, running }: { kind: MiniKind; running: boolean }) {
  return (
    <Canvas
      dpr={[1, 2]}
      frameloop={running ? 'always' : 'never'}
      camera={{ position: [0, 0, 5], fov: 40 }}
      gl={{ antialias: true, alpha: true, powerPreference: 'low-power' }}
      style={{ background: 'transparent' }}
    >
      <MiniScene kind={kind} />
    </Canvas>
  )
}
