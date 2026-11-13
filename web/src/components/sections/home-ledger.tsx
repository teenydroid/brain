import Link from "next/link"
import { ArrowRight } from "lucide-react"
import { InferenceRow, LedgerEmpty } from "@/components/sections/ledger"
import { Button } from "@/components/ui/button"
import { Container } from "@/components/section"
import type { LedgerFeed } from "@/lib/ledger"

/** The last few rows of the ledger, on the front page, so the count has a face. */
export function HomeLedger({ ledger }: { ledger: LedgerFeed }) {
  const latest = ledger.entries.slice(0, 4)
  return (
    <section className="border-b">
      <Container>
        <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="eyebrow">The ledger</p>
            <h2 className="text-2xl font-semibold uppercase tracking-wide sm:text-4xl">The last few calls, as they ran.</h2>
            <p className="mt-2 max-w-2xl text-muted-foreground">
              Input, thinking, output, cost. The system prompt is the one thing withheld; each row carries its
              digest so the record commits to it without showing it.
            </p>
          </div>
          <Button variant="outline" render={<Link href="/ledger" />}>
            The whole ledger
            <ArrowRight className="size-4" />
          </Button>
        </div>
        {latest.length ? (
          <div className="flex flex-col gap-3">
            {latest.map((entry, index) => (
              <InferenceRow key={entry.id} entry={entry} open={index === 0} />
            ))}
          </div>
        ) : (
          <LedgerEmpty source={ledger.source} />
        )}
      </Container>
    </section>
  )
}
