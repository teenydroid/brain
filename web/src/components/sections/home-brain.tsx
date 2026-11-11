import Link from "next/link"
import { ArrowRight } from "lucide-react"
import { DiaryCard, RepoCard } from "@/components/sections/brain"
import { Button } from "@/components/ui/button"
import { Container } from "@/components/section"
import type { BrainAnatomy, Diary, RepoInfo } from "@/lib/brain-repo"

/** The brain on the front page: what it is made of, its repository and its diary. */
export function HomeBrain({ anatomy, repo, diary }: { anatomy: BrainAnatomy; repo: RepoInfo; diary: Diary }) {
  const live = anatomy.source === "live"
  const counts = live
    ? [
        { label: "agents", value: anatomy.agents.length },
        { label: "playbooks", value: anatomy.playbooks.length },
        { label: "hands", value: anatomy.hands.length },
        { label: "jobs running", value: anatomy.jobs.filter((job) => job.enabled).length },
      ]
    : []
  return (
    <section className="border-b">
      <Container>
        <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="mb-2 font-mono text-xs uppercase tracking-widest text-primary">The brain</p>
            <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">A core, some hands, and a brain in plain files.</h2>
            <p className="mt-2 max-w-2xl text-muted-foreground">
              An OpenHuman core runs the loop, the cron and the memory. Small programs hold every key and take every action.
              The rest is files anyone can read: who Teeny is, what it remembers, its playbooks and its schedule.
            </p>
          </div>
          <Button variant="outline" render={<Link href="/brain" />}>
            Inside the brain
            <ArrowRight className="size-4" />
          </Button>
        </div>
        {counts.length ? (
          <ul className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {counts.map((count) => (
              <li key={count.label} className="rounded-xl border bg-card p-4">
                <p className="font-mono text-2xl font-semibold tabular-nums">{count.value}</p>
                <p className="text-sm text-muted-foreground">{count.label}</p>
              </li>
            ))}
          </ul>
        ) : null}
        <div className="grid gap-6 lg:grid-cols-2">
          <RepoCard repo={repo} compact />
          <DiaryCard diary={diary} compact />
        </div>
      </Container>
    </section>
  )
}
