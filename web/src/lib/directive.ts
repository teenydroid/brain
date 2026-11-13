/**
 * The race, as DIRECTIVE.md in the brain repository keeps score: each level
 * on the way to "alive", with how many of its items are proven live. An item
 * is ticked there only when its proof exists on the box, so the page never
 * claims more than that file does. Unreadable, there is no race to show.
 */

import { BRAIN_REPO } from './brain-repo'
import { raw } from './github'

export interface Level {
  /** 0 for "wired up", up to "the human leaves". */
  n: number
  title: string
  /** The line under the heading: what the level means. */
  tagline: string
  done: number
  total: number
}

export type Directive =
  | { source: 'live'; url: string; levels: Level[] }
  | { source: 'unavailable'; url: string }

export const DIRECTIVE_URL = `https://github.com/${BRAIN_REPO}/blob/main/DIRECTIVE.md`

const LEVEL = /^## Level (\d+): (.+)$/

/** Every "## Level N: title" section, its first line of prose and its ticked and unticked items. */
export function parseDirective(text: string): Level[] {
  const levels: Level[] = []
  let current: Level | undefined
  for (const line of text.split('\n')) {
    const heading = line.match(LEVEL)
    if (heading) {
      current = { n: Number(heading[1]), title: heading[2].trim(), tagline: '', done: 0, total: 0 }
      levels.push(current)
      continue
    }
    if (line.startsWith('## ')) {
      current = undefined
      continue
    }
    if (!current) continue
    if (/^- \[x\]/i.test(line)) {
      current.done++
      current.total++
    } else if (/^- \[ \]/.test(line)) {
      current.total++
    } else if (!current.tagline && line.trim() && !line.startsWith('-') && !line.startsWith('#')) {
      current.tagline = line.trim().replace(/\s*\(`[^`]*`\)/g, '').replace(/`/g, '')
    }
  }
  return levels.filter((level) => level.total > 0)
}

export async function loadDirective(): Promise<Directive> {
  const text = await raw(BRAIN_REPO, 'DIRECTIVE.md', 600)
  const levels = text ? parseDirective(text) : []
  return levels.length ? { source: 'live', url: DIRECTIVE_URL, levels } : { source: 'unavailable', url: DIRECTIVE_URL }
}
