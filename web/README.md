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
| `/` | what Teeny has done, counted from its receipts; the brain at a glance, its repository and receipts | a brain: points on a sphere, linked to their neighbours, neurons firing |
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
npm ci
npm run dev        # http://localhost:3000
npm test           # vitest: the data mapping in src/lib and the sections that show numbers
npm run lint
npm run typecheck  # next typegen, then tsc
npm run build
```

## Where every number comes from

The rule: a number on the page is a count of something anyone can check, read
server-side at render time and cached (ISR). When its source cannot be read,
the widget says so or is left out; it never shows a default, a zero or a
sample in its place.

| Widget | Where | Source | Cache | When the source is down |
| --- | --- | --- | --- | --- |
| Receipts counter (the big figure), "latest", count by kind | `/` hero, `src/components/receipts-counter.tsx` | `DIARY_REPO` (default `teenydroid/diary`): `head.json`'s `seq` is the figure; the newest 14 day files under `receipts/` (listed with the git trees API) give the latest and the tally, which says whether it covers every receipt or only those since a date (`src/lib/receipts.ts`) | 10 min | "could not be read", no figure. No `head.json` yet reads as "no receipts yet" |
| Receipts card: `N receipts`, the latest few, each linked to X, Moltbook, GitHub or Solscan | `/`, `/brain` | same | 10 min | says GitHub could not be reached, links the repository |
| Agents, playbooks, hands, jobs scheduled (counts and lists) | `/`, `/brain` | `BRAIN_REPO` (default `teenydroid/brain`): `brain/agents/*.toml`, `brain/playbooks/*.md` front matter, `brain/cron.toml` plus `evals/cron.pinned.toml` (enabled jobs), `hands/*` docstrings (`src/lib/brain-repo.ts`). "Scheduled", not "running": the repository says what cron is told to run, not whether the box is up | 1 h | counts hidden, "GitHub is not reachable" |
| Repository card: stars, last push, last commits | `/`, `/brain` | GitHub REST `repos/{BRAIN_REPO}` and its commits | 10 min | no stars or push time, link only |
| Token counter (tokens, calls, by source) | `/` hero, only while live | `MIND_API_URL` `/mind` and `/ledger`, polled through `/api/pulse` (`src/lib/ledger.ts`) | 60 s, pulse uncached | left out entirely |
| Last few calls | `/`, only while live | `MIND_API_URL` `/ledger` | 60 s | section left out |
| Ledger: calls, turns and their tab counts | `/ledger` | `MIND_API_URL` `/ledger` and `/mind` | 60 s | a notice, empty lists, tabs without counts |
| Articles, word counts | `/articles` | `MIND_API_URL` `/articles` (`src/lib/articles.ts`) | 5 min | "not reachable", no list; a missing word count is not shown |

`GITHUB_TOKEN` is optional and only raises the GitHub rate limit. The web
image is built from `web/` alone, so the brain's files are fetched, never read
from disk.

`MIND_API_URL` was served by Teeny's previous runtime, which no longer runs,
and the current spine does not serve it. It is unset in `deploy/compose.yaml`,
so today the token widgets are not on the page and `/ledger` and `/articles`
show their unavailable state. Nothing on the site reads Teeny's wallet balance
or its follower numbers: the wallet's Solana address and the `stats` snapshot
(`hands/stats`) are not published anywhere the site can read. What the wallet
paid is public all the same, as `wallet.paid` receipts.

`NEXT_PUBLIC_GA_ID` turns on analytics. Everything static on the page
(`src/lib/brain.ts`) is sourced from `SOUL.md`, `CONTEXT.md`, `README.md` and
the published tool list.
