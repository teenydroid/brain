'use client'

import { Canvas } from '@react-three/fiber'
import { Bloom, ChromaticAberration, EffectComposer, Noise, Vignette } from '@react-three/postprocessing'
import { BlendFunction } from 'postprocessing'
import { Vector2 } from 'three'
import type { SceneName } from '@/lib/pages'
import { BrainScene } from './scenes/brain'
import { CoreScene } from './scenes/core'
import { GridScene } from './scenes/grid'
import { LatticeScene } from './scenes/lattice'
import { OrbitScene } from './scenes/orbit'
import { ShardsScene } from './scenes/shards'
import { SoulScene } from './scenes/soul'
import { StreamScene } from './scenes/stream'

interface SceneSpec {
  component: React.ComponentType
  camera: [number, number, number]
  fov?: number
  /** Opaque, with bloom and lens grit. Only the scenes that draw their own backdrop can have it. */
  post?: boolean
}

const SCENES: Record<SceneName, SceneSpec> = {
  core: { component: CoreScene, camera: [0, 0, 6.2], fov: 45, post: true },
  brain: { component: BrainScene, camera: [0, 0, 6] },
  stream: { component: StreamScene, camera: [0, 0.4, 7], fov: 45 },
  soul: { component: SoulScene, camera: [0, 0, 5.2] },
  lattice: { component: LatticeScene, camera: [0, 0.6, 6] },
  orbit: { component: OrbitScene, camera: [0, 2.4, 6.5] },
  grid: { component: GridScene, camera: [0, 0, 7] },
  shards: { component: ShardsScene, camera: [0, 0, 8.5] },
}

const ABERRATION = new Vector2(0.0009, 0.0006)

export function Scene({ name }: { name: SceneName }) {
  const { component: Body, camera, fov = 50, post = false } = SCENES[name]
  return (
    <Canvas
      dpr={[1, post ? 2 : 1.5]}
      camera={{ position: camera, fov }}
      gl={{ antialias: !post, alpha: !post, powerPreference: post ? 'high-performance' : 'low-power' }}
      style={{ background: 'transparent' }}
      eventSource={typeof document !== 'undefined' ? document.body : undefined}
      eventPrefix="client"
    >
      <Body />
      {post ? (
        <EffectComposer multisampling={4}>
          <Bloom mipmapBlur intensity={1.25} luminanceThreshold={0.32} luminanceSmoothing={0.2} radius={0.75} />
          <ChromaticAberration offset={ABERRATION} radialModulation={false} modulationOffset={0} />
          <Noise opacity={0.06} blendFunction={BlendFunction.OVERLAY} />
          <Vignette eskil={false} offset={0.25} darkness={0.75} />
        </EffectComposer>
      ) : null}
    </Canvas>
  )
}
