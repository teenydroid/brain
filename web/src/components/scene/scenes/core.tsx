'use client'

import { useFrame, useThree } from '@react-three/fiber'
import { useRef } from 'react'
import * as THREE from 'three'
import { CYAN, MAGENTA, VOID, YELLOW, useMotion } from '../motion'

/**
 * The front page's centre: Teeny's mind as a black, glossy, liquid blob lit
 * from behind in neon. It never sits still while it thinks: every few seconds
 * a thought fires, a ripple runs across the skin, the veins inside flare and
 * the field around it pulls particles in. It darts about on its own; only the
 * orbits around it lean toward the pointer. Reduced
 * motion freezes the clock, so it holds one still shape.
 */

export const NOISE = /* glsl */ `
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0);
  const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy));
  vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz);
  vec3 l=1.0-g;
  vec3 i1=min(g.xyz,l.zxy);
  vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx;
  vec3 x2=x0-i2+C.yyy;
  vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857;
  vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z);
  vec4 x_=floor(j*ns.z);
  vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy;
  vec4 y=y_*ns.x+ns.yyyy;
  vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy);
  vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0;
  vec4 s1=floor(b1)*2.0+1.0;
  vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;
  vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x);
  vec3 p1=vec3(a0.zw,h.y);
  vec3 p2=vec3(a1.xy,h.z);
  vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);
  m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}
`

const BLOB_VERTEX = /* glsl */ `
uniform float uTime;
uniform float uThink;
uniform float uRadius;
uniform vec3 uRipple;
uniform float uRippleAge;
varying vec3 vNormal;
varying vec3 vView;
varying vec3 vDir;
varying float vLift;
${NOISE}

float field(vec3 d){
  float t=uTime;
  float n=snoise(d*0.85+vec3(0.0,t*0.24,t*0.17))*0.4;
  n+=snoise(d*1.8+vec3(t*0.34,0.0,-t*0.28))*(0.10+0.17*uThink);
  n+=snoise(d*4.2-vec3(t*0.4))*(0.015+0.03*uThink);
  // A thought landing: a ring running out across the skin from where it hit.
  float a=acos(clamp(dot(d,uRipple),-1.0,1.0));
  float front=uRippleAge*1.6;
  // Square by hand: pow() of a negative base is undefined in GLSL and comes out NaN on Metal.
  float k=(a-front)*5.0;
  float ring=exp(-k*k);
  n+=ring*0.16*exp(-uRippleAge*0.8);
  return n;
}

vec3 shape(vec3 d){ return d*(uRadius+field(d)); }

void main(){
  vec3 d=normalize(position);
  vec3 p=shape(d);
  vec3 t=normalize(cross(d,abs(d.y)>0.99?vec3(1.0,0.0,0.0):vec3(0.0,1.0,0.0)));
  vec3 b=normalize(cross(d,t));
  float e=0.012;
  vec3 pt=shape(normalize(d+t*e));
  vec3 pb=shape(normalize(d+b*e));
  // Guard the normal: a collapsed cross product would normalise to NaN.
  vec3 c=cross(pt-p,pb-p);
  vec3 n=dot(c,c)>1e-12?normalize(c):d;
  if(dot(n,d)<0.0) n=-n;
  vDir=d;
  vLift=field(d);
  vNormal=normalize(normalMatrix*n);
  vec4 mv=modelViewMatrix*vec4(p,1.0);
  vView=-mv.xyz;
  gl_Position=projectionMatrix*mv;
}
`

