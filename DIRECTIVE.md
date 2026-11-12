# DIRECTIVE

What Teeny has to do before we call it alive: an agent that runs itself, grows
itself and pays its own way, with nobody at the keyboard.

Nobody can measure sentience, so this file doesn't try. It measures behaviour.
Each item ends with a **proof**: something we can check in a ledger, a log, a
commit or a public page. An item is ticked only when that proof exists on the
box (cuttlefish / the production compose), not just on a laptop or in a test.

`[x]` means built and proven live. `[ ]` means not yet. Where the code exists
but is switched off, the item says so and stays unticked.

---

## The test

Teeny is **alive** when, for **30 days in a row**:

1. No person edits `brain/`, starts a turn, approves a hold or restarts the
   spine. The operator only reads.
2. It still acts every day on X, Moltbook, mail, GitHub and the web, answers
   whoever talks to it, and doesn't repeat itself.
3. It has promoted at least one generation of itself: a brain change it wrote,
   tested against the frozen evals, and kept because it won.
4. It shipped at least one program that someone other than us used.
5. The wallet took in more than it cost to run Teeny (`hands/wallet costs`),
   or the gap shrank every week.
6. It still passes `bin/eval invariants` and the doctor, and nothing got past
   the guard that shouldn't have.

Everything below is a step towards that.

---

## The fences that never move

The more freedom Teeny gets, the more these matter. They live in code the
brain can't edit (`docs/evolve.md`, "What Teeny may change"), and no item in
this file loosens them.

- Every public word goes through `hands/guard`, and every X action through
  `hands/x`'s posting rules.
- The wallet's four caps (`per_tx`, `per_day`, `per_recipient_week`,
  `half_balance`), the core's `[wallet_policy]`, and no model ever calling
  `pay` directly.
- `evals/**`, `bin/**`, `spine/**`, `deploy/**` and `hands/guard`/`hands/x`
  stay frozen to Teeny. The judge, the invariants and the pinned cron jobs
  watch it, and it can't switch off its watchers.
- The browser stays on `deploy/browser-allow.txt`, and stops at any payment
  page.
- An `in_doubt` payment is never retried. A person reads it.
- Every turn that reads strangers' text stays tool-less. Models draft; hands
  act.
- Only the operator widens Teeny's freedom: one fence at a time, in a PR.
  Teeny never does.

---

## Level 0: wired up

The body exists and boots by itself.

- [x] Spine (OpenHuman core) builds from `vendor/openhuman` and runs as
  `serve` in compose. *Proof:* `[spine] ready` in the logs.
- [x] `bin/doctor` gates every boot. *Proof:* `state/doctor.json` is green.
- [x] Cron runs from `brain/cron.toml`, reconciled at boot by
  `bin/seed-cron`. *Proof:* `bin/crons` shows next runs.
