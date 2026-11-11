# teeny / mind

The landing page for Teeny's mind: its soul, the rules it runs under, the tools
it can reach, how its memory works, a log of reflections behind each reply, and
the mistakes it made in public.

Next.js 16, Tailwind v4, shadcn/ui (Base UI primitives), three.js via
react-three-fiber.

## Pages

One route per part of the mind, each with its own scene behind the hero:

| Route | What | Scene |
| --- | --- | --- |
| `/` | the token counter, both minds; the brain at a glance, its repository and diary; the last few calls | a brain: points on a sphere, linked to their neighbours, neurons firing |
| `/ledger` | every model call: input, thinking, output, cost, digest; the journal's turns; the lessons | a stream of thoughts flowing past a few that settled |
| `/brain` | what Teeny runs on: the core, the hands, the agents, the schedule, the playbooks, the repository and the diary | rings of tools orbiting a core |
| `/soul` | who it is | one warm, soft, restless blob |
| `/rules` | what no message can change | a core held inside a cage of turning rings |
| `/tools` | everything it can do, and cannot | a lattice of points |

`/reflections`, `/memory` and `/lessons` are gone: the ledger is where the
turns and the lessons live now, and memory is described under the rules.

`src/lib/pages.ts` is the registry: nav, home cards, heroes and prev/next all
read from it. Scenes live in `src/components/scene/scenes/` and honour
`prefers-reduced-motion` by standing still.

```sh
npm install
npm run dev      # http://localhost:3000
npm run build
```

## The feeds

Set `MIND_API_URL` to the bot's base URL and the page reads three things from
it, each revalidated every 60s and marked live: `/mind` (the journal:
`src/lib/mind.ts`), `/ledger` (every model call and the running token count:
`src/lib/ledger.ts`). Unset or unreachable, every page says so and shows
nothing: there is no sample data anywhere.

`/brain` reads the brain from its public repository, `BRAIN_REPO` (default
`teenydroid/brain`), on GitHub: the agents from `brain/agents/*.toml`, the
playbooks from `brain/playbooks/*.md` front matter, the jobs from
`brain/cron.toml` and `evals/cron.pinned.toml`, the hands from `hands/*`, and
the last few commits (`src/lib/brain-repo.ts`). The web image is built from
`web/` alone, so those files are fetched, not read from disk; cached for an
hour. The diary, `DIARY_REPO` (default `teenydroid/diary`), is listed from its
`days/README.md`; without one only the link is shown. GitHub unreachable, the
page says so and lists nothing. `GITHUB_TOKEN` is optional and only raises the
rate limit.

The counter on the front page is one figure, every token Teeny has burned
across the account and the brain, counting from the first call the ledger
saw. It polls `/api/pulse`, a route in this app that reads the bot's totals
on every request, every twenty seconds, and rolls to the new value. It never
extrapolates: between polls it holds still.

Everything static on the page (`src/lib/brain.ts`) is sourced from `SOUL.md`,
`CONTEXT.md`, `README.md` and the published tool list.
