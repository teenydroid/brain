import { describe, expect, it } from 'vitest'
import { BLOB } from './scenes/core'
import { EVOLVE_BLOB } from './scenes/mini'

/** The first argument of every pow() call in a GLSL source. */
function powBases(source: string): string[] {
  const bases: string[] = []
  let at = source.indexOf('pow(')
  while (at !== -1) {
    let depth = 0
    let i = at + 4
    const start = i
    for (; i < source.length; i++) {
      const ch = source[i]
      if (ch === '(') depth++
      else if (ch === ')') depth--
      if (depth === 0 && ch === ',') break
    }
    bases.push(source.slice(start, i).replace(/\s+/g, ''))
    at = source.indexOf('pow(', i)
  }
  return bases
}

// A base is safe when it is clamped, maxed or a fract(), or is 1 minus a value clamped to [0, 1].
const SAFE = /^(max\(|clamp\(|fract\(|1\.0-clamp\(|1\.0-ndv$|life$)/

describe('blob shaders', () => {
  // One NaN pixel, spread across the frame by the bloom blur, turns the whole hero black for a frame.
  const shaders = {
    'core vertex': BLOB.vertexShader,
    'core fragment': BLOB.fragmentShader,
    'mini vertex': EVOLVE_BLOB.vertexShader,
    'mini fragment': EVOLVE_BLOB.fragmentShader,
  }

  it.each(Object.entries(shaders))('never raise a possibly negative number to a power (%s)', (_, source) => {
    const unsafe = powBases(source).filter((base) => !SAFE.test(base))
    expect(unsafe).toEqual([])
  })

  it('clamp the facing ratio to at most 1 before using 1 - it as a pow() base', () => {
    expect(BLOB.fragmentShader).toMatch(/float ndv=clamp\(dot\(N,V\),0\.0,1\.0\);/)
  })
})
