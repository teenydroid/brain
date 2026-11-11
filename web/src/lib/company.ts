/**
 * The brain: Teeny's mind as an OpenCompany company, read from its checkout
 * by the bot and served at /brain, and its public repository on GitHub.
 *
 * Nothing here is invented: when the bot cannot be reached the page says so
 * and shows an empty roster; the repository card still points at GitHub.
 */

import { readApi } from './api'

export interface BrainAgent {
  id: string
  role: string
  description: string
  tier?: string
}

export interface BrainNode {
  id: string
  kind: string
  name: string
  summary?: string
  agent?: string
  schedule?: string
}

export interface BrainEdge {
  from: string
  to: string
  label?: string
}

export interface BrainWorkflow {
  id: string
  name: string
  description?: string
  nodes: BrainNode[]
  edges: BrainEdge[]
}

export interface BrainRun {
  id: string
  agentId: string
  status: string
  workflowRunId?: string
  nodeId?: string
  createdAtMillis: number
  startedAtMillis?: number
  finishedAtMillis?: number
  usage?: { input?: number; output?: number; cachedInput?: number }
  stepCount?: number
  error?: string
}

export interface BrainStep {
  runId: string
  stepSeq: number
  atMillis: number
  step: { kind: string; status: string; label: string; detail?: string; result?: string }
}

export interface BrainSnapshot {
  /** `unavailable` when the bot could not be reached; the rest is then empty. */
  source: 'live' | 'unavailable'
  generatedAt: string
  company: { name: string; output?: string; slug: string }
  agents: BrainAgent[]
  workflows: BrainWorkflow[]
  runs: BrainRun[]
  steps: BrainStep[]
}

export const BRAIN_REPO = process.env.BRAIN_REPO ?? 'teenydroid/diary'
export const BRAIN_REPO_URL = `https://github.com/${BRAIN_REPO}`
export const OPENCOMPANY_URL = 'https://github.com/tinyhumansai/opencompany'

/**
 * The live snapshot is GET /brain on the bot. A bot that has a brain mounted
 * but no record yet (the host has not run) answers with the definition and
 * empty runs; the page keeps that as live and animates the workflow's own
 * shape instead of a run.
 */
export async function loadBrain(): Promise<BrainSnapshot> {
  const snapshot = await readApi<BrainSnapshot>('/brain', {
    check: (body) => Array.isArray((body as BrainSnapshot).agents) && Array.isArray((body as BrainSnapshot).workflows),
  })
  if (!snapshot) {
    return {
      source: 'unavailable',
      generatedAt: new Date().toISOString(),
      company: { name: 'Teeny', slug: 'teeny' },
      agents: [],
      workflows: [],
      runs: [],
      steps: [],
    }
  }
  return { ...snapshot, source: 'live' }
}

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
  /** True when GitHub answered; false when the numbers are placeholders. */
  live: boolean
}

const REPO_FALLBACK: RepoInfo = {
  name: BRAIN_REPO,
  url: BRAIN_REPO_URL,
  description: 'My brain, built entirely on OpenCompany',
  stars: 0,
  defaultBranch: 'main',
  commits: [],
  live: false,
}

/**
 * The repository as GitHub describes it, and its last few commits. Public
 * API, unauthenticated, revalidated every ten minutes: sixty calls an hour
 * is plenty for two calls per rebuild. `GITHUB_TOKEN`, if set, raises that.
 */
export async function loadRepo(): Promise<RepoInfo> {
  const headers: Record<string, string> = { accept: 'application/vnd.github+json' }
  if (process.env.GITHUB_TOKEN) headers.authorization = `Bearer ${process.env.GITHUB_TOKEN}`
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
