'use client'

import { Canvas } from '@react-three/fiber'
import { Bloom, ChromaticAberration, EffectComposer, Noise, Vignette } from '@react-three/postprocessing'
import { BlendFunction } from 'postprocessing'
import { Vector2 } from 'three'
import { CoreScene } from './scenes/core'

const ABERRATION = new Vector2(0.0014, 0.0009)

/** The one scene on the site: the core, opaque, with bloom and a little lens grit. */
export function Scene() {
  return (
    <Canvas
      dpr={[1, 2]}
      camera={{ position: [0, 0, 6.2], fov: 45 }}
      gl={{ antialias: false, alpha: false, powerPreference: 'high-performance' }}
      // The HUD sits over the canvas; reading the pointer from the body lets the blob follow it anyway.
      eventSource={typeof document !== 'undefined' ? document.body : undefined}
      eventPrefix="client"
    >
      <CoreScene />
      <EffectComposer multisampling={4}>
        <Bloom mipmapBlur intensity={1.45} luminanceThreshold={0.36} luminanceSmoothing={0.2} radius={0.8} />
        <ChromaticAberration offset={ABERRATION} radialModulation={false} modulationOffset={0} />
        <Noise opacity={0.06} blendFunction={BlendFunction.OVERLAY} />
        <Vignette eskil={false} offset={0.25} darkness={0.75} />
      </EffectComposer>
    </Canvas>
  )
}
