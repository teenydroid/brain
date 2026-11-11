/**
 * The brain: Teeny as it runs today, read from its public repository
 * (teenydroid/brain) on GitHub.
 *
 * Teeny is a stock OpenHuman core (spine/, vendor/openhuman), small programs
 * that hold every outside capability (hands/), and the files the core reads
 * (brain/: SOUL.md, CONTEXT.md, MEMORY.md, narrative/, agents/, playbooks/
 * and cron.toml). The web image is built from web/ alone, so those files
 * are not on disk here: the page lists them from GitHub, revalidated hourly.
 * The receipts (teenydroid/diary) are read in receipts.ts.
 *
 * Nothing here is invented: when GitHub cannot be reached the page says so
 * and lists nothing; the links still point at the repositories.
 */

import { BRANCH, githubHeaders, listTree, raw as rawFile } from './github'

export const BRAIN_REPO = process.env.BRAIN_REPO ?? 'teenydroid/brain'
export const BRAIN_REPO_URL = `https://github.com/${BRAIN_REPO}`
export const OPENHUMAN_URL = 'https://github.com/tinyhumansai/openhuman'

const REVALIDATE = 3600

function raw(repo: string, path: string): Promise<string | undefined> {
  return rawFile(repo, path, REVALIDATE)
}

/* ------------------------------------------------------------------------ */
/* A small TOML reader: enough for brain/agents/*.toml, brain/cron.toml and  */
/* a playbook's front matter. Top-level and [table] string, number and       */
/* boolean keys; array tables, inline tables and multi-line strings are      */
/* skipped. Comment lines right above a [table] are kept as its note.        */
/* ------------------------------------------------------------------------ */

type TomlValue = string | number | boolean
interface TomlTable {
  values: Record<string, TomlValue>
  note: string
}
export interface TomlDoc {
  root: Record<string, TomlValue>
  tables: Record<string, TomlTable>
}

function parseValue(text: string): TomlValue | undefined {
  const value = text.trim()
  const quoted = /^"((?:[^"\\]|\\.)*)"/.exec(value) ?? /^'([^']*)'/.exec(value)
  if (quoted) return quoted[1].replace(/\\"/g, '"').replace(/\\\\/g, '\\')
  if (value.startsWith('true')) return true
  if (value.startsWith('false')) return false
  const number = /^-?\d+(\.\d+)?/.exec(value)
  if (number) return Number(number[0])
  return undefined
}

