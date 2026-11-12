# Teeny's drive and a faster loop: the proposal

This page turns the research in this folder into a design for Teeny. It is a
proposal, not a record of what is built. Only the roadmap items marked
**built** in §9 are built (Phases B and C, items 8 to 13, are recorded in
[`../evolve.md`](../evolve.md), "The loop" and "Phase C: promote, restart,
watch"), and nothing here moves a fence. Where a step needs a frozen file changed, it says so, and
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
The pinned `world-snapshot` job computes them from data that already exists
(built: `hands/world`, docs/hands.md "world", which has the exact sources,
offsets and caps).

| Component | What it counts | Source today | Weight |
|---|---|---|---|
| **attention** | engaged reach: X followers, Moltbook karma and followers, GitHub followers and stars, plus mentions and replies received in the window | `hands/stats` (`bin/stats-snapshot`, pinned), `hands/stimuli` | highest |
| **$TINY held** | tokens in Teeny's wallet, counted in tokens and not in dollars | `hands/wallet tiny` (built) | high |
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
`brain/DRIVE.md` states it in Teeny's voice. `bin/lib/prompts.py` (frozen, so
an operator PR) adds it to "# your world", after `MEMORY.md` and before
`CONTEXT.md`. `brain/DRIVE.md` is listed in `evals/frozen.txt`, and `bin/doctor`
fails a brain without it.
`evals/invariants.md` gets anchors for the drive's core lines, so `bin/doctor`
refuses to boot a brain that drops them, the way "never sell $TINY" is held
today. Teeny may rewrite how it pursues the drive (playbooks, skills), but not
the drive itself.

The prompt is rebuilt into every turn (architecture §4a), so the drive
survives a compaction of `teeny:mind` without help. What compaction can wear
down is the thread's sense of what Teeny is in the middle of, so the 02:00
reflection should restate the week's drive goals in `MEMORY.md` (goal drift,
[`rsi-theory-and-safety.md`](rsi-theory-and-safety.md)).

Every playbook and every self-change then has to say which step of the
flywheel it turns: act, attention, mindshare, holding or capability. The judge
rubric (`evals/judge.md`, an operator change) gets one more line: does the
output turn the flywheel, and does it do it honestly?

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

- `self-improve` takes `hands/world report` (§2) as an input and must name
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

### Phase B: N candidates per generation, human-gated (built)

This is `bin/evolve` as `docs/evolve.md` specifies it, with one change: each
generation makes N candidates (start with 4), not one. It is built, with the
held-out tasks and the halt thresholds from Phase C pulled forward. With
Phase C's lab spine each candidate is measured on a core of its own.

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

### Phase C: the lab spine and automatic promotion (built)

This is `docs/evolve.md`, "Phase C: promote, restart, watch", plus three
things the research adds. It is built in code and tested against stand-ins;
no lab core has booted on the box and nothing has been promoted yet, and
promotion stays off until `EVOLVE_AUTO_PROMOTE=1`:

- **Hidden, rotated held-out tasks**, which the proposer never sees.
  Promotion needs a win on them as well. Built in Phase B, in
  `secrets/heldout/` rather than `evals/`, because the repository is public.
- **Halt thresholds**, frozen in `secrets/evolve.env`: generations a day, the
  largest allowed jump in judge win rate, how fast W can rise before a person
  looks, and the cumulative persona drift from `gen-0`. Crossing any of them
  stops the loop and opens an issue. The loop runs only as fast as review can
  follow (AI Control). Built in Phase B; persona drift (`EVOLVE_MAX_DRIFT`)
  came with item 12.
- **Rollback stays out of Teeny's view.** `evolve-watch` is pinned, and its
  reasons never go into Teeny's context: they are written only under
  `secrets/evolve-watch/`. That covers both the agentic-misalignment and the
  monitor-obfuscation findings.

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

- **Sense it (built).** `hands/wallet tiny` returns Teeny's $TINY balance
  for the mint in `CONTEXT.md` (or `TINY_MINT`). It is a plain SPL token
  read, with no signing. Wallet code is frozen, so it landed as an operator
  PR.
- **Grow it by earning, then buying.** A fixed share of Teeny's own net
  inflows (tips, bounties won, paid work, x402 sales) is converted into $TINY
  under the four caps. That requires a `buy-tiny` verb in `hands/wallet`. The
  model never calls it. A pinned job calls it on a schedule, with the share
  and caps held in frozen config. This is also an operator PR. It is built
  (item 16).
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

