import type { Metadata } from "next"
import { RewardsLive } from "@/components/rewards/rewards-live"
import { loadDropsSnapshot } from "@/lib/load-snapshot"

// The rounds are read from the diary once at build time (a static export on
// GitHub Pages), then refreshed in the browser from the published snapshot
// (src/lib/snapshot.ts). What the chain says (claimed or not, what is left in
// a vault) is read in the browser, never here.

const TITLE = "teeny · tiny drops"
const DESCRIPTION =
  "teeny's public $TINY prize rounds: reply to the round post to enter, a seeded draw anyone can re-run picks the winners, and winners claim on chain. free to enter."

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/rewards" },
  openGraph: { title: TITLE, description: DESCRIPTION, url: "/rewards" },
  twitter: { title: TITLE, description: DESCRIPTION },
}

export default async function Rewards() {
  return <RewardsLive initial={await loadDropsSnapshot()} />
}