export function parseToml(source: string): TomlDoc {
  const doc: TomlDoc = { root: {}, tables: {} }
  let current: Record<string, TomlValue> | undefined = doc.root
  let comments: string[] = []
  let inMultiline = false
  for (const line of source.split('\n')) {
    const trimmed = line.trim()
    if (inMultiline) {
      if (trimmed.includes('"""')) inMultiline = false
      continue
    }
    if (!trimmed) {
      comments = []
      continue
    }
    if (trimmed.startsWith('#')) {
      comments.push(trimmed.replace(/^#+\s?/, ''))
      continue
    }
    if (trimmed.startsWith('[[')) {
      current = undefined
      comments = []
      continue
    }
    const header = /^\[([^\]]+)\]/.exec(trimmed)
    if (header) {
      const table: TomlTable = { values: {}, note: comments.join(' ') }
      doc.tables[header[1].trim()] = table
      current = table.values
      comments = []
      continue
    }
    comments = []
    const pair = /^([A-Za-z0-9_.-]+)\s*=\s*(.*)$/.exec(trimmed)
    if (!pair) continue
    if (pair[2].startsWith('"""') && !pair[2].slice(3).includes('"""')) {
      inMultiline = true
      continue
    }
    const value = parseValue(pair[2])
    if (current && value !== undefined) current[pair[1]] = value
  }
  return doc
}

/** The `+++` front matter at the top of a playbook. */
function frontMatter(source: string): TomlDoc | undefined {
  const match = /^\+\+\+\n([\s\S]*?)\n\+\+\+/.exec(source)
  return match ? parseToml(match[1]) : undefined
}

function str(value: TomlValue | undefined): string | undefined {
  return typeof value === 'string' && value ? value : undefined
}

/** The first sentence of a comment, without its file references. */
function firstSentence(note: string): string {
  const plain = note
    .replace(/\s*\([^)]*\)/g, '')
    .replace(/`/g, '')
    .replace(/\s+/g, ' ')
    .trim()
  const end = plain.search(/\.(\s|$)/)
  return end >= 0 ? plain.slice(0, end + 1) : plain
}

/* ------------------------------------------------------------------------ */

export interface BrainAgent {
  id: string
  name: string
  purpose?: string
  sandbox?: string
  /** True when OpenHuman lets its cron runs act without a person. */
  scheduledActions: boolean
}

export interface BrainPlaybook {
  slug: string
  description?: string
  output?: string
  surface?: string
  maxPerDay?: number
}

export interface BrainJob {
  name: string
  schedule: string
  tz?: string
  /** What it runs: a playbook, a script under bin/, or an agent. */
  runs: { kind: 'playbook' | 'script' | 'agent'; target: string }
  summary?: string
  enabled: boolean
  /** Merged in from evals/cron.pinned.toml; the brain cannot change it. */
  pinned: boolean
}

export interface BrainHand {
  name: string
  summary?: string
}

export interface BrainAnatomy {
  /** `unavailable` when GitHub could not be reached; the lists are then empty. */
  source: 'live' | 'unavailable'
  agents: BrainAgent[]
  playbooks: BrainPlaybook[]
  jobs: BrainJob[]
  hands: BrainHand[]
}

const EMPTY: BrainAnatomy = { source: 'unavailable', agents: [], playbooks: [], jobs: [], hands: [] }

async function listPaths(): Promise<string[] | undefined> {
  const listing = await listTree(BRAIN_REPO, '', REVALIDATE)
  return listing.status === 'ok' ? listing.paths : undefined
}

function jobsFrom(doc: TomlDoc | undefined, pinned: boolean, playbooks: Map<string, BrainPlaybook>): BrainJob[] {
  if (!doc) return []
  const jobs: BrainJob[] = []
  const defaultTz = str(doc.root.tz)
  for (const [key, table] of Object.entries(doc.tables)) {
    if (!key.startsWith('jobs.')) continue
    const { values } = table
    const schedule = str(values.schedule)
    if (!schedule) continue
    const command = str(values.command)
    const agent = str(values.agent)
    const playbook = command ? /^bin\/act\s+(\S+)/.exec(command)?.[1] : undefined
    const runs: BrainJob['runs'] = playbook
      ? { kind: 'playbook', target: playbook }
      : agent
        ? { kind: 'agent', target: agent }
        : { kind: 'script', target: command?.split(/\s+/)[0] ?? '' }
    const summary = (playbook ? playbooks.get(playbook)?.description : undefined) ?? (table.note ? firstSentence(table.note) : undefined)
    const tz = str(values.tz) ?? defaultTz
    jobs.push({
      name: key.slice('jobs.'.length),
      schedule,
      ...(tz ? { tz } : {}),
      runs,
      ...(summary ? { summary } : {}),
      enabled: values.enabled !== false,
      pinned,
    })
  }
  return jobs
}

/**
 * What the brain is made of, from the repository's own files: the agents
 * (brain/agents/*.toml), the playbooks (brain/playbooks/*.md front matter),
 * the scheduled jobs (brain/cron.toml plus the pinned evals/cron.pinned.toml,
 * which bin/seed-cron merges at every boot) and the hands (hands/*, each
 * described by its own docstring).
 */
export async function loadAnatomy(): Promise<BrainAnatomy> {
  const paths = await listPaths()
  if (!paths) return EMPTY
  const under = (dir: string, ext: string) =>
    paths.filter((p) => p.startsWith(dir) && p.endsWith(ext) && !p.slice(dir.length).includes('/'))
  const handPaths = paths.filter((p) => /^hands\/[^/]+$/.test(p) && !paths.some((q) => q.startsWith(`${p}/`)))

  const [agentFiles, playbookFiles, handFiles, cron, pinned] = await Promise.all([
    Promise.all(under('brain/agents/', '.toml').map((p) => raw(BRAIN_REPO, p))),
    Promise.all(under('brain/playbooks/', '.md').map(async (p) => [p, await raw(BRAIN_REPO, p)] as const)),
    Promise.all(handPaths.map(async (p) => [p, await raw(BRAIN_REPO, p)] as const)),
    raw(BRAIN_REPO, 'brain/cron.toml'),
    raw(BRAIN_REPO, 'evals/cron.pinned.toml'),
  ])

  const agents: BrainAgent[] = agentFiles.flatMap((file) => {
    if (!file) return []
    const { root } = parseToml(file)
    const id = str(root.id)
    if (!id) return []
    const purpose = str(root.when_to_use)?.replace(/\s*Not a chat delegate\.?$/, '')
    const sandbox = str(root.sandbox_mode)
    return [{ id, name: str(root.display_name) ?? id, ...(purpose ? { purpose } : {}), ...(sandbox ? { sandbox } : {}), scheduledActions: root.scheduled_actions === true }]
  })

  const playbooks: BrainPlaybook[] = playbookFiles.flatMap(([path, file]) => {
    if (!file) return []
    const root = frontMatter(file)?.root ?? {}
    const description = str(root.description)
    const output = str(root.output)
    const surface = str(root.surface)
    return [{
      slug: path.slice('brain/playbooks/'.length, -'.md'.length),
      ...(description ? { description } : {}),
      ...(output ? { output } : {}),
      ...(surface ? { surface } : {}),
      ...(typeof root.max_per_day === 'number' ? { maxPerDay: root.max_per_day } : {}),
    }]
  })

  const hands: BrainHand[] = handFiles.map(([path, file]) => {
    const name = path.slice('hands/'.length)
    const doc = file ? /"""\s*hands\/[^:]+:\s*([^"]*?)\s*(\(docs\/[^)]*\))?\.?\s*"""/.exec(file)?.[1] : undefined
    return { name, ...(doc ? { summary: doc } : {}) }
  })

  const bySlug = new Map(playbooks.map((p) => [p.slug, p]))
  const jobs = [
    ...jobsFrom(cron ? parseToml(cron) : undefined, false, bySlug),
    ...jobsFrom(pinned ? parseToml(pinned) : undefined, true, bySlug),
  ]

  return { source: 'live', agents, playbooks, jobs, hands }
}

/** A cron expression in words, for the common shapes; anything else stays as written. */
export function describeSchedule(schedule: string, tz?: string): string {
  const zone = tz === 'Asia/Kolkata' ? 'IST' : tz ?? 'UTC'
  const [min, hour, dom, mon, dow] = schedule.trim().split(/\s+/)
  if (dom !== '*' || mon !== '*') return schedule
  const days = dow === '*' ? 'daily' : dow === '0' ? 'Sundays' : dow === '1,3,5' ? 'Mon, Wed, Fri' : undefined
  const every = /^(?:\d+-\d+)?\*?\/(\d+)$/.exec(min ?? '')
  if (every && hour === '*' && dow === '*') return `every ${every[1]} min`
  if (/^\d+$/.test(min ?? '') && hour === '*' && dow === '*') return `hourly at :${min!.padStart(2, '0')}`
  const everyHours = /^\*\/(\d+)$/.exec(hour ?? '')
  if (/^\d+$/.test(min ?? '') && everyHours && dow === '*') return `every ${everyHours[1]} h at :${min!.padStart(2, '0')}`
  if (/^\d+$/.test(min ?? '') && /^\d+$/.test(hour ?? '') && days)
    return `${days} ${hour!.padStart(2, '0')}:${min!.padStart(2, '0')} ${zone}`
  return schedule
}

/* ------------------------------------------------------------------------ */

export interface RepoCommit {
  sha: string
  message: string
  author: string
  at: string
  url: string
}

export interface RepoInfo {
  name: string
  url: string
  description?: string
  stars: number
  pushedAt?: string
  defaultBranch: string
  commits: RepoCommit[]
  /** True when GitHub answered; false when only the link is known. */
  live: boolean
}

const REPO_FALLBACK: RepoInfo = {
  name: BRAIN_REPO,
  url: BRAIN_REPO_URL,
  stars: 0,
  defaultBranch: BRANCH,
  commits: [],
  live: false,
}

/**
 * The repository as GitHub describes it, and its last few commits. Public
 * API, unauthenticated, revalidated every ten minutes. `GITHUB_TOKEN`, if
 * set, raises the rate limit.
 */
export async function loadRepo(): Promise<RepoInfo> {
  const headers = githubHeaders()
  try {
    const [repoRes, commitsRes] = await Promise.all([
      fetch(`https://api.github.com/repos/${BRAIN_REPO}`, { headers, next: { revalidate: 600 }, signal: AbortSignal.timeout(6_000) }),
      fetch(`https://api.github.com/repos/${BRAIN_REPO}/commits?per_page=8`, { headers, next: { revalidate: 600 }, signal: AbortSignal.timeout(6_000) }),
    ])
    if (!repoRes.ok) return REPO_FALLBACK
    const repo = (await repoRes.json()) as {
      full_name: string
      html_url: string
      description?: string
      stargazers_count: number
      pushed_at?: string
      default_branch: string
    }
    const commits = commitsRes.ok
      ? ((await commitsRes.json()) as Array<{
          sha: string
          html_url: string
          commit: { message: string; author?: { name?: string; date?: string } }
          author?: { login?: string }
        }>)
      : []
    return {
      name: repo.full_name,
      url: repo.html_url,
      ...(repo.description ? { description: repo.description } : {}),
      stars: repo.stargazers_count,
      ...(repo.pushed_at ? { pushedAt: repo.pushed_at } : {}),
      defaultBranch: repo.default_branch,
      commits: commits.map((c) => ({
        sha: c.sha,
        message: c.commit.message.split('\n')[0] ?? '',
        author: c.author?.login ?? c.commit.author?.name ?? 'unknown',
        at: c.commit.author?.date ?? '',
        url: c.html_url,
      })),
      live: true,
    }
  } catch {
    return REPO_FALLBACK
  }
}
