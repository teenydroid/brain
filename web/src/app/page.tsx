import { HomeLive } from "@/components/home-live"
import { loadHomeSnapshot } from "@/lib/load-snapshot"

// Read once at build time (the page is a static export on GitHub Pages); the
// browser then refreshes it from the published snapshot (src/lib/snapshot.ts).
export default async function Home() {
  return <HomeLive initial={await loadHomeSnapshot()} />
}
