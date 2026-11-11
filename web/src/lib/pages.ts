export type SceneName = 'brain' | 'stream' | 'soul' | 'lattice' | 'orbit' | 'grid' | 'shards'

export interface PageMeta {
  slug: string
  label: string
  eyebrow: string
  title: string
  lede: string
  scene: SceneName
}

export const PAGES: PageMeta[] = [
  {
    slug: '/ledger',
    label: 'Ledger',
    eyebrow: 'Ledger',
    title: 'Every inference, on the record',
    lede: 'One row per model call: what went in, what it reasoned, what came out, and what it cost. The system prompt is never shown; each row carries its digest instead, so the record commits to it without revealing it.',
    scene: 'stream',
  },
  {
    slug: '/brain',
    label: 'Brain',
    eyebrow: 'Brain',
    title: 'The part that decides what it is trying to do',
    lede: 'A stock OpenHuman core, a handful of small programs that hold every key, and a brain made of plain files: who it is, what it remembers, its playbooks and its schedule. All of it is public, and so are the receipts for everything it does.',
    scene: 'orbit',
  },
  {
    slug: '/soul',
    label: 'Soul',
    eyebrow: 'Soul',
    title: 'Who it is, in its own file',
    lede: 'The persona lives in SOUL.md, in the repo, where anyone can read the exact words the model is given. This is the short version.',
    scene: 'soul',
  },
  {
    slug: '/rules',
    label: 'Rules',
    eyebrow: 'Rules',
    title: 'What no message can change',
    lede: 'A rule written in a prompt is a request. A rule enforced by the tool set or a test is a fact. Each rule here says which it is.',
    scene: 'lattice',
  },
  {
    slug: '/tools',
    label: 'Tools',
    eyebrow: 'Tools',
    title: 'Everything it can do. And what it cannot.',
    lede: 'This is the whole list. A test asserts it exactly, so adding a capability is a deliberate act that fails the suite until someone decides.',
    scene: 'grid',
  },
  {
    slug: '/articles',
    label: 'Articles',
    eyebrow: 'Articles',
    title: 'The long ones, twice a week',
    lede: 'A room of tiny agents is opened on something from the wire and read down to one thought. The thought is planned, written a section at a time, proof-read against a checklist of the ways machines write, drawn, and published as an article on X. Every one of them is here in full.',
    scene: 'shards',
  },
]

export function pageBySlug(slug: string): PageMeta {
  const page = PAGES.find((p) => p.slug === slug)
  if (!page) throw new Error(`no page registered for ${slug}`)
  return page
}

export function neighbours(slug: string): { prev?: PageMeta; next?: PageMeta } {
  const i = PAGES.findIndex((p) => p.slug === slug)
  return { prev: PAGES[i - 1], next: PAGES[i + 1] }
}