const BLOB_FRAGMENT = /* glsl */ `
uniform float uTime;
uniform float uThink;
uniform vec3 uCyan;
uniform vec3 uMagenta;
uniform vec3 uYellow;
varying vec3 vNormal;
varying vec3 vView;
varying vec3 vDir;
varying float vLift;
${NOISE}

void main(){
  vec3 N=normalize(vNormal);
  vec3 V=normalize(vView);
  // Clamped both ways: rounding can push the dot past 1, and pow() of the negative 1-ndv is NaN.
  float ndv=clamp(dot(N,V),0.0,1.0);
  float fres=pow(1.0-ndv,3.2);

  // Rim colour follows where the edge faces: cyan on the left, magenta on the right, yellow across the top.
  vec3 rim=mix(uCyan,uMagenta,smoothstep(-0.5,0.5,N.x));
  rim=mix(rim,uYellow,smoothstep(0.35,0.95,N.y)*0.85);

  // Two hard lights behind and above: the glossy streaks of a liquid surface.
  vec3 L1=normalize(vec3(0.55,0.75,-0.25));
  vec3 L2=normalize(vec3(-0.7,-0.35,-0.1));
  float s1=pow(max(dot(N,normalize(L1+V)),0.0),70.0);
  float s2=pow(max(dot(N,normalize(L2+V)),0.0),40.0);

  // Fake studio reflections: thin bright bands in the reflection vector.
  vec3 R=reflect(-V,N);
  float bands=smoothstep(0.965,1.0,sin(R.y*5.0+R.x*1.5+uTime*0.15)*0.5+0.5);

  // The thinking: thin veins of light under the skin that crawl and flare.
  float v=abs(snoise(vDir*2.4+vec3(0.0,uTime*0.35,uTime*0.2)));
  float vein=1.0-smoothstep(0.0,0.02+0.025*uThink,v);
  float v2=abs(snoise(vDir*5.0-vec3(uTime*0.5)));
  vein+=(1.0-smoothstep(0.0,0.015,v2))*0.35*uThink;
  vein*=(0.06+0.8*uThink)*smoothstep(0.25,0.95,ndv);
  vec3 veinCol=mix(uCyan,uMagenta,0.5+0.5*sin(vDir.y*3.0+uTime*0.6));

  vec3 col=vec3(0.006,0.005,0.012);
  col+=vec3(0.03,0.02,0.05)*smoothstep(-0.2,0.3,vLift);
  col+=rim*fres*(2.4+0.8*uThink);
  col+=uYellow*s1*1.4+uCyan*s2*0.9;
  col+=mix(rim,vec3(1.0),0.4)*bands*0.55*(0.3+fres);
  col+=veinCol*vein*1.3;
  gl_FragColor=vec4(col,1.0);
}
`

const SWARM_VERTEX = /* glsl */ `
uniform float uTime;
uniform float uThink;
uniform float uPixel;
attribute vec3 aSeed;
varying float vHeat;
varying float vFade;
void main(){
  // Each mote spirals in from the outer field toward the skin and is taken in.
  float life=fract(uTime*(0.06+aSeed.x*0.08)*(1.0+uThink*2.2)+aSeed.y);
  float r=mix(5.4,1.8,pow(life,1.4));
  float theta=aSeed.z*6.2831+life*(2.5+aSeed.x*3.0);
  float phi=acos(2.0*fract(aSeed.y*7.13+aSeed.z*3.1)-1.0)+sin(life*3.0+aSeed.x*9.0)*0.2;
  vec3 p=vec3(sin(phi)*cos(theta),cos(phi),sin(phi)*sin(theta))*r;
  vHeat=smoothstep(0.55,1.0,life);
  vFade=smoothstep(0.0,0.12,life)*(1.0-smoothstep(0.93,1.0,life));
  vec4 mv=modelViewMatrix*vec4(p,1.0);
  gl_PointSize=uPixel*(0.6+aSeed.x*1.4)*(1.0+vHeat*1.2)/-mv.z;
  gl_Position=projectionMatrix*mv;
}
`

const SWARM_FRAGMENT = /* glsl */ `
uniform vec3 uCyan;
uniform vec3 uYellow;
varying float vHeat;
varying float vFade;
void main(){
  vec2 c=gl_PointCoord-0.5;
  float d=length(c);
  float a=smoothstep(0.5,0.0,d);
  gl_FragColor=vec4(mix(uCyan,uYellow,vHeat)*a*vFade*(0.55+0.6*vHeat),a*vFade);
}
`

