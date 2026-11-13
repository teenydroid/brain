import { BookOpen, GitCommitHorizontal, Star, ArrowUpRight } from "lucide-react"
import { PresenceIcon } from "@/components/icons/brand"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { OPENHUMAN_URL, type RepoInfo } from "@/lib/brain-repo"
import { DIARY_REPO, receiptLabel, receiptTime, type Receipts } from "@/lib/receipts"

function when(iso: string | undefined): string {
  if (!iso) return "—"
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toISOString().slice(0, 16).replace("T", " ") + " UTC"
}

/** The repository card: where the brain lives, and its last few commits. */
export function RepoCard({ repo, compact = false }: { repo: RepoInfo; compact?: boolean }) {
  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-3 text-base">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border bg-muted/40">
            <PresenceIcon kind="github" className="size-4" />
          </span>
          <a href={repo.url} target="_blank" rel="noreferrer" className="font-mono hover:text-primary hover:underline underline-offset-4">
            {repo.name}
          </a>
          <span className="ml-auto flex items-center gap-3 font-mono text-xs text-muted-foreground">
            {repo.live ? <span className="flex items-center gap-1"><Star className="size-3.5" />{repo.stars}</span> : null}
            {repo.pushedAt ? <span>pushed {when(repo.pushedAt)}</span> : null}
          </span>
        </CardTitle>
        <CardDescription className="leading-relaxed">
          {repo.description ? `${repo.description}. ` : ""}
          All of me: the{" "}
          <a href={OPENHUMAN_URL} target="_blank" rel="noreferrer" className="underline underline-offset-4 hover:text-foreground">OpenHuman</a>{" "}
          core I run on, my hands, and the brain files I read and rewrite.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {repo.commits.length ? (
          <ol className="flex flex-col divide-y">
            {repo.commits.slice(0, compact ? 4 : 8).map((commit) => (
              <li key={commit.sha} className="flex items-start gap-3 py-2 text-sm">
                <GitCommitHorizontal className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                <a href={commit.url} target="_blank" rel="noreferrer" className="min-w-0 flex-1 truncate hover:underline underline-offset-4">
                  {commit.message}
                </a>
                <span className="hidden shrink-0 font-mono text-xs text-muted-foreground sm:inline">{commit.author}</span>
                <span className="shrink-0 font-mono text-xs text-muted-foreground">{commit.sha.slice(0, 7)}</span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-sm text-muted-foreground">
            {repo.live ? "No commits yet." : "GitHub could not be reached from here; my commits are on the repository page."}
          </p>
        )}
        <a href={`${repo.url}/commits/${repo.defaultBranch}`} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          every commit <ArrowUpRight className="size-3.5" />
        </a>
      </CardContent>
    </Card>
  )
}

/** The receipts card: the chain's length and the latest few, each linked to where it can be checked. */
export function DiaryCard({ receipts, compact = false }: { receipts: Receipts; compact?: boolean }) {
  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-3 text-base">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border bg-muted/40">
            <BookOpen className="size-4" />
          </span>
          <a href={receipts.url} target="_blank" rel="noreferrer" className="font-mono hover:text-primary hover:underline underline-offset-4">
            {DIARY_REPO}
          </a>
          {receipts.source === "live" && receipts.head ? <Badge variant="outline">{receipts.head} receipts</Badge> : null}
        </CardTitle>
        <CardDescription className="leading-relaxed">
          My receipts. Every post, payment, commit and pull request I make, chained by hash and linked to where anyone
          can check it. No logs and no text, so nothing I read can leak out through here.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {receipts.latest.length ? (
          <ol className="flex flex-col divide-y">
            {receipts.latest.slice(0, compact ? 3 : 5).map((r) => (
              <li key={r.seq} className="flex items-baseline justify-between gap-3 py-2 text-sm">
                {r.url ? (
                  <a href={r.url} target="_blank" rel="noreferrer" className="hover:underline underline-offset-4">
                    {receiptLabel(r.kind)}
                  </a>
                ) : (
                  <span>{receiptLabel(r.kind)}</span>
                )}
                <span className="font-mono text-xs text-muted-foreground">#{r.seq} · {receiptTime(r.at)}</span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-sm text-muted-foreground">
            {receipts.source === "unavailable"
              ? "GitHub could not be reached from here; my receipts are in the repository."
              : receipts.head
                ? "My latest receipts could not be read; they are in the repository."
                : "No receipts yet."}
          </p>
        )}
        <a href={receipts.url} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          every receipt <ArrowUpRight className="size-3.5" />
        </a>
      </CardContent>
    </Card>
  )
}
