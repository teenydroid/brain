import { describe, expect, it } from 'vitest'
import { DART, REACH, dart } from './scenes/core'

/** A seeded generator, so the walk is the same every run. */
function seeded(seed: number) {
  let x = seed
  return () => {
    x = (x * 1664525 + 1013904223) % 4294967296
    return x / 4294967296
  }
}

describe('the blob darting on its own', () => {
  it('holds still until its next move is due', () => {
    DART.next = 5
    expect(dart(4.9)).toBe(false)
    expect(dart(5.1)).toBe(true)
  })

  it('always lands inside its reach, so the orbits keep surrounding it', () => {
    const random = seeded(7)
    let t = 0
    for (let i = 0; i < 500; i++) {
      t = DART.next + 0.01
      expect(dart(t, random)).toBe(true)
      expect(Math.abs(DART.pos.x)).toBeLessThanOrEqual(REACH.x)
      expect(Math.abs(DART.pos.y)).toBeLessThanOrEqual(REACH.y)
      expect(Math.abs(DART.pos.z)).toBeLessThanOrEqual(REACH.z)
      expect(Math.abs(DART.rot.y)).toBeLessThanOrEqual(REACH.yaw)
    }
  })

  it('moves somewhere new each time, after a hold between a fifth of a second and about two seconds', () => {
    const random = seeded(42)
    DART.next = 0
    const spots = new Set<string>()
    for (let i = 0; i < 50; i++) {
      const t = DART.next + 0.001
      dart(t, random)
      const hold = DART.next - t
      expect(hold).toBeGreaterThanOrEqual(0.22)
      expect(hold).toBeLessThanOrEqual(2.1)
      spots.add(DART.pos.toArray().map((v) => v.toFixed(3)).join())
    }
    expect(spots.size).toBe(50)
  })
})