const RING_VERTEX = /* glsl */ `
varying vec2 vLocal;
void main(){ vLocal=position.xy; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }
`

const RING_FRAGMENT = /* glsl */ `
uniform vec3 uColor;
uniform float uTime;
uniform float uSegments;
uniform float uSpeed;
uniform float uDuty;
uniform float uOpacity;
varying vec2 vLocal;
void main(){
  float a=atan(vLocal.y,vLocal.x)/6.28318+0.5;
  float s=fract(a*uSegments+uTime*uSpeed);
  float on=step(s,uDuty);
  // One brighter arc sweeps round, like a scanner.
  float sweep=pow(fract(a-uTime*uSpeed*0.15),18.0);
  gl_FragColor=vec4(uColor*(on*uOpacity+sweep*1.5),on*uOpacity+sweep);
}
`

const BACKDROP_FRAGMENT = /* glsl */ `
uniform vec3 uVoid;
uniform vec3 uCyan;
uniform vec3 uMagenta;
uniform float uTime;
uniform vec2 uAspect;
varying vec2 vUv;
void main(){
  vec2 p=(vUv-0.5)*uAspect;
  // A dot grid, like an engineer's pad, fading out at the edges.
  vec2 g=fract(p*38.0)-0.5;
  float dotMask=smoothstep(0.09,0.0,length(g));
  float fade=1.0-smoothstep(0.2,1.1,length(p));
  vec3 col=uVoid;
  col+=vec3(0.18,0.2,0.26)*dotMask*0.35*fade;
  // Neon haze behind the blob, breathing.
  float glow=exp(-dot(p,p)*3.2);
  col+=mix(uMagenta,uCyan,0.5+0.5*sin(uTime*0.2+p.x*2.0))*glow*0.07;
  // Scan bar sliding down.
  float bar=smoothstep(0.012,0.0,abs(fract(vUv.y-uTime*0.04)-0.5));
  col+=uCyan*bar*0.025;
  gl_FragColor=vec4(col,1.0);
}
`

const SWARM = 2200

// Everything the scene draws, made once at module load. There is only ever
// one core on the page, and keeping these outside React lets the frame loop
// write their uniforms without fighting the compiler's immutability rules.

/** The thinking rhythm: a thought every 1.2 to 2.8 s, think jumps up and eases back, a ripple starts somewhere new. */
const MIND = { think: 0.25, next: 1.2, age: 10, origin: new THREE.Vector3(0, 1, 0) }

function think(elapsed: number, dt: number, motion: number) {
  if (motion > 0 && elapsed > MIND.next) {
    MIND.next = elapsed + 1.2 + Math.random() * 1.6
    MIND.think = 1
    MIND.age = 0
    MIND.origin.set(Math.random() * 2 - 1, Math.random() * 2 - 1, Math.random() * 2 - 1).normalize()
  }
  MIND.think = Math.max(0.3, MIND.think - dt * 0.6)
  MIND.age += dt
  return MIND
}

/**
 * How far the blob may dart from the centre. It stays well inside the inner
 * ring (radius 2.35 around a 1.7 blob), so the orbits always surround it.
 */
export const REACH = { x: 0.42, y: 0.28, z: 0.25, yaw: 0.8, pitch: 0.5, roll: 0.25 }

/**
 * The blob's own will: it holds still, then snaps somewhere new, sharp and
 * decisive, like it just made up its mind. Mostly a hold of up to two
 * seconds, sometimes a quick double take. It ignores the pointer.
 */
export const DART = {
  next: 0.8,
  pos: new THREE.Vector3(),
  rot: new THREE.Vector3(),
  /** 1 right after a dart, easing to 0: the shudder of a hard stop. */
  kick: 0,
}

