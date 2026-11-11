/**
 * Where the page reads the bot from. `MIND_API_URL` is the bot's base over
 * the compose network; it serves the journal at /mind and the inference ledger
 * at /ledger. The older `MIND_FEED_URL` pointed
 * straight at /mind and is still honoured, with the path taken off.
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
