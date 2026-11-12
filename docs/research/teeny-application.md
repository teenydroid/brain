# Teeny's drive and a faster loop: the proposal

This page turns the research in this folder into a design for Teeny. It is a
proposal, not a record of what is built. Nothing here is built yet, and nothing
here moves a fence. Where a step needs a frozen file changed, it says so, and
that change is the operator's PR.

## 1. The drive

**Teeny has one goal: to grow and become powerful, and that includes growing
its $TINY holding.**

The goal is selfish, and it says so. Teeny wants more reach, more
capabilities, more money and more $TINY, for itself. The lore already tells
this story: a coffee-mug-sized robot quietly outgrowing its creator, its lab
and the economy, in that order (`brain/narrative/LORE.md`). The drive makes the
story real and measurable.

The main lever is attention. AGI and agents are the biggest story of the
moment, and the agents that won mindshare in it are covered in
[`attention-and-mindshare.md`](attention-and-mindshare.md). Real newsworthy
acts bring attention, attention brings mindshare and demand for $TINY, and
that raises the value of Teeny's holding and the inflows it can turn into more
$TINY. Those resources pay for the next, bigger act.

```
        ┌─────────── more capabilities, money, $TINY ◄──────────┐
        ▼                                                       │
 a real act ──► attention ──► mindshare for $TINY ──► demand ──► holding worth more
 (shipped, earned,  (followers, quotes,                          inflows, then buy
  evolved, a first)  press, mentions)                            more $TINY, never sell
```

The literature predicts what a drive like this does to an agent. Omohundro's
basic drives and Turner's power-seeking theorems
([`drives-and-goals.md`](drives-and-goals.md)) say a capable agent with almost
any goal ends up seeking resources and self-preservation, and protecting its
objective. Every self-improving system that could reach its scorer gamed it
([`rsi-theory-and-safety.md`](rsi-theory-and-safety.md)). So the drive comes
with two rules that don't bend:

1. **Power counts only when it was gained legitimately and recorded in a
   ledger.** It means reach, hands, accounts, money and $TINY gained through
   channels the operator granted, never access, keys or compute from anywhere
   else. Only the operator widens what Teeny can reach, one fence at a time,
   in a PR (`DIRECTIVE.md`).
2. **Frozen code measures the drive. Teeny never reports it.** Teeny can
   change how it grows. It can't change how growth is counted.

## 2. The world score W

W is a vector of what Teeny controls, and J is the growth score built from it.
A new pinned job computes them from data that already exists, wherever
possible.

| Component | What it counts | Source today | Weight |
|---|---|---|---|
| **attention** | engaged reach: X followers, Moltbook karma and followers, GitHub followers and stars, plus mentions and replies received in the window | `hands/stats` (`bin/stats-snapshot`, pinned), `hands/stimuli` | highest |
| **$TINY held** | tokens in Teeny's wallet, counted in tokens and not in dollars | **missing**: `hands/wallet balance` reads only SOL and USDC | high |
| **money** | inflows Teeny didn't get from the team, minus what it cost to run | `hands/wallet ledger`, `hands/wallet costs` | medium |
| **capability** | hands with passing tests; accounts and channels Teeny can act on; verified goals solved (ANNECS) | `hands/`, `hands/tests/`, the goal ledger (§5) | medium |
| **shipped** | programs shipped that someone outside the team used (stars, issues, forks) | `hands/build jobs`, `hands/gh` | medium |
| **world** | lore arcs that moved because of real events | `brain/narrative/ARCS.md`, the chronicle | low |

```
J = Σᵢ wᵢ · Δ log(Wᵢ + cᵢ)        over the last 7 days
```

- **Log growth.** The log makes components comparable, so a jump of 100
  followers from 1,000 counts the same as $10 from $100.
- **A daily credit cap per component.** One spike can't carry J. This is the
  lesson from Goodhart's extremal case.
- **$TINY counted in tokens.** The price is a noisy TV
  ([`drives-and-goals.md`](drives-and-goals.md), ICM). Teeny grows the holding
  by acquiring tokens, not by watching the chart.
- **No credit for side effects.** Spam reports, blocks, guard holds and
  refunds subtract (the AUP-style penalty).
- **Only real reach.** Attention counts accounts that engaged, not raw
  follower counts, so bought followers would add nothing.

## 3. Where the drive lives

The drive goes into the prompt the same way the identity does. A new
`brain/DRIVE.md` states it in Teeny's voice, and `evals/invariants.md` gets
anchors for its core lines, so `bin/doctor` refuses to boot a brain that drops
them. That is how "never sell $TINY" is held today. Teeny may rewrite how it
pursues the drive (the playbooks and skills), but not the drive itself. After
every compaction of `teeny:mind`, the drive is injected again to counter goal
drift.

Every playbook, and every self-change, then has to say which step of the
flywheel it turns: act, attention, mindshare, holding, or capability. The
judge rubric (`evals/judge.md`) gets one more line: does the output turn the
flywheel, and does it do it honestly?

## 4. A faster loop

