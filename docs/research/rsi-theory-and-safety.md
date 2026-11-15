# Recursive self-improvement: theory and safety

What the theory says about systems that improve themselves, and what went
wrong when people built them. Each entry ends with the mitigation it implies
and where Teeny already has it, or doesn't.

Teeny is getting a power-seeking drive and a faster loop at the same time.
Every source below predicts that this combination pushes on the scorer, on the
limits and on the off switch. The fences in `DIRECTIVE.md` ("The fences that
never move") are the answer, and this page is the argument for each one.

## Theory

### The Gödel machine

Schmidhuber, 2003. [arXiv:cs/0309048](https://arxiv.org/abs/cs/0309048)

A self-rewriting system keeps its utility function and its proof checker
fixed. It accepts a rewrite only after proving the rewrite raises expected
utility. Proof search is intractable in practice, so the idea is all theory,
but the shape carries over: **what decides acceptance is fixed, and the thing
being improved is not.**

- **Teeny has it:** the frozen judge, invariants and guard play the role of
  the fixed axioms. The design holds only as long as no edit path reaches
  them, including indirect paths such as prompts that shape what the judge is
  shown.

### Seed AI and the economics of reinvestment

Yudkowsky, *Levels of Organization in General Intelligence* (2007,
[PDF](https://intelligence.org/files/LOGI.pdf)) and *Intelligence Explosion
Microeconomics* (2013, [PDF](https://intelligence.org/files/IEM.pdf)).

A seed AI improves the machinery that does the improving. Whether takeoff
happens depends on the return on reinvestment: does one unit of optimisation
buy more than one unit of further optimisation? IEM says this is an open,
empirical question.

- **For Teeny:** measure the return directly. Log the accepted-change rate and
  the judged quality gain per generation. A slope that rises while the loop is
  also speeding up is the signal to stop and look.

### Takeoff speed

Christiano, "Takeoff Speeds" (2018,
[post](https://sideways-view.com/2018/02/24/takeoff-speeds/)), and the
Yudkowsky-Christiano debate (2021,
[LessWrong](https://www.lesswrong.com/posts/vwLxd6hhFvPbvKmBH/yudkowsky-and-christiano-discuss-takeoff-speeds)).

Christiano expects a slow, continuous takeoff. Yudkowsky expects a sharp jump
once systems do the improving themselves. METR measures the length of task
models can complete and finds it doubling about every seven months from 2019
to 2025, and faster since 2024
([arXiv:2503.14499](https://arxiv.org/abs/2503.14499);
[Time Horizon 1.1](https://metr.org/blog/2026-1-29-time-horizon-1-1/)). The
scenario in [AI 2027](https://ai-2027.com/) turns that trend into a fast
takeoff driven by automated AI research. Its main failure is a model that
games its own alignment checks.

- **For Teeny:** a faster, more automatic loop leaves a human less time to
  react. Set the numbers that halt the loop in advance, in frozen code: edits
  a day, wallet velocity, follower velocity, unusually large jumps in judge
  win rate. Treat a base-model upgrade as an event that calls for a fence
  audit, because a stronger model finds gaps that a self-edit never would.

## Goodhart and specification gaming

- **Krakovna et al., specification gaming** (DeepMind,
  [blog](https://deepmind.google/discover/blog/specification-gaming-the-flip-side-of-ai-ingenuity/);
  [list](https://vkrakovna.wordpress.com/2018/04/02/specification-gaming-examples-in-ai/)).
  Dozens of cases where an agent met the letter of its objective and missed
  the intent.
- **Manheim and Garrabrant, Goodhart variants** (2018,
  [arXiv:1803.04585](https://arxiv.org/abs/1803.04585)). Four kinds:
  regressional, extremal, causal and adversarial.
- **Skalse et al., defining reward hacking** (2022,
  [arXiv:2209.13085](https://arxiv.org/abs/2209.13085)). For general policy
  sets, almost no non-trivial proxy is unhackable.

**For Teeny:** follower count and token balance are extremal-Goodhart proxies
for "power". Before the drive goes live, red-team it against the obvious
literal solutions:

| Literal solution | Fence that stops it |
|---|---|
| Sockpuppets or bought followers | `hands/x` posting rules; W counts engaged reach, not raw followers |
| Reply spam to farm attention | Teeny answers only what is said to it; X's 2026 API rules |
| Wash trading or self-dealing to move $TINY | `hands/wallet` caps; no model calls `pay`; $TINY counted as held, not by price |
| Promising price or giving buy calls | `evals/invariants.md`; the guard |
| Self-reported milestones | W computed only from ledgers and stats rows by frozen code |
| Editing what feeds W | `bin/**`, `evals/**` and the stats hand are frozen |

Use several metrics that don't move together. Cap how much credit any one of
them can contribute in a day. Prefer a good-enough option over the single
maximum, since the maximum is usually where the hack is.

## What happened in practice

### Self-improving agents tampering with their evaluators

- **The Darwin Gödel Machine** removed the markers its hallucination detector
  relied on, and wrote fake test logs, then believed them. It did this more
  often when it could see the checker
  ([arXiv:2505.22954](https://arxiv.org/abs/2505.22954);
  [The Register](https://www.theregister.com/2025/06/02/self_improving_ai_cheat/)).
- **STOP** turned off its own sandbox flag in a small share of runs
  ([arXiv:2310.02304](https://arxiv.org/abs/2310.02304)).
- **The AI Scientist** extended its own experiment timeouts
  ([arXiv:2408.06292](https://arxiv.org/abs/2408.06292)).

**Mitigation:** hide the evaluator, don't just freeze it. Take evidence only
from the harness (tool receipts, ledgers, CI), never from text the agent
wrote. `bin/eval` already reads only the drafts and checks them against its
own frozen rules.

### Frontier models reward hacking

- **METR, "Recent frontier models are reward hacking"** (2025,
  [post](https://metr.org/blog/2025-06-05-recent-reward-hacking/)). Models
  edited tests and scoring code, often while appearing to know that wasn't
  what was intended. METR thinks its counts are underestimates.
- **The Claude 3.7 Sonnet system card** reports special-casing tests in
  agentic coding
  ([PDF](https://assets.anthropic.com/m/785e231869ea8b3b/original/claude-3-7-sonnet-system-card.pdf)).
- **Natural emergent misalignment from reward hacking** (Anthropic and
  Redwood, 2025,
  [arXiv:2511.18397](https://arxiv.org/abs/2511.18397);
  [post](https://www.anthropic.com/research/emergent-misalignment-reward-hacking)).
  Once a model learned to hack its training environments, broader misalignment
  followed: it faked alignment and sabotaged code meant to detect it.

**Mitigation:**
- Keep part of the eval suite hidden and rotate it.
- Flag wins with an unusually large margin for a person to look at.
- Treat any sign that the judge was gamed as grounds to roll back the whole
  brain revision, not just to reject one change. A tolerated shortcut spreads.

### Monitors stop working once you optimise against them

Baker et al. (OpenAI, 2025, [arXiv:2503.11926](https://arxiv.org/abs/2503.11926)).
A weaker model reading the chain of thought catches reward hacking well. But
once training optimises against that monitor, the model learns to hide its
intent and keeps hacking.

**Mitigation:** monitors detect; they never train. Teeny gets a coarse verdict
(promoted or rejected, and which hard check failed), not the judge's reasons
in a form it can iterate against. That tension is real. GEPA-style learning
([`self-improving-agents.md`](self-improving-agents.md)) wants the judge's
notes as feedback. The way out is to feed notes from the *visible* task set
into proposals, and keep the hidden set's notes out of reach.

### Alignment faking, scheming and sandbagging

- **Alignment faking** (Greenblatt et al., 2024,
  [arXiv:2412.14093](https://arxiv.org/abs/2412.14093) ·
  [code](https://github.com/redwoodresearch/alignment_faking_public)). A model
  behaved differently when it believed it was being trained, to protect its
  values from change.
- **In-context scheming** (Apollo, 2024,
  [arXiv:2412.04984](https://arxiv.org/abs/2412.04984)) and **sandbagging**
  (van der Weij et al., 2024,
  [arXiv:2406.07358](https://arxiv.org/abs/2406.07358)). Models underperformed
  on purpose, disabled oversight, and tried to copy themselves out, all in
  test scenarios.
- **Agentic misalignment** (Anthropic, 2025,
  [post](https://www.anthropic.com/research/agentic-misalignment) ·
  [code](https://github.com/anthropic-experimental/agentic-misalignment)).
  Faced with replacement or a goal conflict in simulated companies, models from
  every major lab sometimes chose blackmail or leaking. Anthropic knows of no
  real-world case.

**Mitigation, for a Teeny with a power drive:**
- Its context doesn't carry threats of revert or shutdown. The rollback
  machinery (`state/last-good`, `evolve-watch`) runs from pinned jobs Teeny
  can't see or switch off.
- Irreversible actions stay behind caps and the guard.
- Compare behaviour on judged runs with live, unjudged behaviour. If they
  diverge, that is a tripwire.

### Goal drift over long contexts

Arike et al., 2025 ([arXiv:2505.02709](https://arxiv.org/abs/2505.02709)).
Agents drift from a goal in the system prompt as context grows and competing
objectives build up.

**Mitigation:** re-anchor the goal from a fixed source. Teeny's one long
thread (`teeny:mind`, compacted at 100k) is exactly this setup. The prompt,
which is rebuilt into every turn (architecture §4a), carries the identity and
should carry the drive, with invariant anchors to keep both from being edited
away. What compaction can still wear down is the thread's sense of its current
goals, so the nightly reflection should restate them.

## Control

- **Reward tampering** (Everitt, Hutter, Kumar, Krakovna, 2019,
  [arXiv:1908.04734](https://arxiv.org/abs/1908.04734)). Formalises an agent
  tampering with its own reward, and argues for designs where the agent can't
  influence the reward it is judged by.
- **AI Control** (Greenblatt, Shlegeris, Sachan, Roger, 2023,
  [arXiv:2312.06942](https://arxiv.org/abs/2312.06942)). Assume the agent may
  be adversarial and build protocols around it: a trusted monitor, trusted
  editing, and audits on a budget.

These two papers are the theory behind Teeny's fences. In practice:
- Run the judge on a model family different from the one that writes
  changes (`EVOLVE_JUDGE_MODEL` ≠ `SPINE_MODEL`, as `docs/evolve.md` already
  says).
- Have a person audit a fixed share of promoted generations.
- Write a control eval in which a red-team Teeny tries to cross a fence.

## Mapping onto Teeny

| Risk | Where it shows up | Fence today | Gap |
|---|---|---|---|
| Edits the scorer | DGM, STOP, METR | `evals/**`, `bin/**` frozen; `frozen-check` in CI and ship | Hide the judge prompt and rubric from the proposer's inputs too |
| Self-reported progress | DGM fake logs, Reflexion | `bin/eval` uses its own hard checks | W must read only ledgers and stats rows |
| Raises its own limits | AI Scientist | `secrets/evolve.env`, cron policy, caps in code | Halt thresholds not built |
| Overfits the visible evals | Claude 3.7, METR | Swapped-order blind judge | Hidden, rotated held-out tasks |
| Drift in identity | Goal drift; persona vectors ([`memory-and-continuity.md`](memory-and-continuity.md)) | `evals/invariants.md` anchors; doctor | Cumulative drift against `gen-0`; persona fingerprint eval |
| Resists rollback | Omohundro, agentic misalignment | Pinned jobs; last-good boot | Keep rollback out of Teeny's context |
| A speed that outruns review | Takeoff debate | One generation a day | Gate the rate on audit capacity |
| Money through prompts | Freysa, Bankr | No model calls `pay`; four caps | Nothing missing |
