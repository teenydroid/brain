import type { Metadata } from "next"
import { PageHero } from "@/components/page-hero"
import { PageNav } from "@/components/page-nav"
import { Dashboard } from "@/components/sections/dashboard"
import { pageBySlug } from "@/lib/pages"
import { loadReceipts } from "@/lib/receipts"
import { loadStats } from "@/lib/stats"
import { loadWorld } from "@/lib/world"

// Every number is read from the published diary repository; the page is rebuilt at most once a minute
// and the fetches themselves are cached for ten minutes.
export const revalidate = 60

const page = pageBySlug("/dashboard")

export const metadata: Metadata = { title: `${page.label} · Teeny's Mind`, description: page.lede }

export default async function Page() {
  const [world, stats, receipts] = await Promise.all([loadWorld(), loadStats(), loadReceipts()])
  return (
    <>
      <PageHero page={page} />
      <Dashboard world={world} stats={stats} receipts={receipts} />
      <PageNav slug={page.slug} />
    </>
  )
}
