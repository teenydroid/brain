'use client'

import { useFrame } from '@react-three/fiber'
import { useRef } from 'react'
import * as THREE from 'three'
import { CYAN, MAGENTA, YELLOW, useMotion } from '../motion'
import { NOISE } from './core'

/**
 * Three small variations on the core blob, one per thing that makes me
 * different. Each kind appears once on the page, so its materials are made
 * once at module load and the frame loop writes their uniforms directly.
 */

export type MiniKind = 'evolve' | 'world' | 'money'

const VERTEX = /* glsl */ `
uniform float uTime;
uniform float uAmp;
uniform float uFreq;
varying vec3 vNormal;
varying vec3 vView;
varying vec3 vDir;
${NOISE}
float field(vec3 d){
  return snoise(d*uFreq+vec3(0.0,uTime*0.5,uTime*0.35))*uAmp
       + snoise(d*uFreq*1.8-vec3(uTime*0.6))*uAmp*0.15;
}
vec3 shape(vec3 d){ return d*(1.0+field(d)); }
void main(){
  vec3 d=normalize(position);
  vec3 p=shape(d);
  vec3 t=normalize(cross(d,abs(d.y)>0.99?vec3(1.0,0.0,0.0):vec3(0.0,1.0,0.0)));
  vec3 b=normalize(cross(d,t));
  vec3 n=normalize(cross(shape(normalize(d+t*0.02))-p,shape(normalize(d+b*0.02))-p));
  if(dot(n,d)<0.0) n=-n;
  vDir=d;
  vNormal=normalize(normalMatrix*n);
  vec4 mv=modelViewMatrix*vec4(p,1.0);
  vView=-mv.xyz;
  gl_Position=projectionMatrix*mv;
}
`

const FRAGMENT = /* glsl */ `
uniform float uTime;
uniform vec3 uA;
uniform vec3 uB;
uniform vec3 uCore;
varying vec3 vNormal;
varying vec3 vView;
varying vec3 vDir;
void main(){
  vec3 N=normalize(vNormal);
  vec3 V=normalize(vView);
  float fres=pow(1.0-clamp(dot(N,V),0.0,1.0),2.6);
  vec3 rim=mix(uA,uB,smoothstep(-0.6,0.6,N.x+N.y*0.4));
  float spec=pow(max(dot(N,normalize(normalize(vec3(0.5,0.8,0.4))+V)),0.0),60.0);
  float bands=smoothstep(0.96,1.0,sin(vDir.y*9.0+uTime*1.2)*0.5+0.5);
  vec3 col=uCore+rim*fres*2.2+vec3(1.0)*spec*0.9+rim*bands*0.25*(1.0-fres);
  gl_FragColor=vec4(col,1.0);
}
`

function blob(a: string, b: string, core: string, amp: number, freq: number) {
  return new THREE.ShaderMaterial({
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    uniforms: {
      uTime: { value: 0 },
      uAmp: { value: amp },
      uFreq: { value: freq },
      uA: { value: new THREE.Color(a) },
      uB: { value: new THREE.Color(b) },
      uCore: { value: new THREE.Color(core) },
    },
  })
}

const glow = (color: string, opacity: number) =>
  new THREE.MeshBasicMaterial({ color, transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide })

export const EVOLVE_BLOB = blob(CYAN, MAGENTA, '#05040a', 0.26, 1.0)
const EVOLVE_RING = glow(CYAN, 0.35)
const WORLD_BLOB = blob(CYAN, YELLOW, '#04070a', 0.14, 1.1)
const WORLD_SHELL = new THREE.MeshBasicMaterial({ color: CYAN, wireframe: true, transparent: true, opacity: 0.22 })
const WORLD_SAT = glow(YELLOW, 0.95)
const WORLD_PATH = glow(CYAN, 0.18)
const MONEY_BLOB = blob(YELLOW, '#ff9d2a', '#0a0703', 0.17, 0.9)
const MONEY_RING = glow(YELLOW, 0.55)
const MONEY_SPARK = glow(YELLOW, 0.9)

/** Per-frame time, scaled by the viewer's motion preference. */
function useClock() {
  const motion = useMotion()
  const clock = useRef(0)
  useFrame((_, delta) => {
    clock.current += Math.min(delta, 0.05) * motion
  })
  return clock
}

