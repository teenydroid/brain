# teeny / web

The landing page at teeny.enamakel.com: Teeny's soul, the rules it runs under,
the tools it can reach, what its brain runs on, its ledger and its articles.

Next.js 16, Tailwind v4, shadcn/ui (Base UI primitives), three.js via
react-three-fiber. It runs as the `web` service in `deploy/compose.yaml`
(its own image, `127.0.0.1:3120`, behind the `web` profile).

## Pages

One route per part of the mind, each with its own scene behind the hero:

| Route | What | Scene |
| --- | --- | --- |
| `/` | the token counter; the brain at a glance, its repository and diary; the last few calls | a brain: points on a sphere, linked to their neighbours, neurons firing |
| `/ledger` | every model call: input, thinking, output, cost, digest | a stream of thoughts flowing past a few that settled |
| `/brain` | what Teeny runs on: the core, the hands, the agents, the schedule, the playbooks, the repository and the diary | rings of tools orbiting a core |
| `/soul` | who it is | one warm, soft, restless blob |
| `/rules` | what no message can change | a core held inside a cage of turning rings |
| `/tools` | everything it can do, and cannot | a lattice of points |
| `/articles` | long pieces, in full | |

`src/lib/pages.ts` is the registry: nav, home cards, heroes and prev/next all
read from it. Scenes live in `src/components/scene/scenes/` and honour
`prefers-reduced-motion` by standing still.

```sh
npm install
npm run dev      # http://localhost:3000
npm run build
```

## The brain and the diary

`/brain` reads the brain from its public repository, `BRAIN_REPO` (default
`teenydroid/brain`), on GitHub: the agents from `brain/agents/*.toml`, the
playbooks from `brain/playbooks/*.md` front matter, the jobs from
`brain/cron.toml` and `evals/cron.pinned.toml`, the hands from `hands/*`, and
the last few commits (`src/lib/brain-repo.ts`). The web image is built from
`web/` alone, so those files are fetched, not read from disk, and cached for
an hour. The receipts, `DIARY_REPO` (default `teenydroid/diary`), are read from
its `head.json` and the last week of `receipts/` files, each linked to where
it can be checked; without a head only the link is shown. If GitHub is
unreachable the page says so and lists nothing. `GITHUB_TOKEN` is optional and
only raises the rate limit.

## The feeds

The other live parts read a JSON API at `MIND_API_URL` (`src/lib/api.ts`),
each revalidated every 60s: `/mind` (`src/lib/mind.ts`), `/ledger`
(`src/lib/ledger.ts`) and `/articles` (`src/lib/articles.ts`). The front-page
counter polls `/api/pulse`, a route in this app that reads the same API.
Unset or unreachable, every page says so and shows nothing: there is no
sample data.

That API was served by Teeny's previous runtime, which no longer runs, and
the current spine does not serve it, so today these feeds show their empty
state.

`NEXT_PUBLIC_GA_ID` turns on analytics. Everything static on the page
(`src/lib/brain.ts`) is sourced from `SOUL.md`, `CONTEXT.md`, `README.md` and
the published tool list.
