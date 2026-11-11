import type { Metadata } from "next"
import { PageHero } from "@/components/page-hero"
import { PageNav } from "@/components/page-nav"
import { BrainSection } from "@/components/sections/brain"
import { loadAnatomy, loadRepo } from "@/lib/brain-repo"
import { loadReceipts } from "@/lib/receipts"
import { pageBySlug } from "@/lib/pages"

// The brain's files are read from GitHub at render time; the page is rebuilt at most once a minute
// and the fetches themselves are cached for up to an hour.
export const revalidate = 60

const page = pageBySlug("/brain")

export const metadata: Metadata = { title: `${page.label} · Teeny's Mind`, description: page.lede }

export default async function Page() {
  const [anatomy, repo, receipts] = await Promise.all([loadAnatomy(), loadRepo(), loadReceipts()])
  return (
    <>
      <PageHero page={page} />
      <BrainSection anatomy={anatomy} repo={repo} receipts={receipts} />
      <PageNav slug={page.slug} />
    </>
  )
}
