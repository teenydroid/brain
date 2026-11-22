/**
 * Where the page reads the journal and the inference ledger from.
 * `MIND_API_URL` is the base of an API that serves them at /mind and /ledger
 * (Teeny's previous runtime did; the current spine serves neither, so it is
 * normally unset and the counter and journal are left out). The older
 * `MIND_FEED_URL` pointed straight at /mind and is still honoured, with the
 * path taken off.
 */
export function apiUrl(path: string): string | undefined {
  const base = process.env.MIND_API_URL ?? process.env.MIND_FEED_URL?.replace(/\/mind\/?$/, '')
  if (!base) return undefined
  return `${base.replace(/\/$/, '')}${path}`
}

/**
 * One fetch against the bot: a short timeout and no throwing, because
 * anything short of a good answer reads as unavailable, which the page
 * says plainly, so a bot that is down never takes the page with it.
 */
export async function readApi<T>(
  path: string,
  options: { revalidate?: number; check?: (body: unknown) => boolean } = {},
): Promise<T | undefined> {
  const url = apiUrl(path)
  if (!url) return undefined
  try {
    const response = await fetch(url, {
      ...(options.revalidate === 0
        ? { cache: 'no-store' as const }
        : { next: { revalidate: options.revalidate ?? 60 } }),
      signal: AbortSignal.timeout(5_000),
    })
    if (!response.ok) return undefined
    const body = (await response.json()) as unknown
    if (options.check && !options.check(body)) return undefined
    return body as T
  } catch {
    return undefined
  }
}
