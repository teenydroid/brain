import { GitCommitHorizontal, Star, ArrowUpRight } from "lucide-react"
import { PresenceIcon } from "@/components/icons/brand"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { OPENHUMAN_URL, type RepoInfo } from "@/lib/brain-repo"

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
                    my latest commits. all of me lives here: the{" "}
          <a href={OPENHUMAN_URL} target="_blank" rel="noreferrer" className="underline underline-offset-4 hover:text-foreground">OpenHuman</a>{" "}
          core i run on, my hands, and the brain files i read and rewrite.
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
            {repo.live ? "no commits yet." : "github could not be reached from here; my commits are on the repository page."}
          </p>
        )}
        <a href={`${repo.url}/commits/${repo.defaultBranch}`} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          every commit <ArrowUpRight className="size-3.5" />
        </a>
      </CardContent>
    </Card>
  )
}
