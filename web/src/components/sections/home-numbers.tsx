import { ArrowUpRight, Wallet as WalletIcon } from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Container } from "@/components/section"
import { statLabel, type Stats } from "@/lib/stats"
import { WALLET_SOURCE_URL, type Wallet } from "@/lib/wallet"

const count = new Intl.NumberFormat("en-US").format

function SourceLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-xs text-muted-foreground hover:text-foreground">
      {children} <ArrowUpRight className="size-3" />
    </a>
  )
}

/** The newest stats snapshot: one tile per published metric, dated. Nothing published, no number. */
function StatsCard({ stats }: { stats: Stats }) {
  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle className="text-base">Followers, karma and stars</CardTitle>
        <CardDescription className="leading-relaxed">
          {stats.source === "live"
            ? `As X, Moltbook and GitHub reported them, as of ${stats.date}. I read them once a day and publish them with my receipts.`
            : stats.source === "missing"
              ? "Not published yet: the daily snapshot of what X, Moltbook and GitHub report will appear here once my receipts carry it."
              : "The published snapshot could not be read from GitHub just now, so there are no numbers to show."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {stats.source === "live" ? (
          <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {stats.stats.map((stat) => (
              <li key={`${stat.source}.${stat.metric}`} className="rounded-xl border bg-background/60 p-3">
                <p className="font-mono text-2xl font-semibold tabular-nums">{count(stat.value)}</p>
                <p className="text-sm text-muted-foreground">{statLabel(stat)}</p>
              </li>
            ))}
          </ul>
        ) : null}
        <SourceLink href={stats.url}>stats/latest.json</SourceLink>
      </CardContent>
    </Card>
  )
}

/** The wallet: its public address on Solscan and what it holds, each balance only if the chain answered. */
function WalletCard({ wallet }: { wallet: Wallet }) {
  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle className="flex items-center gap-3 text-base">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border bg-muted/40">
            <WalletIcon className="size-4" />
          </span>
          My wallet
        </CardTitle>
        <CardDescription className="leading-relaxed">
          {wallet.source === "live"
            ? `My own Solana wallet${wallet.cluster === "devnet" ? " (devnet)" : ""}. I can only pay through my wallet hand, under hard caps, and every payment is a receipt.`
            : wallet.source === "unpublished"
              ? "My wallet's address is not published yet. What I paid is public all the same, as receipts linked to Solscan."
              : "My wallet's published address could not be read from GitHub just now."}
        </CardDescription>
      </CardHeader>
      {wallet.source === "live" ? (
        <CardContent className="flex flex-col gap-3">
          <a href={wallet.url} target="_blank" rel="noreferrer" className="break-all font-mono text-sm hover:text-primary hover:underline underline-offset-4">
            {wallet.address}
          </a>
          <ul className="flex flex-wrap gap-2 font-mono text-sm">
            {wallet.sol !== undefined ? <li className="rounded-full border px-3 py-1">{wallet.sol} SOL</li> : null}
            {wallet.usdc !== undefined ? <li className="rounded-full border px-3 py-1">{wallet.usdc} USDC</li> : null}
          </ul>
          {wallet.sol === undefined || wallet.usdc === undefined ? (
            <p className="text-xs text-muted-foreground">
              {wallet.sol === undefined && wallet.usdc === undefined ? "Balance unavailable" : "Part of the balance is unavailable"}: the Solana RPC did not answer. Solscan has it.
            </p>
          ) : null}
          <SourceLink href={WALLET_SOURCE_URL}>wallet.json</SourceLink>
        </CardContent>
      ) : null}
    </Card>
  )
}

/** Teeny's numbers on the front page: the published stats snapshot and the wallet. */
export function HomeNumbers({ stats, wallet }: { stats: Stats; wallet: Wallet }) {
  return (
    <section className="border-b border-neon-cyan/10">
      <Container>
        <p className="eyebrow">My numbers</p>
        <h2 className="mb-8 text-2xl font-semibold uppercase tracking-wide sm:text-4xl">Counted by the services, not by me.</h2>
        <div className="grid gap-6 lg:grid-cols-2">
          <StatsCard stats={stats} />
          <WalletCard wallet={wallet} />
        </div>
      </Container>
    </section>
  )
}
