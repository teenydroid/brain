/**
 * Reads from public GitHub repositories, server-side and cached (ISR). Every
 * read fails soft: the caller gets a status it can tell apart from content,
 * so "not there" and "GitHub did not answer" never look the same.
 */

export const BRANCH = 'main'

export function githubHeaders(): Record<string, string> {
  const headers: Record<string, string> = { accept: 'application/vnd.github+json' }
  if (process.env.GITHUB_TOKEN) headers.authorization = `Bearer ${process.env.GITHUB_TOKEN}`
  return headers
}

/** A file's text, `missing` on a 404, `unavailable` on anything else (an error, a timeout, a 5xx, a rate limit). */
export type RawRead = { status: 'ok'; text: string } | { status: 'missing' } | { status: 'unavailable' }

export async function readRaw(repo: string, path: string, revalidate: number): Promise<RawRead> {
  try {
    const response = await fetch(`https://raw.githubusercontent.com/${repo}/${BRANCH}/${path}`, {
      next: { revalidate },
      signal: AbortSignal.timeout(6_000),
    })
    if (response.status === 404) return { status: 'missing' }
    return response.ok ? { status: 'ok', text: await response.text() } : { status: 'unavailable' }
  } catch {
    return { status: 'unavailable' }
  }
}

/** One file from a public repository, or undefined on anything but a 200. */
export async function raw(repo: string, path: string, revalidate: number): Promise<string | undefined> {
  const read = await readRaw(repo, path, revalidate)
  return read.status === 'ok' ? read.text : undefined
}

/**
 * The paths under `dir` on the branch (relative to it), through the git trees
 * API: `missing` when the directory does not exist, `unavailable` when GitHub
 * did not answer.
 */
export async function listTree(
  repo: string,
  dir: string,
  revalidate: number,
): Promise<{ status: 'ok'; paths: string[] } | { status: 'missing' } | { status: 'unavailable' }> {
  const treeish = dir ? `${BRANCH}:${dir}` : BRANCH
  try {
    const response = await fetch(`https://api.github.com/repos/${repo}/git/trees/${encodeURIComponent(treeish)}?recursive=1`, {
      headers: githubHeaders(),
      next: { revalidate },
      signal: AbortSignal.timeout(8_000),
    })
    if (response.status === 404) return { status: 'missing' }
    if (!response.ok) return { status: 'unavailable' }
    const body = (await response.json()) as { tree?: Array<{ path: string }> }
    return body.tree ? { status: 'ok', paths: body.tree.map((entry) => entry.path) } : { status: 'unavailable' }
  } catch {
    return { status: 'unavailable' }
  }
}