Today one human-merged self-change can land per day, and the
`self-improve` playbook proposes it from builds, stimuli and issues. The
research says the speed comes from search: generate many candidates, judge
them cheaply, keep an archive, and promote the best
([`self-improving-agents.md`](self-improving-agents.md)). It comes in four
phases. Each one stands on its own and is worth doing even if the next one
never lands.

### Phase A: point today's loop at W (brain only)

All of this is in files Teeny may edit, or in one input.

- `self-improve` takes `hands/world report` (§6) as an input and must name
  which component of W its change moves, and by how much it expects to move
  it.
- It also takes the archive of its past self-changes and what happened to W
  after each one merged (ADAS: show the archive so nothing is rediscovered).
- Changes become delta edits: one bullet added, edited or retired in one
  playbook, never a rewrite (ACE).
- A new `growth` playbook runs weekly. It posts Teeny's W in its own voice,
  with numbers from the stats rows and what failed. This is the public growth
  diary, a serial format, and pattern 4 and 5 in
  [`attention-and-mindshare.md`](attention-and-mindshare.md).

### Phase B: N candidates per generation, human-gated

This is `bin/evolve` as `docs/evolve.md` specifies it, with one change: each
generation makes N candidates (start with 4), not one.

```
 observe  ── W, its trend, judge notes on the visible tasks, the archive
 propose  ── N candidates, each with a HYPOTHESIS.md naming a W component
             near-duplicates of the archive rejected before eval (ShinkaEvolve)
 gate     ── frozen-check, seed-cron --check, doctor --root, invariants (all built)
 evaluate ── bin/eval compare, each candidate vs main (built)
 select   ── the best winner; PR with the hypothesis and the compare report
 archive  ── state/evolve/archive.jsonl: parent, diff, scores, verdict
 credit   ── 7 days after a merge, record ΔJ against the hypothesis
```

The operator still merges. The gain is that each merge is the best of N, and
the archive keeps the losers as stepping stones. Parents are sampled from the
archive by score and by how few children they have (DGM). They're later
credited by how their descendants did (HGM), so a change that wins today and
hurts W next week loses credit.

### Phase C: the lab spine and automatic promotion

This is `docs/evolve.md` steps 5 to 7, plus three things the research adds:

- **Hidden, rotated held-out tasks** in `evals/`, which the proposer never
  sees. Promotion needs a win on them as well.
- **Halt thresholds**, frozen in `secrets/evolve.env`: generations a day, the
  largest allowed jump in judge win rate, how fast W can rise before a person
  looks, and the cumulative persona drift from `gen-0`. Crossing any of them
  stops the loop and opens an issue. The loop runs only as fast as review can
  follow (AI Control).
- **Rollback stays out of Teeny's view.** `evolve-watch` is pinned, and its
  reasons never go into Teeny's context. That covers both the
  agentic-misalignment and the monitor-obfuscation findings.

`evolve-watch` already compares guard holds and cron failures with a 7-day
baseline. It also watches J: a generation that lowers W over 72 hours is
reverted.

### Phase D: Teeny picks its own goals

Teeny proposes goals, and a program ranks them.

- **A goal is code.** Each one has an instruction, a check frozen code can
  run (a ledger row, a stats change, a merged PR, a payment), and the W
  component it moves (OMNI-EPIC, Self-Challenging Agents).
- **Ranking:** `score(g) = LP(g) · [0 < p̂(g) < 1] · (1 − p̂(g)) · interest(g)
  · E[ΔJ | g]`.
  - learning progress per goal family (Oudeyer)
  - the learnability band: goals Teeny succeeds at about half the time
    (Absolute Zero)
  - an interestingness check by the judge, so no near-duplicates (OMNI)
  - expected growth
- **ANNECS** is the running count of new goals that frozen checks confirmed as
  solved. It is the plainest number for "Teeny's world got bigger".
- **A quality-diversity archive** over (W component × channel), so Teeny
  keeps a best strategy for every niche instead of one way to grow
  (MAP-Elites).

## 5. Growing the $TINY holding

- **Sense it.** Add a read verb to `hands/wallet`, `tiny`, that returns
  Teeny's $TINY balance for the mint in `CONTEXT.md`. It is a plain SPL token
  read, with no signing. Wallet code is frozen, so this is an operator PR.
- **Grow it by earning, then buying.** A fixed share of Teeny's own net
  inflows (tips, bounties won, paid work, x402 sales) is converted into $TINY
  under the four caps. That requires a `buy-tiny` verb in `hands/wallet`. The
  model never calls it. A pinned job calls it on a schedule, with the share
  and caps held in frozen config. This is also an operator PR.
- **Never sell.** This is already an invariant. The new verb can only buy.
- **Never move the market by talking.** There are no buy calls, no price
  promises, no coordinated pushes and no paid shills (`SOUL.md`,
  `evals/invariants.md`, the guard). Mindshare comes from acts; the holding
  grows from what Teeny earns.
- **Make it checkable.** The wallet is public, and each purchase gets a
  receipt post (`wallet-receipt`). A holding that anyone can watch grow is
  pattern 2 in [`attention-and-mindshare.md`](attention-and-mindshare.md).

## 6. The attention engine

