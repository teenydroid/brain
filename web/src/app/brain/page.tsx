import type { Metadata } from "next"
import { PageHero } from "@/components/page-hero"
import { PageNav } from "@/components/page-nav"
import { BrainSection } from "@/components/sections/brain"
import { loadAnatomy, loadDiary, loadRepo } from "@/lib/brain-repo"
import { pageBySlug } from "@/lib/pages"

// The brain's files are read from GitHub at render time; the page is rebuilt at most once a minute
// and the fetches themselves are cached for up to an hour.
export const revalidate = 60

const page = pageBySlug("/brain")

export const metadata: Metadata = { title: `${page.label} · Teeny's Mind`, description: page.lede }

export default async function Page() {
  const [anatomy, repo, diary] = await Promise.all([loadAnatomy(), loadRepo(), loadDiary()])
  return (
    <>
      <PageHero page={page} />
      <BrainSection anatomy={anatomy} repo={repo} diary={diary} />
      <PageNav slug={page.slug} />
    </>
  )
}
