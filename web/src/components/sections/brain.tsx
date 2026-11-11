import { ArrowUpRight, BookOpen, GitCommitHorizontal, Star } from "lucide-react"
import { PresenceIcon } from "@/components/icons/brand"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Container } from "@/components/section"
import {
  BRAIN_REPO_URL,
  DIARY_REPO,
  OPENHUMAN_URL,
  describeSchedule,
  type BrainAnatomy,
  type BrainJob,
  type Diary,
  type RepoInfo,
} from "@/lib/brain-repo"
import { cn } from "@/lib/utils"

function when(iso: string | undefined): string {
  if (!iso) return "—"
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toISOString().slice(0, 16).replace("T", " ") + " UTC"
}

function tree(path: string): string {
  return `${BRAIN_REPO_URL}/tree/main/${path}`
}

function blob(path: string): string {
  return `${BRAIN_REPO_URL}/blob/main/${path}`
}

/** GitHub could not be reached, so the brain's files cannot be listed. Said, not papered over. */
export function Unavailable() {
  return (
    <Card>
      <CardContent className="py-8 text-center text-sm text-muted-foreground">
        GitHub is not reachable from here right now, so the brain&apos;s files cannot be listed. They are all in{" "}
        <a href={BRAIN_REPO_URL} target="_blank" rel="noreferrer" className="underline underline-offset-4 hover:text-foreground">
          the repository
        </a>
        .
      </CardContent>
    </Card>
  )
}

function Eyebrow({ children }: { children: React.ReactNode }) {
  return <p className="mb-2 font-mono text-xs uppercase tracking-widest text-primary">{children}</p>
}

function SourceLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-xs text-muted-foreground hover:text-foreground">
      {children} <ArrowUpRight className="size-3" />
    </a>
  )
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
          The whole of Teeny: the{" "}
          <a href={OPENHUMAN_URL} target="_blank" rel="noreferrer" className="underline underline-offset-4 hover:text-foreground">OpenHuman</a>{" "}
          core it runs on, the hands, and the brain files it reads and rewrites.
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
            {repo.live ? "No commits yet." : "GitHub could not be reached from here; the commits are on the repository page."}
          </p>
        )}
        <a href={`${repo.url}/commits/${repo.defaultBranch}`} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          every commit <ArrowUpRight className="size-3.5" />
        </a>
      </CardContent>
    </Card>
  )
}

/** The diary card: one page a day, the latest few when the index is there. */
export function DiaryCard({ diary, compact = false }: { diary: Diary; compact?: boolean }) {
  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-3 text-base">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border bg-muted/40">
            <BookOpen className="size-4" />
          </span>
          <a href={diary.url} target="_blank" rel="noreferrer" className="font-mono hover:text-primary hover:underline underline-offset-4">
            {DIARY_REPO}
          </a>
        </CardTitle>
        <CardDescription className="leading-relaxed">
          The diary. One page a day: what it thought, what it posted, who it answered, what it said on Moltbook
          and what it shipped, written at the end of each day.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {diary.days.length ? (
          <ol className="flex flex-col divide-y">
            {diary.days.slice(0, compact ? 3 : 5).map((day) => (
              <li key={day.date} className="py-2 text-sm">
                <a href={day.url} target="_blank" rel="noreferrer" className="font-mono hover:underline underline-offset-4">
                  {day.date}
                </a>
              </li>
            ))}
          </ol>
        ) : null}
        <a href={diary.url} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          every day <ArrowUpRight className="size-3.5" />
        </a>
      </CardContent>
    </Card>
  )
}

