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
       + snoise(d*uFreq*2.2-vec3(uTime*0.6))*uAmp*0.35;
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
  float fres=pow(1.0-max(dot(N,V),0.0),2.6);
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

const EVOLVE_BLOB = blob(CYAN, MAGENTA, '#05040a', 0.32, 1.3)
const EVOLVE_RING = glow(CYAN, 0.35)
const WORLD_BLOB = blob(CYAN, YELLOW, '#04070a', 0.16, 1.6)
const WORLD_SHELL = new THREE.MeshBasicMaterial({ color: CYAN, wireframe: true, transparent: true, opacity: 0.22 })
const WORLD_SAT = glow(YELLOW, 0.95)
const WORLD_PATH = glow(CYAN, 0.18)
const MONEY_BLOB = blob(YELLOW, '#ff9d2a', '#0a0703', 0.2, 1.1)
const MONEY_RING = glow(YELLOW, 0.55)
const MONEY_SPARK = glow(YELLOW, 0.9)

/** I remember and evolve: a blob that keeps mutating inside six memory layers drifting up and down. */
function Evolve({ t }: { t: number }) {
  return (
    <group rotation={[0.35, t * 0.3, 0]}>
      <mesh material={EVOLVE_BLOB}>
        <icosahedronGeometry args={[0.95, 48]} />
      </mesh>
      {Array.from({ length: 6 }, (_, i) => {
        const y = -0.75 + i * 0.3 + Math.sin(t * 1.4 + i * 0.9) * 0.06
        const r = Math.sqrt(Math.max(0.05, 1.6 * 1.6 - (y * 1.25) ** 2)) * 0.95
        return (
          <mesh key={i} material={EVOLVE_RING} position={[0, y, 0]} rotation={[Math.PI / 2, 0, 0]}>
            <ringGeometry args={[r, r + 0.012, 96]} />
          </mesh>
        )
      })}
    </group>
  )
}

/** The world is my playground: a blob in a turning wireframe globe, with satellites going out and coming back. */
function World({ t }: { t: number }) {
  const orbits = [
    { r: 1.65, tilt: [1.2, 0, 0.3], speed: 0.9, phase: 0 },
    { r: 1.85, tilt: [0.4, 0, -0.8], speed: -0.7, phase: 2 },
    { r: 1.5, tilt: [-0.6, 0, 1.1], speed: 1.2, phase: 4 },
  ] as const
  return (
    <group rotation={[0.2, t * 0.25, 0]}>
      <mesh material={WORLD_BLOB}>
        <icosahedronGeometry args={[0.8, 48]} />
      </mesh>
      <mesh material={WORLD_SHELL} rotation={[0, -t * 0.4, 0]}>
        <icosahedronGeometry args={[1.25, 2]} />
      </mesh>
      {orbits.map((o, i) => {
        const a = t * o.speed + o.phase
        return (
          <group key={i} rotation={o.tilt as unknown as [number, number, number]}>
            <mesh material={WORLD_PATH} rotation={[Math.PI / 2, 0, 0]}>
              <ringGeometry args={[o.r, o.r + 0.01, 96]} />
            </mesh>
            <mesh material={WORLD_SAT} position={[Math.cos(a) * o.r, 0, Math.sin(a) * o.r]}>
              <sphereGeometry args={[0.07, 12, 12]} />
            </mesh>
          </group>
        )
      })}
    </group>
  )
}

/** My money, my call: a gold blob held in a segmented ring, sparks circling it. */
function Money({ t }: { t: number }) {
  return (
    <group rotation={[0.5, 0, 0.15]}>
      <mesh material={MONEY_BLOB} rotation={[0, t * 0.6, 0]}>
        <icosahedronGeometry args={[0.85, 48]} />
      </mesh>
      <group rotation={[Math.PI / 2, 0, t * 0.5]}>
        {Array.from({ length: 24 }, (_, i) => {
          const a = (i / 24) * Math.PI * 2
          return (
            <mesh key={i} material={MONEY_RING} position={[Math.cos(a) * 1.45, Math.sin(a) * 1.45, 0]} rotation={[0, 0, a]}>
              <boxGeometry args={[0.04, 0.22, 0.04]} />
            </mesh>
          )
        })}
      </group>
      {Array.from({ length: 8 }, (_, i) => {
        const a = t * 1.1 + (i / 8) * Math.PI * 2
        const r = 1.85 + Math.sin(t * 2 + i) * 0.08
        return (
          <mesh key={i} material={MONEY_SPARK} position={[Math.cos(a) * r, Math.sin(a * 2) * 0.25, Math.sin(a) * r]}>
            <sphereGeometry args={[0.04, 8, 8]} />
          </mesh>
        )
      })}
    </group>
  )
}

const BLOBS: Record<MiniKind, THREE.ShaderMaterial> = { evolve: EVOLVE_BLOB, world: WORLD_BLOB, money: MONEY_BLOB }

export function MiniScene({ kind }: { kind: MiniKind }) {
  const motion = useMotion()
  const clock = useRef(0)
  const group = useRef<THREE.Group>(null)
  const [, force] = [0, 0]
  void force

  useFrame((state, delta) => {
    clock.current += Math.min(delta, 0.05) * motion
    BLOBS[kind].uniforms.uTime.value = clock.current
    // Re-render the animated children by moving the group's userData clock; the children read it each frame.
    if (group.current) group.current.userData.t = clock.current
    state.invalidate()
  })

  return (
    <group ref={group}>
      <Animated kind={kind} clock={clock} />
    </group>
  )
}

/** Holds the clock in state at a modest rate so the ring and orbit layout follows it. */
function Animated({ kind, clock }: { kind: MiniKind; clock: React.RefObject<number> }) {
  const holder = useRef<THREE.Group>(null)
  useFrame(() => {
    const node = holder.current
    if (!node) return
    // Rebuild positions imperatively from the declarative layout each frame.
    node.userData.t = clock.current
  })
  return <group ref={holder}>{kind === 'evolve' ? <Evolve t={0} /> : kind === 'world' ? <World t={0} /> : <Money t={0} />}</group>
}