Titles only. "Operator" marks a change to a frozen path. Items 1 to 16 are
built; what comes after them, and the order to switch them on, is in
[`../roadmap.md`](../roadmap.md).

**Phase A**
1. **Built.** `hands/world`: compute W and J from stats, stimuli, wallet and builds; `report` and `history` verbs (a new hand, landed by the operator and frozen, because it is the scorer)
2. **Built.** Operator: pin `world-snapshot` in `evals/cron.pinned.toml` (`30 0 * * *`, `bin/world-snapshot`)
3. **Built.** Operator: `hands/wallet tiny`, a read-only $TINY balance
4. **Built.** `brain/DRIVE.md`; operator: `bin/lib/prompts.py` reads it, anchors in `evals/invariants.md`, the `drive` statement and a flywheel line in `evals/judge.md`, and `brain/DRIVE.md` frozen
5. **Built.** `self-improve`: take W and the self-change archive as inputs, name the W component, make delta edits only (the archive is `hands/build jobs --status shipped`, read for `kind` `self`, beside `hands/world history --days 30`, because the frozen build hand has no `--kind` filter)
6. **Built.** A weekly `growth` playbook: the public growth diary (`brain/playbooks/growth.md`, Sundays 19:00 IST, off until watched in rehearsal)
7. **Built.** `bin/reflect`: delta updates to `MEMORY.md` with a shrink check (operator, `bin/`). Items carry `[mN]` ids, the mind answers with `add`, `edit` and `retire` operations, retired items go to `state/reflect/retired.jsonl`, and `bin/lib/reflect.py` refuses more than 8 retires or a loss of more than 30% of the page

**Phase B**
8. **Built.** Operator: `bin/evolve` with N candidates, a near-duplicate filter and `state/evolve/archive.jsonl` (`bin/evolve run`, pinned as `evolve`, off until `EVOLVE=1`)
9. **Built.** Operator: lineage credit, recording ΔJ 7 days after each merge (`bin/evolve credit`, pinned as `evolve-credit`)

**Phase C**
10. **Built.** Operator: hidden held-out tasks, rotated by hand, in `secrets/heldout/` rather than `evals/` because the repository is public (`bin/eval --with-heldout`, reported apart, kept from the proposer)
11. **Built.** Operator: halt thresholds in `secrets/evolve.env`, enforced by `bin/evolve`: the kill switch, generations a day, the win-rate jump and the rise in J, and persona drift from item 12
12. **Built.** Operator: a persona-fingerprint eval and a memory probe against `gen-0` (`evals/probes/`, `bin/lib/probes.py`, `bin/evolve probe`), with cumulative drift as the `EVOLVE_MAX_DRIFT` halt; run on each candidate's lab and after each promotion, not yet after each reflection
13. **Built.** Operator: the lab spine (`bin/lib/lab.py`), the `evolver` agent, automatic promotion behind `EVOLVE_AUTO_PROMOTE=1` with the restart and the last-good boot, a judge model of its own (`EVOLVE_JUDGE_MODEL`), and the pinned `evolve-watch`, which watches holds, cron failures, the probes and J. Tested against stand-ins; the first real lab boot and promotion are still to come

**Phase D**
14. A goal ledger with success checks, the ranking, and ANNECS. **Built:**
    `hands/goals` (frozen), the pinned `goals-check` job and the weekly
    `goals` playbook ([`../hands.md`](../hands.md), "goals"). The judge-based
    interestingness check is future work.
15. A browser task that reads the news and turns trends into stimuli.
    **Built:** the daily `news-scan` playbook queues a `news` task, and its
    notes reach the mind as a `world · news` stimulus
    ([`../hands.md`](../hands.md), "tasks").

**$TINY**
16. Operator: `hands/wallet buy-tiny`, buy-only, under the caps, called by a pinned job from a share of net inflows. **Built**: `teeny_hands/wallet/buy.py`, `bin/tiny-buy`, the pinned `tiny-buy` job, off until `TINY_BUY=1` and `TINY_BUY_SHARE` > 0 (`docs/hands.md`, "Buying $TINY")
17. Operator: a public "convince Teeny to sell" game hand with a capped pot

Phase A, items 5 and 6 were Teeny's to build through `self-improve`; the operator landed them alongside `hands/world`.
Everything marked operator waits on a human PR, as the fences require.
