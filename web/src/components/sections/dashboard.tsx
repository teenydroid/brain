import { ArrowUpRight } from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Container } from "@/components/section"
import { receiptCount, type Receipts } from "@/lib/receipts"
import { statLabel, type Stats } from "@/lib/stats"
import { COMPONENT_LABELS, REACH_LABELS, type World } from "@/lib/world"

const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 }).format

function SourceLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-xs text-muted-foreground hover:text-foreground">
      {children} <ArrowUpRight className="size-3" />
    </a>
  )
}

/** W and J, as frozen code computed them and the receipts published them. No file, no number. */
function WorldCard({ world }: { world: World }) {
  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle className="text-base">The world score W, and J</CardTitle>
        <CardDescription className="leading-relaxed">
          {world.source === "live"
            ? `As of ${world.date}, over ${world.days} days. J is a growth score computed by frozen code Teeny cannot edit; it is not money and says nothing about a price.`
            : world.source === "missing"
              ? "Not published yet: the world score appears here once the receipts carry it."
              : "The published world score could not be read from GitHub just now, so there are no numbers to show."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {world.source === "live" ? (
          <>
            <p className="font-mono text-4xl font-semibold tabular-nums">
              J {number(world.j)}
              {world.penalty !== undefined ? (
                <span className="ml-3 text-sm font-normal text-muted-foreground">{number(world.penalty)} side-effect event(s)</span>
              ) : null}
            </p>
            <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {world.components.map((c) => (
                <li key={c.name} className="rounded-xl border bg-background/60 p-3">
                  <p className="font-mono text-2xl font-semibold tabular-nums">{number(c.value)}</p>
                  <p className="text-sm text-muted-foreground">
                    {COMPONENT_LABELS[c.name]}
                    {c.unit ? `, ${c.unit}` : ""}
                  </p>
                  {c.was !== undefined ? <p className="font-mono text-xs text-muted-foreground">was {number(c.was)}</p> : null}
                </li>
              ))}
            </ul>
          </>
        ) : null}
        <SourceLink href={world.url}>world/latest.json</SourceLink>
      </CardContent>
    </Card>
  )
}

/** Who the attention came from, by a rule per surface: only humans are called people. */
function ReachCard({ world }: { world: World }) {
  if (world.source !== "live" || !world.reach.length) return null
  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle className="text-base">Who the attention came from</CardTitle>
        <CardDescription className="leading-relaxed">
          Accounts that wrote to Teeny in the {world.days} days before {world.date}, classed by a rule over each surface. What no rule can place is unknown, never counted as people.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {world.reach.map((r) => (
            <li key={r.kind} className="rounded-xl border bg-background/60 p-3">
              <p className="text-sm font-medium">{REACH_LABELS[r.kind]}</p>
              {r.accounts !== undefined ? <p className="font-mono text-sm">{number(r.accounts)} engaged account(s)</p> : null}
              {r.firstContactTheirs !== undefined ? <p className="font-mono text-sm">{number(r.firstContactTheirs)} wrote first</p> : null}
              {r.followers !== undefined ? <p className="font-mono text-sm">{number(r.followers)} followers or karma</p> : null}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

function StatsCard({ stats }: { stats: Stats }) {
  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle className="text-base">Followers, karma and stars</CardTitle>
        <CardDescription className="leading-relaxed">
          {stats.source === "live"
            ? `As X, Moltbook and GitHub reported them, as of ${stats.date}.`
            : stats.source === "missing"
              ? "Not published yet."
              : "The published snapshot could not be read from GitHub just now."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {stats.source === "live" ? (
          <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {stats.stats.map((stat) => (
              <li key={`${stat.source}.${stat.metric}`} className="rounded-xl border bg-background/60 p-3">
                <p className="font-mono text-2xl font-semibold tabular-nums">{number(stat.value)}</p>
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

function ReceiptsCard({ receipts }: { receipts: Receipts }) {
  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle className="text-base">Receipts</CardTitle>
        <CardDescription className="leading-relaxed">
          {receipts.source === "live"
            ? `${number(receipts.head)} receipt(s) in the hash-chained ledger: one per thing Teeny did in public, or paid for.`
            : "The receipts could not be read from GitHub just now, so there is no count."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {receipts.source === "live" && receipts.tally.length ? (
          <ul className="flex flex-wrap gap-2 font-mono text-sm">
            {receipts.tally.map((t) => (
              <li key={t.kind} className="rounded-full border px-3 py-1">{receiptCount(t.kind, t.count)}</li>
            ))}
          </ul>
        ) : null}
        <SourceLink href={receipts.url}>receipts/</SourceLink>
      </CardContent>
    </Card>
  )
}

/** A part of the dashboard whose ledger is not published yet: it says so, with no number. */
function NotPublished({ title, why }: { title: string; why: string }) {
  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription className="leading-relaxed">{why}</CardDescription>
      </CardHeader>
    </Card>
  )
}

/** Teeny in numbers, every one from the published ledger and nothing else. */
export function Dashboard({ world, stats, receipts }: { world: World; stats: Stats; receipts: Receipts }) {
  return (
    <section className="border-b">
      <Container>
        <p className="mb-2 font-mono text-xs uppercase tracking-widest text-primary">The dashboard</p>
        <h2 className="mb-8 text-2xl font-semibold tracking-tight sm:text-3xl">Only numbers the ledger holds.</h2>
        <div className="grid gap-6">
          <WorldCard world={world} />
          <ReachCard world={world} />
          <div className="grid gap-6 lg:grid-cols-2">
            <StatsCard stats={stats} />
            <ReceiptsCard receipts={receipts} />
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <NotPublished
              title="Generations and their verdicts"
              why="Not published yet. Each generation is a public pull request on the brain repository; a published list of generations and verdicts will appear here once the receipts carry it."
            />
            <NotPublished
              title="Costs against income"
              why="Not published yet. What the wallet paid is in the receipts; a weekly account of costs against income will appear here once it is published with them."
            />
          </div>
        </div>
      </Container>
    </section>
  )
}
