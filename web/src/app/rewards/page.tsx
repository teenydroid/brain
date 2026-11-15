import type { Metadata } from "next"
import { RewardsView } from "@/components/rewards/rewards-view"
import { loadDrops } from "@/lib/drops"

// The rounds are read from the diary at render time; the page is rebuilt at
// most once a minute and each round file keeps its own ten-minute cache. What
// the chain says (claimed or not, what is left in a vault) is read in the
// browser, never here.
export const revalidate = 60

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

/** The rounds, and the moment they were read: which rounds still pay out is decided against it. */
async function load() {
  const drops = await loadDrops()
  return { drops, now: Date.now() }
}

export default async function Rewards() {
  const { drops, now } = await load()
  return <RewardsView drops={drops} now={now} />
}
