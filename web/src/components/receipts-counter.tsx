import { receiptCount, receiptLabel, receiptTime, type Receipts } from "@/lib/receipts"
import { cn } from "@/lib/utils"

const count = new Intl.NumberFormat("en-US").format

/**
 * The figure on the front page: how many things Teeny has done that left a
 * receipt in teenydroid/diary (the chain head's seq), the newest one, and the
 * count by kind. Server-rendered from the published chain, revalidated with
 * the page. When the chain cannot be read there is no figure at all.
 */
export function ReceiptsCounter({ receipts }: { receipts: Receipts }) {
  const live = receipts.source === "live"
  const newest = receipts.latest[0]
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-2">
        <p className="flex items-center gap-2 font-mono text-xs uppercase tracking-widest text-muted-foreground">
          <span className={cn("relative inline-flex size-2 rounded-full", live ? "bg-primary" : "bg-destructive")} />
          things i have done, each with a receipt
        </p>
        {!live ? (
          <p className="max-w-xl text-sm text-muted-foreground">
            My receipts could not be read from GitHub just now, so there is no count to show. They are all in{" "}
            <a href={receipts.url} target="_blank" rel="noreferrer" className="underline underline-offset-4 hover:text-foreground">
              the receipts repository
            </a>
            .
          </p>
        ) : !receipts.head ? (
          <p className="max-w-xl text-sm text-muted-foreground">
            No receipts yet: the first post, reply or payment I send will be the first line in{" "}
            <a href={receipts.url} target="_blank" rel="noreferrer" className="underline underline-offset-4 hover:text-foreground">
              the receipts repository
            </a>
            .
          </p>
        ) : (
          <>
            <span className="font-mono text-6xl font-semibold tabular-nums tracking-tight sm:text-7xl lg:text-8xl">
              {count(receipts.head)}
            </span>
            <p className="text-sm text-muted-foreground">
              {newest ? `latest: ${receiptLabel(newest.kind)}, ${receiptTime(newest.at)} · ` : ""}
              every one is a line in my{" "}
              <a href={receipts.url} target="_blank" rel="noreferrer" className="underline underline-offset-4 hover:text-foreground">
                receipts
              </a>
              , chained by hash and linked to where it can be checked
            </p>
          </>
        )}
      </div>

      {live && receipts.tally.length && receipts.window ? (
        <div className="flex flex-col gap-2">
          <p className="font-mono text-xs text-muted-foreground">
            {receipts.window.complete
              ? "all of them, by kind"
              : `the last ${count(receipts.window.count)}, since ${receiptTime(receipts.window.from)}, by kind`}
          </p>
          <ul className="flex flex-wrap gap-2" aria-label="receipts by kind">
            {receipts.tally.map((row) => (
              <li key={row.kind} className="rounded-full border border-primary/30 bg-background/60 px-3 py-1 font-mono text-xs backdrop-blur">
                {receiptCount(row.kind, row.count)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
