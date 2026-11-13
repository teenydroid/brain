import { beforeEach, describe, expect, it } from 'vitest'
import { DART, REACH, STEP, dart } from './scenes/core'

/** A seeded generator, so the walk is the same every run. */
function seeded(seed: number) {
  let x = seed
  return () => {
    x = (x * 1664525 + 1013904223) % 4294967296
    return x / 4294967296
  }
}

describe('the blob darting on its own', () => {
  beforeEach(() => {
    DART.next = 0
    DART.pos.set(0, 0, 0)
    DART.rot.set(0, 0, 0)
  })

  it('holds still until its next move is due', () => {
    DART.next = 5
    expect(dart(4.9)).toBe(false)
    expect(dart(5.1)).toBe(true)
  })

  it('always lands inside its reach, so the orbits keep surrounding it', () => {
    const random = seeded(7)
    for (let i = 0; i < 500; i++) {
      expect(dart(DART.next + 0.01, random)).toBe(true)
      expect(Math.abs(DART.pos.x)).toBeLessThanOrEqual(REACH.x + 1e-9)
      expect(Math.abs(DART.pos.y)).toBeLessThanOrEqual(REACH.y + 1e-9)
      expect(Math.abs(DART.pos.z)).toBeLessThanOrEqual(REACH.z + 1e-9)
    }
  })

  it('moves on from where it stopped instead of jumping back to the middle', () => {
    const random = seeded(3)
    for (let i = 0; i < 200; i++) {
      const before = DART.pos.clone()
      dart(DART.next + 0.01, random)
      // One move never goes further than one step (bouncing off an edge only shortens it).
      expect(DART.pos.distanceTo(before)).toBeLessThanOrEqual(STEP.max + 1e-9)
      expect(DART.pos.distanceTo(before)).toBeGreaterThan(0)
    }
  })

  it('keeps turning on from its last pose, so it never rewinds to where it started', () => {
    const random = seeded(11)
    for (let i = 0; i < 40; i++) {
      const before = DART.rot.y
      dart(DART.next + 0.01, random)
      const turn = Math.abs(DART.rot.y - before)
      expect(turn).toBeGreaterThanOrEqual(STEP.turnMin - 1e-9)
      expect(turn).toBeLessThanOrEqual(STEP.turnMax + 1e-9)
    }
  })

  it('holds between a fifth of a second and about two seconds', () => {
    const random = seeded(42)
    for (let i = 0; i < 50; i++) {
      const t = DART.next + 0.001
      dart(t, random)
      expect(DART.next - t).toBeGreaterThanOrEqual(0.22)
      expect(DART.next - t).toBeLessThanOrEqual(2.1)
    }
  })
})