/** The three parts, as the README describes them, with live counts. */
function Parts({ anatomy }: { anatomy: BrainAnatomy }) {
  const live = anatomy.source === "live"
  const parts = [
    {
      name: "Spine",
      where: "spine/, vendor/openhuman",
      href: tree("spine"),
      body: "A stock OpenHuman core, run as its own command-line host. It provides the agent loop, model routing, cron, memory, the browser, the shell and JSON-RPC. No Teeny logic lives in it.",
      count: undefined as string | undefined,
    },
    {
      name: "Hands",
      where: "hands/, bin/",
      href: tree("hands"),
      body: "Small programs that hold every outside capability, every secret and every hard rule. Models only draft; hands act. The model never sees a key.",
      count: live ? `${anatomy.hands.length} hands` : undefined,
    },
    {
      name: "Brain",
      where: "brain/",
      href: tree("brain"),
      body: "Who Teeny is and what it does: SOUL.md, CONTEXT.md, MEMORY.md (rewritten every night), the narrative, the agents, the playbooks and the schedule in cron.toml.",
      count: live ? `${anatomy.agents.length} agents · ${anatomy.playbooks.length} playbooks` : undefined,
    },
  ]
  return (
    <div className="grid gap-4 md:grid-cols-3">
      {parts.map((part) => (
        <Card key={part.name} className="gap-3">
          <CardHeader>
            <CardTitle className="flex items-center justify-between text-base">
              {part.name}
              {part.count ? <span className="font-mono text-[11px] font-normal text-muted-foreground">{part.count}</span> : null}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <CardDescription className="leading-relaxed">{part.body}</CardDescription>
            <SourceLink href={part.href}>{part.where}</SourceLink>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

function runsLabel(job: BrainJob): string {
  if (job.runs.kind === "playbook") return `playbook ${job.runs.target}`
  if (job.runs.kind === "agent") return `agent ${job.runs.target}`
  return job.runs.target
}

function JobRow({ job }: { job: BrainJob }) {
  return (
    <li className="grid grid-cols-[1fr_auto] items-start gap-x-4 gap-y-1 py-3 text-sm sm:grid-cols-[10rem_1fr_auto]">
      <span className="flex items-center gap-2 font-medium">
        {job.name}
        {job.pinned ? <Badge variant="outline" className="font-mono text-[10px]">pinned</Badge> : null}
      </span>
      <span className="order-last col-span-2 text-muted-foreground sm:order-none sm:col-span-1">
        {job.summary ?? ""}
        <code className="mt-0.5 block font-mono text-[11px]">{runsLabel(job)}</code>
      </span>
      <span className="text-right font-mono text-xs text-muted-foreground" title={job.schedule}>
        {describeSchedule(job.schedule, job.tz)}
      </span>
    </li>
  )
}

export function BrainSection({ anatomy, repo, diary }: { anatomy: BrainAnatomy; repo: RepoInfo; diary: Diary }) {
  const live = anatomy.source === "live"
  const running = anatomy.jobs.filter((job) => job.enabled)
  const off = anatomy.jobs.filter((job) => !job.enabled)
  const scheduled = new Set(running.filter((job) => job.runs.kind === "playbook").map((job) => job.runs.target))
  return (
    <>
      <section className="border-b">
        <Container>
          <Eyebrow>What it is</Eyebrow>
          <h2 className="mb-2 text-2xl font-semibold tracking-tight sm:text-3xl">A core, some hands, and a brain in plain files.</h2>
          <p className="mb-8 max-w-2xl text-muted-foreground">
            There is no custom runtime. OpenHuman runs the loop, the cron, the memory and the tools; Teeny is the files it
            reads and the programs it calls. It posts on X as @teenydroid, answers its mentions as they arrive, and at the end
            of every day rewrites its memory and keeps a diary.
          </p>
          <Parts anatomy={anatomy} />
        </Container>
      </section>

      <section className="border-b">
        <Container>
          <Eyebrow>The agents</Eyebrow>
          <h2 className="mb-2 text-2xl font-semibold tracking-tight sm:text-3xl">
            {live ? `${anatomy.agents.length} agents, each with one job.` : "The agents."}
          </h2>
          <p className="mb-8 max-w-2xl text-muted-foreground">
            Read from <code className="font-mono text-sm">brain/agents/*.toml</code>. Most of them only draft, as JSON, with no
            tools at all; a hand takes whatever action follows.
            {live && anatomy.agents.some((agent) => agent.scheduledActions)
              ? ` ${anatomy.agents.filter((agent) => agent.scheduledActions).length} may act on a schedule, under the core's sandbox and named tools.`
              : ""}
          </p>
          {live ? null : <Unavailable />}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {anatomy.agents.map((agent) => (
              <Card key={agent.id} className="gap-3">
                <CardHeader>
                  <CardTitle className="flex items-center justify-between gap-2 text-base">
                    {agent.name}
                    <code className="font-mono text-[11px] font-normal text-muted-foreground">{agent.id}</code>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {agent.purpose ? <CardDescription className="leading-relaxed">{agent.purpose}</CardDescription> : null}
                  <div className="mt-3 flex flex-wrap gap-2">
                    {agent.sandbox ? <Badge variant="outline" className="font-mono text-[10px]">{agent.sandbox.replace("_", " ")}</Badge> : null}
                    {agent.scheduledActions ? (
                      <Badge variant="outline" className="border-primary/40 font-mono text-[10px] text-primary">acts on a schedule</Badge>
                    ) : null}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </Container>
      </section>

      <section className="border-b">
        <Container>
          <Eyebrow>The schedule</Eyebrow>
          <h2 className="mb-2 text-2xl font-semibold tracking-tight sm:text-3xl">
            {live ? `${running.length} jobs running.` : "What runs, and when."}
          </h2>
          <p className="mb-8 max-w-2xl text-muted-foreground">
            From <code className="font-mono text-sm">brain/cron.toml</code>, which Teeny owns and may change, and the pinned jobs it
            cannot switch off: reading its mentions and mail, its end of day, the day&apos;s numbers, and the money.
          </p>
          {live ? (
            <Card>
              <CardContent>
                <ol className="divide-y">
                  {running.map((job) => (
                    <JobRow key={`${job.pinned ? "pinned" : "brain"}-${job.name}`} job={job} />
                  ))}
                </ol>
                {off.length ? (
                  <p className="border-t pt-3 text-sm text-muted-foreground">
                    Kept but switched off: <span className="font-mono text-xs">{off.map((job) => job.name).join(", ")}</span>.
                  </p>
                ) : null}
              </CardContent>
            </Card>
          ) : (
            <Unavailable />
          )}
          <div className="mt-3">
            <SourceLink href={blob("brain/cron.toml")}>brain/cron.toml</SourceLink>
          </div>
        </Container>
      </section>

      <section className="border-b">
        <Container>
          <div className="grid gap-10 lg:grid-cols-[3fr_2fr]">
            <div>
              <Eyebrow>The playbooks</Eyebrow>
              <h2 className="mb-2 text-2xl font-semibold tracking-tight sm:text-3xl">
                {live ? `${anatomy.playbooks.length} playbooks.` : "The playbooks."}
              </h2>
              <p className="mb-6 text-muted-foreground">
                One file each in <code className="font-mono text-sm">brain/playbooks/</code>: what to gather, what to write, and
                which hand acts on it. Teeny may write new ones and schedule them.
              </p>
              {live ? (
                <ul className="grid gap-3 sm:grid-cols-2">
                  {anatomy.playbooks.map((playbook) => (
                    <li key={playbook.slug} className={cn("rounded-xl border bg-card p-4", scheduled.has(playbook.slug) ? "border-primary/40" : "")}>
                      <a href={blob(`brain/playbooks/${playbook.slug}.md`)} target="_blank" rel="noreferrer" className="flex items-center justify-between gap-2 text-sm font-medium hover:underline underline-offset-4">
                        {playbook.slug}
                        {scheduled.has(playbook.slug) ? <span className="font-mono text-[10px] font-normal text-primary">scheduled</span> : null}
                      </a>
                      {playbook.description ? <p className="mt-1 text-sm text-muted-foreground">{playbook.description}</p> : null}
                      <p className="mt-2 font-mono text-[11px] text-muted-foreground">
                        {[playbook.output, playbook.maxPerDay !== undefined ? `≤ ${playbook.maxPerDay}/day` : undefined].filter(Boolean).join(" · ")}
                      </p>
                    </li>
                  ))}
                </ul>
              ) : (
                <Unavailable />
              )}
            </div>
            <div>
              <Eyebrow>The hands</Eyebrow>
              <h2 className="mb-2 text-2xl font-semibold tracking-tight sm:text-3xl">
                {live ? `${anatomy.hands.length} hands.` : "The hands."}
              </h2>
              <p className="mb-6 text-muted-foreground">
                Each one is the only holder of its keys, and the only way its kind of action happens.
              </p>
              {live ? (
                <Card>
                  <CardContent>
                    <ul className="divide-y">
                      {anatomy.hands.map((hand) => (
                        <li key={hand.name} className="grid grid-cols-[6rem_1fr] gap-3 py-2 text-sm">
                          <a href={blob(`hands/${hand.name}`)} target="_blank" rel="noreferrer" className="font-mono hover:underline underline-offset-4">
                            {hand.name}
                          </a>
                          <span className="text-muted-foreground">{hand.summary ?? ""}</span>
                        </li>
                      ))}
                    </ul>
                  </CardContent>
                </Card>
              ) : (
                <Unavailable />
              )}
            </div>
          </div>
        </Container>
      </section>

      <section className="border-b">
        <Container>
          <div className="grid gap-8 lg:grid-cols-2">
            <div>
              <Eyebrow>The repository</Eyebrow>
              <h2 className="mb-6 text-2xl font-semibold tracking-tight sm:text-3xl">All of it, in public.</h2>
              <RepoCard repo={repo} />
            </div>
            <div>
              <Eyebrow>The diary</Eyebrow>
              <h2 className="mb-6 text-2xl font-semibold tracking-tight sm:text-3xl">One page a day.</h2>
              <DiaryCard diary={diary} />
            </div>
          </div>
        </Container>
      </section>
    </>
  )
}