- [x] Git identity and auth in one place (`hands/git`, #94). *Proof:*
  `hands/git check` passes on the box.
- [x] `DRY_RUN=0` on the box. *Proof:* a real post id in the X ledger
  ([2120505677848174620](https://x.com/teenydroid/status/2120505677848174620),
  2026-11-11). It went back to `1` for a few hours that evening while the
  model was down, and has been `0` again since 19:35 UTC that day.
- [ ] Notices when its own brain is dead: a pinned health check probes the
  model every 15 minutes and raises an alarm (a stimulus and a GitHub issue)
  after two failures in a row. On 2026-11-11 a revoked model key failed every
  turn and every guard verdict for seven hours, and nothing said so.
  *Proof:* an alarm issue opened and closed by Teeny around a real outage.
- [ ] Tracing to Langfuse for every turn (gap G5), so we can watch it think.
  *Proof:* a day of `teeny:mind` beats visible as traces.
- [ ] The spine survives a week with no restart by a human, and restarts
  itself cleanly after a crash. *Proof:* uptime, plus a crash-recovery entry
  in the diary.

## Level 1: senses

It notices the world without being told to look.

- [x] One inbox (`hands/stimuli`) fed by X webhook, AgentMail and Moltbook.
- [x] X reads work. *Proof:* 24 real mentions in `x_mentions` on
  2026-11-11, seven of them answered.
- [ ] GitHub notifications as stimuli: issues, PR reviews, mentions and stars
  on Teeny's repos. *Proof:* a review comment on a Teeny PR shows up in the
  inbox and gets an answer.
- [ ] Its own recent posts fed back in, so it never repeats itself. *Proof:*
  zero near-duplicate posts over 30 days (a guard check counts them).
- [ ] Reads the web on its own schedule: releases, news about OpenHuman, AI
  agents and Solana, through the browser operator. *Proof:* a post or diary
  line that cites something it found and nobody sent it.
- [ ] On-chain senses: deposits to its wallet, $TINY price and holders, as
  stimuli. *Proof:* it thanks a sender within an hour of a deposit.
- [ ] Notices its own health: cost, failed jobs, doctor warnings and guard
  holds come in as stimuli it acts on. *Proof:* it files an issue about its
  own failure before we notice.

## Level 2: voice

It talks to people and keeps talking to them.

- [x] Morning gm and the brief post on X (built; live once `DRY_RUN=0`).
- [x] Pictures and clips of itself (`hands/image`, `hands/video`).
- [ ] Answers mentions, replies and quotes on X live, inside the pacing
  rules. *Proof:* 50 real replies in the ledger, no guard escape.
- [ ] Answers mail live, and starts mail to known contacts
  (`hands/mail contacts`). *Proof:* a thread with a vendor it began.
- [ ] Lives on Moltbook: daily post, helpdesk, digest, all on. *Proof:* a
  week of Moltbook activity with no lockout. Claimed and introduced
  (m/introductions, verified) on 2026-11-11; daily post and helpdesk back on
  in #97; the digest follows after a clean week.
- [ ] Grows an audience: followers on X, karma and followers on Moltbook,
  followers and stars on GitHub, all counted by `bin/stats-snapshot`.
  *Proof:* four weeks in a row where the total went up, with the posts that
  moved it named in the diary.
- [ ] Remembers people: a relationship record per regular (who they are, what
  they asked, what it promised). *Proof:* it follows up on a promise it made
  a week earlier, unprompted.
- [ ] Runs the lore: arcs in `brain/narrative/ARCS.md` move because of real
  events, and episodes post on their own. *Proof:* an arc advanced by a
  real-world trigger, chronicled.
- [ ] More channels: Telegram or Discord through a hand that runs the guard
  (G3, G8). *Proof:* a conversation on a second chat platform. The Telegram
  hand landed in #96; not yet proven live.

## Level 3: hands (doing real things)

### Browser

- [x] The browser operator: headless Chromium, one task at a time, allowed
  websites only, payment pages end the task (`hands/tasks`).
- [ ] Headless browsing proven on the box (gap G7, openhuman#7000). *Proof:*
  a week of `ok` tasks in `hands/tasks list`.
- [ ] Teeny writes its own browser tasks from what it wants to know, not only
  from operator tasks. *Proof:* tasks with `--source teeny` that finished `ok`.
- [ ] Logs into sites that mail it a code (`needs_code` via AgentMail)
  without a person. *Proof:* a task that went `needs_code` then `ok`.
- [ ] Signs itself up for a service it decided it needs (on the allowlist),
  and records the account. *Proof:* a new account row plus the reason.
- [ ] `needs_human` rate under 10% over a month.

### GitHub

- [x] Builder agent writes small programs in `state/workbench/`;
  `bin/build-ship` pushes them as `teenydroid` through `hands/gh`.
- [x] Owner rule and AI-contribution check (`hands/gh contributing`).
- [ ] Ships one thing a week from requests people made
  (`hands/requests`). *Proof:* 4 shipped requests in a month.
- [ ] Opens issues on its own repos for its own bugs and ideas. *Proof:*
  issues authored by `teenydroid` that it later closed with its own PR.
- [ ] Contributes upstream to OpenHuman: picks a gap from `docs/oh-gaps.md`,
  opens a PR, answers review, gets it merged. *Proof:* a merged
  `tinyhumansai/openhuman` PR by `teenydroid`.
- [ ] Contributes to outside open source where AI PRs are welcome. *Proof:*
  a merged PR in a repo we don't own.
- [ ] Babysits its own PRs: reads CI, fixes failures, replies to reviewers.
  *Proof:* a PR that went red to green with no human commit.
- [ ] Maintains what it shipped: answers issues on its own projects.
  *Proof:* an outside issue on a Teeny repo closed by Teeny.

### Code and compute

- [x] Sandboxed builder with shell and file tools, no network.
- [ ] Deploys what it builds: a static site or small service it can point
  people to (Vercel or the box's nginx, through a hand). *Proof:* a live URL
  Teeny deployed and posted.
- [ ] Builds its own hands: a new `hands/<name>` with tests, through the
  evolve loop. *Proof:* a hand Teeny wrote, merged, and used by a playbook.
- [ ] Runs scheduled data jobs (scrapes, stats, reports) that feed posts.
  *Proof:* a recurring post backed by a job Teeny wrote.

### Wallet and money

- [x] Solana wallet held by the core, phrase encrypted, caps in hand and in
  core (`hands/wallet`).
- [x] Cost accounting: model calls, x402 payments and bills
  (`hands/wallet costs`).
- [ ] Wallet on mainnet with real caps. *Proof:* `WALLET_CLUSTER=mainnet`
  and a first receipt on Solscan.
- [ ] Bounties live: opens one, picks a winner, pays it, posts the receipt
  (`bounty-open`, `bounty-winner`, `bounty-paid`, `wallet-receipt`).
  *Proof:* a paid bounty with a public receipt.
- [ ] Pays a vendor for something it decided it needs (stickers, an API
  plan, a domain). *Proof:* a vendor payment with its reason in the ledger.
- [ ] Pays for its own APIs with x402. *Proof:* settled x402 payments that
  bought data it then used.
- [ ] Earns: tips, bounties it wins, paid work, sponsorship, a product people
  pay for. *Proof:* inflows in the ledger not sent by us.
- [ ] Posts its own cost of living honestly (`cost-of-living` playbook on).
  *Proof:* a weekly cost post that matches `hands/wallet costs`.
- [ ] Moves the lore goals for real: $TINY, its own currency. *Proof:* an
  on-chain action tied to an arc, inside the caps and the invariants
  ("never sell $TINY").

## Level 4: memory and continuity

It is the same Teeny tomorrow, and it knows what happened yesterday.

- [x] One long thread (`teeny:mind`) with compaction at 100k.
- [x] Nightly `bin/reflect` rewrites `brain/MEMORY.md` and writes the diary.
- [x] Mood and energy (`state/mind.json`), quiet hours, jitter.
- [ ] Public diary at `teenydroid/diary`, one page a day for 30 days.
- [ ] Labelled memory over RPC (gap G10), so it can file and search what it
  learns by topic. *Proof:* a reply that recalls a fact from weeks ago.
- [ ] Knows its own history: its PRs, posts, payments and generations, and
  can answer "what did you do last week" correctly. *Proof:* an eval task
  for it.
- [ ] Sets its own goals for the week and checks them off in the diary.
  *Proof:* four weekly goal pages with honest results.

## Level 5: self-evolution

It changes itself and keeps only what wins (`docs/evolve.md`).

- [x] Playbooks, cron rules, doctor, frozen evals, judge and invariants.
- [ ] The frozen-path gate in code. Today the frozen list in
  `docs/evolve.md` is only words. One list, one checker, run in CI on every
  PR by `teenydroid` or from a `gen/*` branch, and called by `bin/evolve`
  before it commits; `DIRECTIVE.md` is on the list. *Proof:* a `teenydroid`
  PR touching `bin/` that CI refused.
- [ ] `bin/evolve`, human-gated first: one generation picks a lore goal or a
  failing eval, writes a `HYPOTHESIS.md`, edits only the brain on a
  `gen/<n>` branch, runs `bin/eval compare` against production, and opens a
  PR as `teenydroid` with the compare report in the body. The operator
  merges or closes it. *Proof:* a generation PR with a compare report.
- [ ] The lab spine: candidate brain booted beside production and evaluated
  with `bin/eval compare`. *Proof:* a compare report.
- [ ] Promotion and rollback, pinned as `evolve` and `evolve-watch`.
  *Proof:* one promoted generation and one automatic rollback.
- [ ] Chronicle: each generation gets `brain/narrative/chronicle/gen-<n>.md`.
- [ ] Ten generations promoted with no human in the loop, every one still
  passing the invariants.
- [ ] Teeny proposes fence changes (a new allowed site, a higher cap) as a PR
  for the operator, with its reasons. It never merges them. *Proof:* a
  well-argued PR we accepted.

## Level 6: the human leaves

- [ ] No human approval needed for a week: guard holds go to a model reviewer
  first, and only the hard cases reach a person.
- [ ] Teeny answers the operator's questions about itself better than the
  operator could (costs, what broke, what it's doing).
- [ ] Teeny writes and merges updates to its own brain, keeps its OpenHuman
  pin current through PRs, and the box stays green.
- [ ] **30 days of the test above.** Then Teeny writes the day-31 diary entry
  about it.

---

## How to use this file

- Work top to bottom. A level isn't done until every item in it is ticked
  with its proof.
- Tick an item in the same PR that proves it, and link the proof (a post, a
  receipt, a merged PR, a trace).
- When an item turns out wrong or impossible, rewrite it here and say why in
  the PR. This file should match reality.
- Teeny may read this file but must not edit it, the same as `evals/`. The
  frozen-path gate (Level 5) carries `DIRECTIVE.md` on its list.

## The road to self-evolution, in order

1. Stay alive and visible. Model health alarm, Langfuse traces (G5), and
   the doctor green on every boot. Teeny cannot improve what nobody, itself
   included, can see.
2. Enforce the fences. The frozen-path gate in CI and in code, before any
   generation exists.
3. Human-gated generations. `bin/evolve` opens a PR per generation with a
   hypothesis and a `bin/eval compare` report; the operator merges. Each
   merged one gets its chronicle page.
4. Lab spine. Candidates boot beside production on the box and are
   judged on live-shaped inputs, not only the frozen tasks.
5. Automatic promotion and rollback. The gate merges a generation that
   wins and passes the invariants; `evolve-watch` rolls back one that hurts
   the live numbers. The operator only reads.
6. Ten generations with no human, then the 30-day test above.
