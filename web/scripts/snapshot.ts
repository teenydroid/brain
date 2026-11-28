/**
 * Writes the site's JSON snapshots (src/lib/snapshot.ts) into a directory:
 *
 *   npm run snapshot -- <out-dir>     writes <out-dir>/home.json and drops.json
 *
 * teenydroid/teenydroid.github.io's build workflow runs it every fifteen
 * minutes and commits the result to that repository's data/. GITHUB_TOKEN, if set, lifts the
 * GitHub API's rate limit for the loaders.
 */

import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { loadDropsSnapshot, loadHomeSnapshot } from '../src/lib/load-snapshot'

async function writeSnapshots(dir: string): Promise<string[]> {
  await mkdir(dir, { recursive: true })
  const [home, drops] = await Promise.all([loadHomeSnapshot(), loadDropsSnapshot()])
  const written: string[] = []
  for (const [name, body] of [['home', home], ['drops', drops]] as const) {
    const file = path.join(dir, `${name}.json`)
    await writeFile(file, `${JSON.stringify(body, null, 2)}\n`)
    written.push(file)
  }
  return written
}

const dir = process.argv[2]
if (!dir) {
  console.error('usage: npm run snapshot -- <out-dir>')
  process.exit(2)
}
writeSnapshots(path.resolve(dir)).then(
  (files) => console.log(files.join('\n')),
  (err: unknown) => {
    console.error(err)
    process.exit(1)
  },
)