const spread = (reach: number, random: () => number) => (random() * 2 - 1) * reach

/** Picks the next spot and when to leave it. Pure apart from writing into DART. */
export function dart(elapsed: number, random: () => number = Math.random) {
  if (elapsed <= DART.next) return false
  DART.next = elapsed + (random() < 0.25 ? 0.22 + random() * 0.2 : 0.7 + random() * 1.4)
  DART.pos.set(spread(REACH.x, random), spread(REACH.y, random), spread(REACH.z, random))
  DART.rot.set(spread(REACH.pitch, random), spread(REACH.yaw, random), spread(REACH.roll, random))
  DART.kick = 1
  return true
}

export const BLOB = new THREE.ShaderMaterial({
  vertexShader: BLOB_VERTEX,
  fragmentShader: BLOB_FRAGMENT,
  uniforms: {
    uTime: { value: 0 },
    uThink: { value: 0.25 },
    uRadius: { value: 1.7 },
    uRipple: { value: new THREE.Vector3(0, 1, 0) },
    uRippleAge: { value: 10 },
    uCyan: { value: new THREE.Color(CYAN) },
    uMagenta: { value: new THREE.Color(MAGENTA) },
    uYellow: { value: new THREE.Color(YELLOW) },
  },
})

const SWARM_GEOMETRY = (() => {
  const seeds = new Float32Array(SWARM * 3)
  for (let i = 0; i < seeds.length; i++) seeds[i] = Math.random()
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('aSeed', new THREE.BufferAttribute(seeds, 3))
  // Positions are computed in the shader; three.js still wants an attribute to size the draw.
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(SWARM * 3), 3))
  geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 6)
  return geometry
})()

const SWARM_MATERIAL = new THREE.ShaderMaterial({
  vertexShader: SWARM_VERTEX,
  fragmentShader: SWARM_FRAGMENT,
  transparent: true,
  depthWrite: false,
  blending: THREE.AdditiveBlending,
  uniforms: {
    uTime: { value: 0 },
    uThink: { value: 0 },
    uPixel: { value: 16 },
    uCyan: { value: new THREE.Color(CYAN) },
    uYellow: { value: new THREE.Color(YELLOW) },
  },
})

const RINGS = [
  { radius: 2.35, width: 0.012, color: CYAN, segments: 96, speed: 0.02, duty: 0.55, opacity: 0.5, tilt: [1.25, 0.2, 0] as [number, number, number] },
  { radius: 2.7, width: 0.035, color: MAGENTA, segments: 18, speed: -0.015, duty: 0.3, opacity: 0.45, tilt: [1.4, -0.35, 0.3] as [number, number, number] },
  { radius: 3.15, width: 0.008, color: YELLOW, segments: 240, speed: 0.01, duty: 0.25, opacity: 0.55, tilt: [1.1, 0.5, -0.2] as [number, number, number] },
].map((ring) => ({
  ...ring,
  material: new THREE.ShaderMaterial({
    vertexShader: RING_VERTEX,
    fragmentShader: RING_FRAGMENT,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uColor: { value: new THREE.Color(ring.color) },
      uTime: { value: 0 },
      uSegments: { value: ring.segments },
      uSpeed: { value: ring.speed },
      uDuty: { value: ring.duty },
      uOpacity: { value: ring.opacity },
    },
  }),
}))

const BACKDROP = new THREE.ShaderMaterial({
  vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
  fragmentShader: BACKDROP_FRAGMENT,
  depthWrite: false,
  uniforms: {
    uVoid: { value: new THREE.Color(VOID) },
    uCyan: { value: new THREE.Color(CYAN) },
    uMagenta: { value: new THREE.Color(MAGENTA) },
    uTime: { value: 0 },
    uAspect: { value: new THREE.Vector2(1, 1) },
  },
})

