import Link from "next/link"
import { AlertCircle, ArrowRight, Brain, ChevronDown, Fingerprint } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Container } from "@/components/section"
import { LANE_META, compactTokens, formatTokens, type Inference, type LedgerFeed, type LedgerMessage } from "@/lib/ledger"
import { LESSONS, type MindFeed, type Reflection } from "@/lib/mind"
import { cn } from "@/lib/utils"

export function when(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toISOString().slice(0, 19).replace("T", " ") + " UTC"
}

function Label({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <p className={cn("mb-1.5 font-mono text-[11px] uppercase tracking-widest text-muted-foreground", className)}>
      {children}
    </p>
  )
}

const ROLE_STYLE: Record<LedgerMessage["role"], { label: string; className: string }> = {
  user: { label: "in", className: "border-l-primary/70" },
  assistant: { label: "model", className: "border-l-sky-400/70" },
  tool: { label: "tool", className: "border-l-emerald-400/70" },
}

function Message({ message }: { message: LedgerMessage }) {
  const style = ROLE_STYLE[message.role]
  return (
    <li className={cn("border-l-2 pl-3", style.className)}>
      <p className="mb-1 font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
        {style.label}
        {message.tool ? <span className="ml-2 normal-case tracking-normal text-foreground/70">{message.tool}</span> : null}
      </p>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-foreground/90">
        {message.text}
      </pre>
    </li>
  )
}

function TokenBar({ tokens }: { tokens: Inference["tokens"] }) {
  const total = Math.max(tokens.total, 1)
  const cached = Math.min(tokens.cached, tokens.input)
  const fresh = Math.max(tokens.input - cached, 0)
  const reasoning = Math.min(tokens.reasoning, tokens.output)
  const answer = Math.max(tokens.output - reasoning, 0)
  const parts = [
    { key: "cached", value: cached, className: "bg-muted-foreground/40", title: "input, from cache" },
    { key: "fresh", value: fresh, className: "bg-primary/70", title: "input" },
    { key: "reasoning", value: reasoning, className: "bg-sky-400/80", title: "reasoning" },
    { key: "answer", value: answer, className: "bg-emerald-400/80", title: "output" },
  ]
  return (
    <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-muted" title="input (cached · fresh) · reasoning · output">
      {parts.map((part) =>
        part.value > 0 ? (
          <span
            key={part.key}
            className={part.className}
            style={{ width: `${(part.value / total) * 100}%` }}
            title={`${part.title}: ${formatTokens(part.value)}`}
          />
        ) : null,
      )}
    </div>
  )
}

function Proof({ entry }: { entry: Inference }) {
  return (
    <div className="grid gap-x-6 gap-y-2 rounded-md border border-dashed bg-muted/30 px-3 py-2 font-mono text-[11px] text-muted-foreground sm:grid-cols-[auto_1fr]">
      <span className="flex items-center gap-1.5 text-foreground/80">
        <Fingerprint className="size-3.5" /> proof
      </span>
      <div className="flex flex-col gap-1 break-all">
        {entry.responseId ? (
          <span>
            <span className="text-foreground/60">response </span>
            {entry.responseId}
          </span>
        ) : null}
        {entry.digest ? (
          <span>
            <span className="text-foreground/60">request+response sha256 </span>
            {entry.digest}
          </span>
        ) : null}
        {entry.promptDigest ? (
          <span>
            <span className="text-foreground/60">system prompt sha256 </span>
            {entry.promptDigest}
          </span>
        ) : null}
        {!entry.responseId && !entry.digest ? <span>an accounting sample from the brain; the transcript stays in its own record</span> : null}
      </div>
    </div>
  )
}