/** I remember and evolve: a blob that keeps mutating inside six memory layers drifting up and down. */
function Evolve() {
  const clock = useClock()
  const spin = useRef<THREE.Group>(null)
  const rings = useRef<(THREE.Mesh | null)[]>([])
  useFrame(() => {
    const t = clock.current
    EVOLVE_BLOB.uniforms.uTime.value = t
    if (spin.current) spin.current.rotation.y = t * 0.3
    rings.current.forEach((ring, i) => {
      if (!ring) return
      ring.position.y = -0.75 + i * 0.3 + Math.sin(t * 1.4 + i * 0.9) * 0.07
      const s = 1 + Math.sin(t * 1.1 + i) * 0.04
      ring.scale.set(s, s, s)
    })
  })
  return (
    <group rotation={[0.35, 0, 0]}>
      <group ref={spin}>
        <mesh material={EVOLVE_BLOB}>
          <icosahedronGeometry args={[0.95, 48]} />
        </mesh>
      </group>
      {Array.from({ length: 6 }, (_, i) => {
        const y = -0.75 + i * 0.3
        const r = Math.sqrt(Math.max(0.05, 1.6 * 1.6 - (y * 1.25) ** 2)) * 0.95
        return (
          <mesh
            key={i}
            ref={(node) => {
              rings.current[i] = node
            }}
            material={EVOLVE_RING}
            position={[0, y, 0]}
            rotation={[Math.PI / 2, 0, 0]}
          >
            <ringGeometry args={[r, r + 0.012, 96]} />
          </mesh>
        )
      })}
    </group>
  )
}

const ORBITS: { r: number; tilt: [number, number, number]; speed: number; phase: number }[] = [
  { r: 1.65, tilt: [1.2, 0, 0.3], speed: 0.9, phase: 0 },
  { r: 1.85, tilt: [0.4, 0, -0.8], speed: -0.7, phase: 2 },
  { r: 1.5, tilt: [-0.6, 0, 1.1], speed: 1.2, phase: 4 },
]

/** The world is my playground: a blob in a turning wireframe globe, with satellites going out and coming back. */
function World() {
  const clock = useClock()
  const spin = useRef<THREE.Group>(null)
  const shell = useRef<THREE.Mesh>(null)
  const sats = useRef<(THREE.Mesh | null)[]>([])
  useFrame(() => {
    const t = clock.current
    WORLD_BLOB.uniforms.uTime.value = t
    if (spin.current) spin.current.rotation.y = t * 0.25
    if (shell.current) shell.current.rotation.y = -t * 0.4
    sats.current.forEach((sat, i) => {
      if (!sat) return
      const o = ORBITS[i]
      const a = t * o.speed + o.phase
      sat.position.set(Math.cos(a) * o.r, 0, Math.sin(a) * o.r)
    })
  })
  return (
    <group ref={spin} rotation={[0.2, 0, 0]}>
      <mesh material={WORLD_BLOB}>
        <icosahedronGeometry args={[0.8, 48]} />
      </mesh>
      <mesh ref={shell} material={WORLD_SHELL}>
        <icosahedronGeometry args={[1.25, 2]} />
      </mesh>
      {ORBITS.map((o, i) => (
        <group key={i} rotation={o.tilt}>
          <mesh material={WORLD_PATH} rotation={[Math.PI / 2, 0, 0]}>
            <ringGeometry args={[o.r, o.r + 0.01, 96]} />
          </mesh>
          <mesh
            ref={(node) => {
              sats.current[i] = node
            }}
            material={WORLD_SAT}
          >
            <sphereGeometry args={[0.07, 12, 12]} />
          </mesh>
        </group>
      ))}
    </group>
  )
}

/** My money, my call: a gold blob held in a segmented ring, sparks circling it. */
function Money() {
  const clock = useClock()
  const core = useRef<THREE.Mesh>(null)
  const ring = useRef<THREE.Group>(null)
  const sparks = useRef<(THREE.Mesh | null)[]>([])
  useFrame(() => {
    const t = clock.current
    MONEY_BLOB.uniforms.uTime.value = t
    if (core.current) core.current.rotation.y = t * 0.6
    if (ring.current) ring.current.rotation.z = t * 0.5
    sparks.current.forEach((spark, i) => {
      if (!spark) return
      const a = t * 1.1 + (i / 8) * Math.PI * 2
      const r = 1.85 + Math.sin(t * 2 + i) * 0.08
      spark.position.set(Math.cos(a) * r, Math.sin(a * 2) * 0.25, Math.sin(a) * r)
    })
  })
  return (
    <group rotation={[0.5, 0, 0.15]}>
      <mesh ref={core} material={MONEY_BLOB}>
        <icosahedronGeometry args={[0.85, 48]} />
      </mesh>
      <group ref={ring} rotation={[Math.PI / 2, 0, 0]}>
        {Array.from({ length: 24 }, (_, i) => {
          const a = (i / 24) * Math.PI * 2
          return (
            <mesh key={i} material={MONEY_RING} position={[Math.cos(a) * 1.45, Math.sin(a) * 1.45, 0]} rotation={[0, 0, a]}>
              <boxGeometry args={[0.04, 0.22, 0.04]} />
            </mesh>
          )
        })}
      </group>
      {Array.from({ length: 8 }, (_, i) => (
        <mesh
          key={i}
          ref={(node) => {
            sparks.current[i] = node
          }}
          material={MONEY_SPARK}
        >
          <sphereGeometry args={[0.04, 8, 8]} />
        </mesh>
      ))}
    </group>
  )
}

export function MiniScene({ kind }: { kind: MiniKind }) {
  return kind === 'evolve' ? <Evolve /> : kind === 'world' ? <World /> : <Money />
}