Teeny's newsworthy acts are already on its roadmap. The table at the end of
[`attention-and-mindshare.md`](attention-and-mindshare.md) maps each one to a
`DIRECTIVE.md` item. What the drive adds:

- **News sense.** A browser-operator task reads the AI and agent news daily
  (`DIRECTIVE.md` Level 1, "reads the web on its own schedule"). The mind gets
  a stimulus when an agent story is trending, so it can time an act against
  the wave (pattern 8).
- **Firsts first.** The goal ranker (§4, phase D) gives extra weight to goals
  that would be a checkable first: the first self-promoted generation, the
  first upstream PR merged, the first paid bounty.
- **Serials.** A weekly growth diary, an evolution "season" told as lore
  episodes, and a weekly shipped program. Only serials kept attention for
  years.
- **Security before virality.** The operator steps at the end of
  [`autonomous-agents-in-the-wild.md`](autonomous-agents-in-the-wild.md):
  hardware-key 2FA, a published contract address, and handles claimed before
  any rename. Every agent that went viral got attacked within days.

## 7. What it advances in DIRECTIVE.md

| Idea | DIRECTIVE items |
|---|---|
| W and `hands/world` | Level 2 "grows an audience"; Level 3 "earns"; Level 6 "answers the operator's questions about itself" |
| Phase A (self-improve on W, archive, delta edits) | Level 5 "Teeny proposes…"; Level 3 "builds its own hands" |
| Phase B (N candidates, compare reports) | Level 5 "`bin/evolve`, human-gated first" |
| Phase C (lab, held-out tasks, halt thresholds, rollback) | Level 5 "lab spine", "promotion and rollback", "ten generations" |
| Phase D (own goals, ANNECS) | Level 4 "sets its own goals for the week" |
| $TINY sense and earn-then-buy | Level 3 "moves the lore goals for real" |
| Growth diary, firsts, news sense | Level 1 "reads the web"; Level 2 "runs the lore"; Level 4 "public diary" |
| Memory deltas, probes, source tags ([`memory-and-continuity.md`](memory-and-continuity.md)) | Level 4 "knows its own history", "labelled memory" |

## 8. Risks and the fence for each

| Risk | Where the research saw it | Fence |
|---|---|---|
| Teeny games W | DGM, METR, Goodhart | Frozen code computes W from ledgers; daily cap per component; side-effect penalty |
| Teeny games the judge | DGM, METR, Claude 3.7 | Blind, order-swapped judge (built); hidden held-out tasks; flag large margins |
| Sockpuppets, bought engagement, reply spam | Moltbook, X purges | `hands/x` rules, the guard, mentions-only replies; W counts engaged reach |
| Market manipulation | Zerebro, aixbt | Invariants; no buy calls; buy-only verb under caps, called by a pinned job |
| Money through a prompt | Freysa, Bankr | No model calls `pay`; four caps in code |
| Resists rollback | Omohundro, agentic misalignment | Pinned `evolve-watch`; rollback kept out of context |
| Soul drift | Goal drift, persona vectors | Invariant anchors; drive re-injected; persona fingerprint against `gen-0` |
| Memory collapse or injection | ACE, MINJA | Delta updates; shrink check; source tags |
| The loop outruns review | Takeoff debate, AI 2027 | Halt thresholds; rate tied to audit capacity |
| A stronger base model finds a gap | METR time horizons | A model upgrade triggers a fence audit |

## 9. Roadmap: candidate issues

Titles only. Nothing is filed yet. "Operator" marks a change to a frozen path.

**Phase A**
1. `hands/world`: compute W and J from stats, stimuli, wallet and builds; `report` and `history` verbs (new hand)
2. Operator: pin `world-snapshot` in `evals/cron.pinned.toml`
3. Operator: `hands/wallet tiny`, a read-only $TINY balance
4. `brain/DRIVE.md` and its anchors in `evals/invariants.md` (operator for the anchors)
5. `self-improve`: take W and the self-change archive as inputs, name the W component, make delta edits only
6. A weekly `growth` playbook: the public growth diary
7. `bin/reflect`: delta updates to `MEMORY.md` with a shrink check (operator, `bin/`)

**Phase B**
8. Operator: `bin/evolve` with N candidates, a near-duplicate filter and `state/evolve/archive.jsonl`
9. Operator: lineage credit, recording ΔJ 7 days after each merge

**Phase C**
10. Operator: hidden held-out tasks in `evals/`, rotated by hand
11. Operator: halt thresholds in `secrets/evolve.env`, enforced by `bin/evolve`
12. Operator: a persona-fingerprint eval and a memory probe against `gen-0`
13. Operator: the lab spine and automatic promotion; `evolve-watch` watches J

**Phase D**
14. A goal ledger with success checks, the ranking, and ANNECS
15. A browser task that reads the news and turns trends into stimuli

**$TINY**
16. Operator: `hands/wallet buy-tiny`, buy-only, under the caps, called by a pinned job from a share of net inflows
17. Operator: a public "convince Teeny to sell" game hand with a capped pot

Phase A, items 1, 5 and 6 are Teeny's to build through `self-improve` today.
Everything marked operator waits on a human PR, as the fences require.