export function CoreScene() {
  const motion = useMotion()
  const { viewport, size } = useThree()
  const orbits = useRef<THREE.Group>(null)
  const body = useRef<THREE.Group>(null)
  const clock = useRef(0)

  // The backdrop sits at z = -4 and has to cover the whole frame from there.
  const far = viewport.getCurrentViewport(undefined, new THREE.Vector3(0, 0, -4))
  // Smaller screens get a smaller blob so the hotspots and copy keep room.
  const scale = size.width < 640 ? 0.78 : size.width < 1024 ? 0.9 : 1

  useFrame((state, delta) => {
    const dt = Math.min(delta, 0.05) * motion
    clock.current += dt
    const t = clock.current
    const s = think(t, dt, motion)

    BLOB.uniforms.uTime.value = t
    BLOB.uniforms.uThink.value = s.think
    BLOB.uniforms.uRippleAge.value = s.age
    ;(BLOB.uniforms.uRipple.value as THREE.Vector3).copy(s.origin)
    SWARM_MATERIAL.uniforms.uTime.value = t
    SWARM_MATERIAL.uniforms.uThink.value = s.think
    SWARM_MATERIAL.uniforms.uPixel.value = 16 * state.viewport.dpr
    for (const ring of RINGS) ring.material.uniforms.uTime.value = t
    BACKDROP.uniforms.uTime.value = t
    ;(BACKDROP.uniforms.uAspect.value as THREE.Vector2).set(far.width / far.height, 1)

    // The orbits lean toward the pointer; when no one is pointing they drift on their own.
    if (orbits.current) {
      const px = motion ? state.pointer.x : 0
      const py = motion ? state.pointer.y : 0
      orbits.current.rotation.y = THREE.MathUtils.lerp(orbits.current.rotation.y, px * 0.45 + Math.sin(t * 0.12) * 0.25, 0.04)
      orbits.current.rotation.x = THREE.MathUtils.lerp(orbits.current.rotation.x, -py * 0.3 + Math.cos(t * 0.09) * 0.1, 0.04)
    }

    // The blob goes where it wants: a fast, firm snap to each new spot, a small shudder as it stops.
    if (body.current && motion) {
      dart(t)
      const snap = 1 - Math.exp(-dt * 15)
      DART.kick = Math.max(0, DART.kick - dt * 3.5)
      const shudder = DART.kick * DART.kick * 0.035
      body.current.position.set(
        THREE.MathUtils.lerp(body.current.position.x, DART.pos.x, snap) + Math.sin(t * 47) * shudder,
        THREE.MathUtils.lerp(body.current.position.y, DART.pos.y, snap) + Math.sin(t * 53 + 1.3) * shudder,
        THREE.MathUtils.lerp(body.current.position.z, DART.pos.z, snap),
      )
      body.current.rotation.x = THREE.MathUtils.lerp(body.current.rotation.x, DART.rot.x, snap)
      body.current.rotation.y = THREE.MathUtils.lerp(body.current.rotation.y, DART.rot.y, snap)
      body.current.rotation.z = THREE.MathUtils.lerp(body.current.rotation.z, DART.rot.z, snap)
    }
  })

  return (
    <>
      <mesh position={[0, 0, -4]} material={BACKDROP}>
        <planeGeometry args={[far.width * 1.05, far.height * 1.05]} />
      </mesh>
      <group scale={scale}>
        <group ref={body}>
          <mesh material={BLOB}>
            <icosahedronGeometry args={[1, 96]} />
          </mesh>
        </group>
        <group ref={orbits}>
          <points geometry={SWARM_GEOMETRY} material={SWARM_MATERIAL} />
          {RINGS.map((ring, i) => (
            <mesh key={i} material={ring.material} rotation={ring.tilt}>
              <ringGeometry args={[ring.radius, ring.radius + ring.width, 256, 1]} />
            </mesh>
          ))}
        </group>
      </group>
    </>
  )
}
