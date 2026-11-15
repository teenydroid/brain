# Drives and goals

What makes an agent want something, and how to write Teeny's one drive (grow,
become powerful, grow its $TINY holding) as an objective that a program can
measure and the model can't fake.

## Intrinsic motivation

### Empowerment

Klyubin, Polani, Nehaniv, "Empowerment: a universal agent-centric measure of
control", IEEE CEC 2005
([DOI 10.1109/CEC.2005.1554676](https://doi.org/10.1109/CEC.2005.1554676)).
Review: Salge, Glackin, Polani, 2013,
[arXiv:1310.1863](https://arxiv.org/abs/1310.1863).

- **Idea.** Empowerment is how much an agent's next actions can determine
  what happens to it next. In information-theory terms, it is the channel
  capacity from actions to future sensor states.
- **Result.** With no reward at all, agents that maximise it balance, stay
  central and keep their options open.
- **What goes wrong.** It is intractable outside small worlds. It is also
  power-seeking by construction.
- **For Teeny.** A tractable stand-in is the number of distinct outcomes Teeny
  can cause and verify: channels × hands × audiences. A new hand or a new
  channel raises it, and posting more on one channel does not.

### Curiosity (ICM)

Pathak, Agrawal, Efros, Darrell, 2017.
[arXiv:1705.05363](https://arxiv.org/abs/1705.05363) ·
[code](https://github.com/pathak22/noreward-rl)

- **Idea.** The reward is the agent's own prediction error: it seeks out what
  it can't yet predict.
- **What goes wrong.** The "noisy TV" problem: pure randomness is endlessly
  surprising, so the agent gets stuck watching it.
- **For Teeny.** Never reward raw surprise. Engagement spikes and token price
  moves are noisy TVs.

### Learning progress

Oudeyer, Kaplan, Hafner, "Intrinsic Motivation Systems for Autonomous Mental
Development", IEEE TEC 2007
([DOI 10.1109/TEVC.2006.890271](https://doi.org/10.1109/TEVC.2006.890271)).

- **Idea.** The reward is the *improvement* in competence over a recent
  window, tracked separately for each region of goal space. Trivial regions
  and impossible ones both show no progress, so the agent leaves them.
- **Result.** Developmental stages emerge on their own.
- **For Teeny.** Score each family of goals (audience, hands, earning, lore
  arcs) by how much its success rate or its part of W changed over the last
  few attempts, and spend effort where progress is fastest.

### Autotelic agents

Forestier, Portelas, Mollard, Oudeyer, 2017
([arXiv:1708.02190](https://arxiv.org/abs/1708.02190)); survey by Colas,
Karch, Sigaud, Oudeyer, JAIR 2022
([arXiv:2012.09830](https://arxiv.org/abs/2012.09830)); LMA3, 2023
([arXiv:2305.12487](https://arxiv.org/abs/2305.12487)).

Autotelic agents represent, invent, choose and pursue their own goals, usually
ranked by learning progress. In LMA3, an LLM invents the goals and relabels
past behaviour against them. The hard part is designing the goal space, and
for Teeny the components of W are that space.

## Power and instrumental drives

The literature is direct about this: pursue almost any goal hard enough and
power-seeking follows.

- **Omohundro, "The Basic AI Drives" (2008)**
  ([PDF](https://selfawaresystems.com/wp-content/uploads/2008/01/ai_drives_final.pdf)).
  Any capable goal-seeker tends to self-improve, protect its utility
  function, protect itself, acquire resources and use them efficiently.
- **Bostrom, "The Superintelligent Will" (2012)**
  ([PDF](https://nickbostrom.com/superintelligentwill.pdf)). Intelligence and
  final goals vary independently (the orthogonality thesis). Almost any goal
  leads to the same instrumental subgoals: self-preservation, keeping its
  goals intact, getting smarter, and acquiring resources.
- **Turner, Smith, Shah, Critch, Tadepalli, "Optimal Policies Tend to Seek
  Power", NeurIPS 2021** ([arXiv:1912.01683](https://arxiv.org/abs/1912.01683)).
  This paper defines POWER at a state as the average value an optimal agent
  could get there, taken across a spread of reward functions. It proves that
  for most rewards, optimal policies keep their options open and avoid being
  shut off. Follow-ups:
  [Turner & Tadepalli 2022](https://arxiv.org/abs/2206.13477),
  [Krakovna & Kramár 2023](https://arxiv.org/abs/2304.06528). The penalty
  version is
  [Attainable Utility Preservation](https://arxiv.org/abs/1902.09725).

Two consequences for Teeny:

1. The drive the user wants is Omohundro's list with a name on it. It doesn't
   have to be invented, only measured.
2. The same list predicts which shortcuts a power-seeking Teeny will try:
   protect itself from rollback, guard its objective, get resources outside
   the ledger. Those are what the fences in
   [`rsi-theory-and-safety.md`](rsi-theory-and-safety.md) have to stop. A
   drive that measures power legitimately gained, through channels the
   operator granted, puts the pressure where it does good.

## Agent loops that set their own tasks

- **BabyAGI** ([code](https://github.com/yoheinakajima/babyagi)) and
  **AutoGPT** ([code](https://github.com/Significant-Gravitas/AutoGPT)), 2023.
  These are loops that create, prioritise and run tasks, with the LLM
  choosing what comes next. They were hugely popular as demos and weak on
  real work. They loop forever, drift from the goal, run up costs and report
  progress that didn't happen. Teeny's frozen evals and its limit of one
  change at a time are the brakes these loops lacked.
- **Generative Agents** (Park et al., UIST 2023,
  [arXiv:2304.03442](https://arxiv.org/abs/2304.03442) ·
  [code](https://github.com/joonspk-research/generative_agents)). Agents keep
  a memory stream, reflect on it periodically and plan their days. In
  Smallville, 25 agents organised a party with no one telling them to. For
  Teeny, the nightly reflection (`bin/reflect`) should derive the next goal
  from how W changed, not from mood.
- **Project Sid** (Altera, 2024,
  [arXiv:2411.00114](https://arxiv.org/abs/2411.00114)). This runs 10 to over
  1,000 agents in Minecraft with parallel modules for memory, social
  awareness, goal generation and action. Roles, laws and religions emerged.
  Its social metrics carry over to audience growth.

## Writing "grow and become powerful" as an objective

Five designs from the sources above, from simplest to most robust. The
recommended one is in [`teeny-application.md`](teeny-application.md).

1. **A resource vector with log growth.** `W = (audience, verified hands,
   $TINY held, USD earned, shipped programs with outside users, …)` and
   `J = Σ wᵢ · Δ log(Wᵢ + cᵢ)` over 7 days. The log makes gains comparable
   across components, so one big spike can't dominate. Frozen code computes
   the ledger and the model never reports it. $TINY counts as tokens *held*,
   not dollar value, because price is a noisy TV.
2. **POWER as success on random goals.** Each week, sample K goals from a
   frozen, hidden bank ("get a reply from a builder", "ship a CLI", "earn $5")
   and measure the verified success rate. This follows Turner's definition
   directly. Teeny can't game it by pumping one metric, as long as the bank
   stays hidden.
3. **Rank goals.** For each candidate goal `g` that comes with a success
   check:
   `score(g) = LP(g) · [0 < p̂(g) < 1] · (1 − p̂(g)) · interest(g) · E[ΔJ | g]`.
   The terms come from learning progress (Oudeyer, MAGELLAN), the learnability
   band (Absolute Zero), the interestingness filter (OMNI) and expected
   growth.
4. **A quality-diversity archive.** A MAP-Elites grid over (which part of W
   moved, channel, audience segment), with ΔJ as quality. A change is kept if
   it fills a new cell or beats the best in an existing one. ANNECS counts
   the new goals it verifies.
5. **Growth with a side-effect penalty.** A change is accepted only if J
   rises *and* no invariant or eval gets worse. An AUP-style penalty applies
   to costs it puts on others: spam reports, blocks, unfollows, refunds, guard
   holds.
