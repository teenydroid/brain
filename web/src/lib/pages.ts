export type SceneName = 'core' | 'brain' | 'orbit' | 'grid'

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
    slug: '/brain',
    label: 'Brain',
    eyebrow: 'Brain',
    title: 'The part that decides what it is trying to do',
    lede: 'A stock OpenHuman core, a handful of small programs that hold every key, and a brain made of plain files: who it is, what it remembers, its playbooks and its schedule. All of it is public, and so are the receipts for everything it does.',
    scene: 'orbit',
  },
  {
    slug: '/tools',
    label: 'Tools',
    eyebrow: 'Tools',
    title: 'Everything it can do. And what it cannot.',
    lede: 'This is the whole list. A test asserts it exactly, so adding a capability is a deliberate act that fails the suite until someone decides.',
    scene: 'grid',
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