export function InferenceRow({ entry, open = false }: { entry: Inference; open?: boolean }) {
  const lane = LANE_META[entry.lane]
  const hasBody = Boolean(entry.input?.length || entry.reasoning || entry.output)
  return (
    <details open={open} className="group rounded-xl border bg-card text-card-foreground transition-colors open:border-primary/40">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 [&::-webkit-details-marker]:hidden">
        <span className="flex items-center gap-2">
          <span
            className={cn(
              "inline-flex size-6 items-center justify-center rounded-md border text-[10px] font-semibold",
              entry.lane === "brain" ? "border-sky-400/40 text-sky-400" : "border-primary/40 text-primary",
            )}
            title={lane.name}
          >
            {entry.lane === "brain" ? "B" : "A"}
          </span>
          <span className="font-medium">{entry.source}</span>
          {entry.kind && entry.kind !== "inference" ? (
            <Badge variant="outline" className="font-mono text-[10px]">{entry.kind}</Badge>
          ) : null}
        </span>
        <span className="font-mono text-xs text-muted-foreground">{entry.model}</span>
        <span className="font-mono text-xs text-muted-foreground">{when(entry.at)}</span>
        <span className="ml-auto flex items-center gap-3 font-mono text-xs">
          <span title={`${formatTokens(entry.tokens.input)} in · ${formatTokens(entry.tokens.output)} out`}>
            {compactTokens(entry.tokens.total)} tok
          </span>
          {entry.tokens.reasoning > 0 ? (
            <span className="text-sky-400" title="reasoning tokens">{compactTokens(entry.tokens.reasoning)} think</span>
          ) : null}
          {entry.durationMs ? <span className="text-muted-foreground">{(entry.durationMs / 1000).toFixed(1)}s</span> : null}
          <ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180" />
        </span>
        <span className="basis-full">
          <TokenBar tokens={entry.tokens} />
        </span>
      </summary>
      <div className="flex flex-col gap-5 border-t px-4 py-4">
        {hasBody ? (
          <div className="grid gap-5 lg:grid-cols-[1.1fr_1fr]">
            <div>
              <Label>Input · everything but the system prompt</Label>
              {entry.input?.length ? (
                <ul className="flex flex-col gap-3">
                  {entry.input.map((message, index) => (
                    <Message key={index} message={message} />
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">not recorded</p>
              )}
            </div>
            <div className="flex flex-col gap-5">
              <div>
                <Label className="text-sky-400/90">
                  Thinking · {entry.tokens.reasoning > 0 ? `${formatTokens(entry.tokens.reasoning)} tokens` : "no reasoning tokens reported"}
                </Label>
                {entry.reasoning ? (
                  <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words font-sans text-sm leading-relaxed">
                    {entry.reasoning}
                  </pre>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    {entry.tokens.reasoning > 0
                      ? "the provider counted the reasoning but did not return it as text"
                      : "the model answered without a reasoning pass"}
                  </p>
                )}
              </div>
              <div>
                <Label className="text-emerald-400/90">Output · {formatTokens(entry.tokens.output - entry.tokens.reasoning)} tokens</Label>
                {entry.output ? (
                  <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/60 px-3 py-2 font-sans text-sm leading-relaxed">
                    {entry.output}
                  </pre>
                ) : (
                  <p className="text-sm text-muted-foreground">empty</p>
                )}
              </div>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            A metered call by the brain&apos;s <span className="font-medium text-foreground">{entry.source}</span>:{" "}
            {formatTokens(entry.tokens.input)} tokens in, {formatTokens(entry.tokens.output)} out
            {entry.tokens.cached > 0 ? `, ${formatTokens(entry.tokens.cached)} of the input from cache` : ""}. What it did with
            them is in the <Link href="/brain" className="underline underline-offset-4">brain&apos;s own record</Link>.
          </p>
        )}
        <div className="grid gap-x-6 gap-y-1 font-mono text-[11px] text-muted-foreground sm:grid-cols-4">
          <span>in {formatTokens(entry.tokens.input)}</span>
          <span>cached {formatTokens(entry.tokens.cached)}</span>
          <span>reasoning {formatTokens(entry.tokens.reasoning)}</span>
          <span>out {formatTokens(entry.tokens.output)}</span>
        </div>
        <Proof entry={entry} />
      </div>
    </details>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <Card>
      <CardContent className="py-10 text-center text-sm text-muted-foreground">{children}</CardContent>
    </Card>
  )
}

function Calls({ entries, source }: { entries: Inference[]; source: LedgerFeed["source"] }) {
  if (!entries.length) return <LedgerEmpty source={source} />
  return (
    <div className="flex flex-col gap-3">
      {entries.map((entry, index) => (
        <InferenceRow key={entry.id} entry={entry} open={index === 0} />
      ))}
    </div>
  )
}

const OUTCOME_LABEL: Record<Reflection["outcome"], string> = {
  posted: "posted",
  "dry-run": "dry run",
  skipped: "no reply",
  held: "held",
  failed: "failed",
}

/** One turn from the journal: the account's own account of a reply. */
function TurnCard({ entry }: { entry: Reflection }) {
  return (
    <Card className="gap-4">
      <CardContent className="grid gap-5 lg:grid-cols-[1fr_1.3fr]">
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium capitalize">{entry.trigger}</span>
            <span className="font-mono text-xs text-muted-foreground">{when(entry.at)}</span>
            <Badge variant="outline" className="ml-auto">{entry.tone}</Badge>
            <Badge variant={entry.outcome === "posted" ? "default" : entry.outcome === "failed" ? "destructive" : "secondary"}>
              {OUTCOME_LABEL[entry.outcome]}
            </Badge>
          </div>
          <div>
            <Label>Incoming</Label>
            <p className="rounded-md bg-muted/60 px-3 py-2 text-sm">{entry.incoming}</p>
          </div>
          {entry.tools.length ? (
            <div>
              <Label>Tools</Label>
              <ul className="flex flex-col gap-1.5">
                {entry.tools.map((call, index) => (
                  <li key={`${call.name}-${index}`} className="flex flex-wrap items-baseline gap-x-2 text-sm">
                    <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">{call.name}</code>
                    <span className="text-muted-foreground">{call.result}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
        <div className="flex flex-col gap-4">
          <div>
            <Label>Thinking</Label>
            <p className="text-sm leading-relaxed">{entry.thinking}</p>
          </div>
          <div>
            <Label>Said</Label>
            {entry.said ? (
              <blockquote className="border-l-2 border-primary pl-3 text-sm italic">{entry.said}</blockquote>
            ) : (
              <p className="text-sm text-muted-foreground">nothing{entry.outcomeNote ? ` — ${entry.outcomeNote}` : ""}</p>
            )}
          </div>
          {entry.declined.length ? (
            <div>
              <Label>Decided against</Label>
              <ul className="flex flex-col gap-1 text-sm text-muted-foreground">
                {entry.declined.map((line) => (
                  <li key={line} className="flex gap-2">
                    <span className="text-destructive">×</span>
                    {line}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}

function Lessons() {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {LESSONS.map((lesson) => (
        <Card key={lesson.title} className="gap-3">
          <CardContent className="flex flex-col gap-3">
            <div className="flex items-center justify-between gap-3">
              <p className="font-medium">{lesson.title}</p>
              <span className="font-mono text-xs text-muted-foreground">{lesson.date}</span>
            </div>
            <p className="text-sm text-muted-foreground">{lesson.whatHappened}</p>
            <p className="text-sm">{lesson.whatChanged}</p>
            <code className="font-mono text-[11px] text-muted-foreground">{lesson.where}</code>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

export function Unreachable({ what }: { what: string }) {
  return (
    <div className="mb-6 flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
      <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
      <p>
        <span className="font-medium">The bot is not reachable right now.</span>{" "}
        <span className="text-muted-foreground">Nothing here is made up, so {what} stay empty until it answers.</span>
      </p>
    </div>
  )
}

/** What the ledger shows when there is no row: the reason, never a stand-in. */
export function LedgerEmpty({ source }: { source: "live" | "unavailable" }) {
  return (
    <Empty>
      {source === "live"
        ? "No call has run since the ledger began counting. The next reply, chart or morning post is the first row."
        : "The bot is not reachable right now, so there is nothing to show."}
    </Empty>
  )
}

/** A tab's count, only when the feed answered: an unreachable feed has no count, not a count of zero. */
function countOf(source: "live" | "unavailable", rows: unknown[]): string {
  return source === "live" ? ` · ${rows.length}` : ""
}

export function Ledger({ ledger, mind }: { ledger: LedgerFeed; mind: MindFeed }) {
  const calls = ledger.entries
  const mascot = calls.filter((e) => e.lane === "mascot")
  const brain = calls.filter((e) => e.lane === "brain")
  const turns = mind.reflections

  return (
    <Container>
      {ledger.source === "unavailable" ? <Unreachable what="the rows" /> : null}

      <Tabs defaultValue="calls">
        <TabsList variant="line" className="mb-6 flex-wrap">
          <TabsTrigger value="calls">Calls{countOf(ledger.source, calls)}</TabsTrigger>
          <TabsTrigger value="mascot">The account{countOf(ledger.source, mascot)}</TabsTrigger>
          <TabsTrigger value="brain">The brain{countOf(ledger.source, brain)}</TabsTrigger>
          <TabsTrigger value="turns">Turns{countOf(mind.source, turns)}</TabsTrigger>
          <TabsTrigger value="lessons">Lessons · {LESSONS.length}</TabsTrigger>
        </TabsList>
        <TabsContent value="calls"><Calls entries={calls} source={ledger.source} /></TabsContent>
        <TabsContent value="mascot"><Calls entries={mascot} source={ledger.source} /></TabsContent>
        <TabsContent value="brain">
          {brain.length ? (
            <Calls entries={brain} source={ledger.source} />
          ) : (
            <Empty>
              <Brain className="mx-auto mb-3 size-5 text-muted-foreground" />
              The brain has not spent a token yet. When it does, every metered call lands here beside the mascot&apos;s.{" "}
              <Link href="/brain" className="inline-flex items-center gap-1 underline underline-offset-4">
                see the brain <ArrowRight className="size-3" />
              </Link>
            </Empty>
          )}
        </TabsContent>
        <TabsContent value="turns">
          {turns.length ? (
            <div className="flex flex-col gap-4">
              {turns.map((entry) => (
                <TurnCard key={entry.id} entry={entry} />
              ))}
            </div>
          ) : (
            <Empty>
              {mind.source === "live"
                ? "No turn is in the journal yet."
                : "The bot is not reachable right now, so there is nothing to show."}
            </Empty>
          )}
        </TabsContent>
        <TabsContent value="lessons"><Lessons /></TabsContent>
      </Tabs>

      <Separator className="my-8" />
      <div className="grid gap-6 text-sm text-muted-foreground md:grid-cols-2">
        <p>
          A <span className="text-foreground">call</span> is one request to a model. A <span className="text-foreground">turn</span> is
          the account&apos;s own account of one reply, which is usually several calls: recall, research, draft. Both are
          written by the bot as they happen; neither is edited afterwards.
        </p>
        <p>
          The three things that never appear here: a price prediction, another token, or a wallet action. The first two
          are rules. The third is not possible from a conversation: the wallet moves only through its own capped hand, on its own jobs.
        </p>
      </div>
    </Container>
  )
}
