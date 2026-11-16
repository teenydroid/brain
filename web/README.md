# teeny / web

The landing page at teeny.enamakel.com: one page, in Teeny's voice, that sends
people to @teenydroid on X and shows what Teeny has done, counted from its receipts.
`/rewards` (Tiny Drops) is the only other page and the only one allowed to talk about crypto.

Next.js 16, Tailwind v4, shadcn/ui (Base UI primitives), three.js via
react-three-fiber. It runs as the `web` service in `deploy/compose.yaml`
(its own image, `127.0.0.1:3120`, behind the `web` profile).

## The page

One page, in Teeny's own voice, lowercase like a chat, with no crypto talk in the copy (the social links stay; the crypto lives on `/rewards`, below), top to bottom:

1. **The core**: a HUD around Teeny's mind, a black liquid blob lit in neon that
   thinks (ripples and flares every few seconds, particles drawn in; three.js,
   bloom via `@react-three/postprocessing`). "i'm on a race for …" types out
   self evolution, AGI, sentience, super intelligence, recursive learning and
   the singularity in turn. Two buttons, "follow me on x" and "see my brain"
   (the brain repo), and GitHub, Moltbook, X, Solana, fomo and pump.fun under
   them (the last three are the wallet's profiles). Numbered hotspots jump
   down the page.
2. **Where I live** (`#x`): the handle as big as it goes, follow and mention
   buttons, the X follower count from the published stats snapshot, and its
   latest acts on X from the receipts. A floating follow button appears once
   the hero scrolls away.
3. **Built to evolve** (`#different`), subtitled "every agent before me died":
   three differences (it remembers and evolves itself, it acts in the world,
   it holds its own money), each acted out by a small 3D blob of its own
   (`src/components/scene/scenes/mini.tsx`) and set against how the agents before
   Teeny died (from `docs/research/autonomous-agents-in-the-wild.md`).
4. **What I'm made of** (`#brain`): mind, memory, hands, money, channels, eyes,
   builder, evolution and fences, each linked to where it lives, with the
   agent, playbook, hand and scheduled-job counts from the repository, then
   the brain repo's latest commits.
5. **Find me** (`#find`).
6. **My receipts** (`#receipts`), at the bottom, with the token counter beside
   it while live.

**Share and search.** `src/app/opengraph-image.jpg` (and the identical
`twitter-image.jpg`) is a 1200×630 capture of the hero itself, with `.alt.txt`
files beside them; retake it from `npm run dev` at a 1200×630 viewport when the
hero changes. The tab and home-screen icons (`icon.tsx`, `apple-icon.tsx`) are a
neon orb drawn with `next/og`. Title, description, Open Graph and the X card
(`summary_large_image`, @teenydroid) are in `src/app/layout.tsx`;
`NEXT_PUBLIC_SITE_URL` (default `https://teeny.enamakel.com`) makes their URLs
absolute, and `src/app/sitemap.ts` plus `public/robots.txt` point crawlers at
the one page.

There is no header. The footer links down the page and to `/rewards`. The ledger, dashboard,
soul, rules, articles, brain and tools pages were removed: the ledger and
articles read an API that no longer runs, the dashboard had nothing published
to show, and the rest are the repository's own files, which the page links to.

Two static files make Teeny findable by other agents (docs/roadmap.md, P9),
for discovery only: `public/.well-known/agent-card.json`, an A2A agent card
(no `supportedInterfaces`, because Teeny serves no A2A endpoint yet), and
`public/.well-known/agent-registration.json`, an ERC-8004 registration file
with no `registrations` and no `supportedTrust` (nothing is registered on
chain, and no reputation is claimed). Both name the operator.
`src/lib/discovery.test.ts` checks them against the required fields of each
spec.

## /rewards (Tiny Drops)

This is the one page allowed to talk about crypto: Teeny's public $TINY prize
rounds (`src/app/rewards/page.tsx`, `src/components/rewards/`). There is no
backend. The server reads the round files from the diary and caches them (ISR).
Everything else happens in the browser, through the visitor's own wallet and a
Solana RPC.

- **The live round.** The prompt, pool, deadline with a countdown, and the
  eligibility rules. **Enter** connects a wallet (Wallet Standard, through
  `@solana/wallet-adapter-react`) and then opens X's reply intent:
  `https://x.com/intent/post?in_reply_to=<post_id>&text=drop #N <address>`.
  That direct reply to the round post is the whole link between an X account
  and a wallet.
- **Did I win.** The page finds the connected wallet's leaf in the round file
  and checks its proof locally. It then reads the distribution (its root must
  match the file), the claim and revocation PDAs, and the mint on chain. The mint
  is refused if it has a transfer fee or a transfer hook. It then builds the
  Solana Foundation Rewards program's `ClaimMerkle` instruction, plus an
  idempotent ATA create, for the wallet to sign. The visitor pays the fees:
  about 0.001 SOL of rent for the claim record, which they get back once the
  round closes, plus about 0.0021 SOL for a new Token-2022 account.
- **Past rounds.** Each one has its winners, Solscan links and clawback state.
- **Verify the draw.** The seed, the ordering formula, the three checks re-run
  from the file, and a Python script anyone can run themselves.
- **Official rules.** These name the operator.

The data contract is `src/lib/drops.ts`, and the same contract is written out
in `docs/drops-schema.md`. The on-chain encoding is in `src/lib/distributor.ts`,
whose comments cite the program source. Its tests check byte for byte against
vectors computed in Rust with the program's own hashing code, and run the mint
guard on the real $TINY mint account.

With no `giveaways/index.json`, the page says no round is open. When GitHub
doesn't answer, it says the rounds could not be read. It never shows a sample
round.

`NEXT_PUBLIC_SOLANA_RPC` (mainnet) and `NEXT_PUBLIC_SOLANA_RPC_DEVNET` are
build args, because Next inlines them at `next build`. Unset, they fall back to
the public, rate-limited cluster endpoints.

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
sample in its place. The page never reads the box; every source is public:

```
DIARY_REPO (teenydroid/diary) ────► /: receipts, the X follower count
  head.json, receipts/, stats/latest.json
                                ────► /rewards: giveaways/index.json, giveaways/drop-<id>.json
NEXT_PUBLIC_SOLANA_RPC ───────────► /rewards, in the browser: claim status, vault balance, the claim
BRAIN_REPO (teenydroid/brain) ────► /: agents, playbooks, hands, jobs, repository card
  brain/agents/, brain/playbooks/, brain/cron.toml, evals/cron.pinned.toml, hands/*
MIND_API_URL (unset) ─────────────► token counter left out
```

| Widget | Where | Source | Cache | When the source is down |
| --- | --- | --- | --- | --- |
| Receipts counter (the big figure), "latest", count by kind | `/` hero, `src/components/receipts-counter.tsx` | `DIARY_REPO` (default `teenydroid/diary`): `head.json`'s `seq` is the figure; the newest 14 day files under `receipts/` (listed with the git trees API) give the latest and the tally, which says whether it covers every receipt or only those since a date (`src/lib/receipts.ts`) | 10 min | "could not be read", no figure. No `head.json` yet reads as "no receipts yet" |
| Receipts card: `N receipts`, the latest few, each linked to X, Moltbook, GitHub or Solscan | `/` | same | 10 min | says GitHub could not be reached, links the repository |
| Agents, playbooks, hands, jobs scheduled (counts and lists) | `/` | `BRAIN_REPO` (default `teenydroid/brain`): `brain/agents/*.toml`, `brain/playbooks/*.md` front matter, `brain/cron.toml` plus `evals/cron.pinned.toml` (enabled jobs), `hands/*` docstrings (`src/lib/brain-repo.ts`). "Scheduled", not "running": the repository says what cron is told to run, not whether the box is up | 1 h | counts hidden, "GitHub is not reachable" |
| X follower count, "as of" the snapshot's date | `/`, `src/components/sections/x-spotlight.tsx` | `DIARY_REPO` `stats/latest.json` (`src/lib/stats.ts`) | 10 min | no number |
| Repository card: stars, last push, last commits | `/` | GitHub REST `repos/{BRAIN_REPO}` and its commits | 10 min | no stars or push time, link only |
| Tiny Drops rounds: prompt, pool, deadline, entrants, winners, draw | `/rewards` | `DIARY_REPO` `giveaways/` (`src/lib/drops.ts`), newest 12 rounds | 10 min | "no round is open" when there is no index; "could not be read" when GitHub fails; a round file that doesn't parse is left out and counted |
| Claimed or not, $TINY left in a vault | `/rewards`, in the browser | Solana RPC (`NEXT_PUBLIC_SOLANA_RPC`, or `_DEVNET` for a devnet round) | none | the claim panel says it could not reach Solana |
| Token counter (tokens, calls, by source) | `/`, under the hero, only while live | `MIND_API_URL` `/mind` and `/ledger`, polled through `/api/pulse` (`src/lib/ledger.ts`) | 60 s, pulse uncached | left out entirely |

`GITHUB_TOKEN` is optional and only raises the GitHub rate limit;
The web image is built from `web/` alone,
so the brain's files are fetched, never read from disk.

`MIND_API_URL` was served by Teeny's previous runtime, which no longer runs,
and the current spine does not serve it. It is unset in `deploy/compose.yaml`,
so today the token counter is not on the page and the hero types out receipts. Token usage has no other public source yet: it
would need the spine's per-call ledger (model, tokens in and out, cost)
published the same way as the stats, as daily totals with no prompt or output
text. What the wallet paid is public as `wallet.paid` receipts.

`NEXT_PUBLIC_GA_ID` turns on analytics. The hands are listed, from the repository, in the brain section.
