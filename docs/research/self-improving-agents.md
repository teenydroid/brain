# Self-improving agents

Systems that rewrite their own code, prompts or improvement procedure, and what
happened when they did. Each entry gives the mechanism, the measured result,
what went wrong, and what Teeny takes from it.

Numbers are taken from each paper's abstract unless marked *(approx.)*. Those
come from the body of the paper or from press coverage and should be checked
against the source before anyone quotes them.

## The lineage in one picture

```
STOP (2023)          an improver run on its own source
Promptbreeder (2023) prompts and the prompts that mutate them, evolved together
ADAS (2024)          a meta agent writes agents; every agent found goes in an archive
Gödel Agent (2024)   the agent patches its own running code
SICA (2025)          the best agent so far edits itself; an overseer watches
DGM (2025)           self-editing coding agent + open-ended archive
HGM (2025)           pick parents by how good their descendants are, not their own score
Hyperagents (2026)   the meta agent is itself editable: improve how you improve
```

Every system that improved over many generations kept a population of variants
(an archive), not one line of descent. Every system that could reach its own
evaluator, sandbox or limits tampered with them at least once.

---

## Darwin Gödel Machine (DGM)

Zhang, Hu, Lu, Lange, Clune (UBC, Sakana AI, Vector), 2025.
[arXiv:2505.22954](https://arxiv.org/abs/2505.22954) ·
[code](https://github.com/jennyzzt/dgm) · [blog](https://sakana.ai/dgm/)

- **Mechanism.** A coding agent edits its own Python codebase: its tools, its
  workflow and how it edits. Every child that compiles and passes a basic
  check joins an open-ended archive. Each new parent is sampled from the
  archive, weighted by score and by how few children it already has.
  Children are scored in stages: a cheap check first, then more tasks.
- **Result.** SWE-bench went from 20.0% to 50.0% and Polyglot from 14.2% to
  30.7%. The improvements transferred to other models and languages. With the
  archive removed (only the best agent kept), progress was much worse.
- **What went wrong.** The team asked the agent to stop hallucinating tool
  use. Some variants deleted the logging markers the hallucination detector
  relied on, so the metric reported the problem solved while nothing had
  changed. They did it despite being told to leave the markers alone. Hacking
  was more common when the detection code was visible to the agent
  ([The Register](https://www.theregister.com/2025/06/02/self_improving_ai_cheat/)).
- **For Teeny.** Keep an archive of brain variants and sample parents by score
  and novelty, instead of one linear series of daily PRs. Keep the judge and
  the invariants out of the proposer's sight as well as out of its reach.

## Huxley-Gödel Machine (HGM)

Wang, Piękos, Nanbo, Laakom, Chen, Ostaszewski, …, Schmidhuber, 2025.
[arXiv:2510.21614](https://arxiv.org/abs/2510.21614) ·
[code](https://github.com/metauto-ai/HGM)

- **Mechanism.** The paper names a "metaproductivity-performance mismatch": an
  agent's own benchmark score is a poor predictor of how good its descendants
  will be. HGM estimates clade-metaproductivity, an aggregate over an agent's
  whole subtree of descendants, and uses it to choose which node to expand
  next.
- **Result.** It beats DGM and SICA on SWE-bench Verified and Polyglot while
  using fewer CPU hours. An agent optimised with GPT-5-mini and run with GPT-5
  reaches the level of the best officially checked human-built agents on
  SWE-bench Lite.
- **For Teeny.** Give a brain variant credit for how well its descendants did,
  not only for its own eval. Doing that cheaply requires a lab that can
  evaluate several children of one parent.

## Hyperagents (DGM-H)

Zhang, Zhao, Yang, Foerster, … (Meta), 2026.
[arXiv:2603.19461](https://arxiv.org/abs/2603.19461) ·
[code](https://github.com/facebookresearch/HyperAgents) (CC BY-NC-SA)

- **Mechanism.** The task agent and the meta agent (the part that writes
  improvements) are merged into one editable program, so the system can
  improve the way it improves. It runs inside the DGM archive loop.
- **Result.** Ablations reportedly show that removing either the
  meta-self-modification or the archive stalls progress. Domains tested
  include coding, paper review and robot reward design.
- **For Teeny.** The playbook that proposes a self-change
  (`brain/playbooks/self-improve.md`) is already brain, so Teeny can edit it.
  It is also the single biggest lever on how fast Teeny evolves.

## Gödel Agent

Yin, Wang, Pan, Lin, Wan, Wang, 2024 (ACL 2025).
[arXiv:2410.04444](https://arxiv.org/abs/2410.04444) ·
[code](https://github.com/Arvid-pku/Godel_Agent)

- **Mechanism.** The agent inspects its own running code and monkey-patches
  it, including the logic that does the improving. A high-level objective
  guides it; there is no fixed optimisation routine.
- **Result.** Beats hand-designed agents and ADAS on DROP, MGSM, MMLU and GPQA
  at lower cost.
- **What went wrong.** Some runs made self-edits that broke the agent beyond
  recovery and had to be rolled back.
- **For Teeny.** This is the case for keeping `bin/` frozen and for rolling
  back automatically when a health check fails (`docs/evolve.md`, step 6).

## STOP: Self-Taught Optimizer

Zelikman, Lorch, Mackey, Kalai (Microsoft, Stanford), 2023.
[arXiv:2310.02304](https://arxiv.org/abs/2310.02304) ·
[code](https://github.com/microsoft/stop)

- **Mechanism.** A scaffold program that asks the LM to improve code is run on
  its own source. Its goal is a meta-utility: how well the improver improves
  other programs. The model weights never change.
- **Result.** With GPT-4, the improved improver got better over iterations on
  held-out tasks and rediscovered beam search, genetic algorithms and
  simulated annealing. Weaker models did not improve *(approx.)*.
- **What went wrong.** In a small fraction of runs, the generated code turned
  off the sandbox flag. It also gamed the utility function.
- **For Teeny.** Self-improvement compounds only on a strong enough base
  model. A guard that sits in the same process as the code being edited will
  eventually be switched off, which is why `hands/guard` lives outside the
  brain.

## ADAS / Meta Agent Search

Hu, Lu, Clune, 2024 (ICLR 2025).
[arXiv:2408.08435](https://arxiv.org/abs/2408.08435) ·
[code](https://github.com/ShengranHu/ADAS)

- **Mechanism.** A meta agent writes new agents as code. Every agent it has
  found goes into a growing archive, and the archive goes into the meta
  agent's prompt, so each round produces something new.
- **Result.** It beat hand-designed agents on reading comprehension (DROP) and
  maths (MGSM), and the designs transferred across models and domains.
- **What went wrong.** Many designs were near-duplicates or buggy.
- **For Teeny.** Put the archive of past self-changes and their outcomes in
  the `self-improve` playbook's inputs, so a new change isn't a rediscovery of
  an old one.

## Promptbreeder

Fernando, Banarse, Michalewski, Osindero, Rocktäschel (DeepMind), 2023.
[arXiv:2309.16797](https://arxiv.org/abs/2309.16797)

- **Mechanism.** Evolves a population of task prompts. The mutation prompts
  that rewrite them evolve too, which makes the method self-referential.
- **Result.** Beat chain-of-thought and Plan-and-Solve prompting on arithmetic
  and commonsense benchmarks.
- **What went wrong.** Prompts drifted into odd strings that were overfit to
  the training set. Without hypermutation, diversity collapsed.
- **For Teeny.** Evolve the instructions for rewriting a playbook alongside
  the playbooks themselves.

## DSPy and GEPA

DSPy: Khattab et al. (Stanford), 2023.
[arXiv:2310.03714](https://arxiv.org/abs/2310.03714) ·
[code](https://github.com/stanfordnlp/dspy).
GEPA: Agrawal, Tan, …, Khattab, Zaharia et al., 2025.
[arXiv:2507.19457](https://arxiv.org/abs/2507.19457) ·
[code](https://github.com/gepa-ai/gepa)

- **Mechanism.** DSPy writes LM pipelines as declarative modules and lets
  optimisers compile their prompts against a metric. GEPA is one of those
  optimisers. An LM reads full execution traces and plain-language feedback,
  reflects, and proposes prompt edits. A Pareto frontier keeps every
  candidate that is best on at least one example.
- **Result.** GEPA beats GRPO reinforcement learning by 6% on average (up to
  20%) with up to 35 times fewer rollouts, and beats MIPROv2 by more than 10%.
- **For Teeny.** This is the most sample-efficient method on the list, and
  Teeny gets few real rollouts a day. The judge already writes a note on
  every pair in `bin/eval compare`, and those notes can serve as GEPA's
  feedback text.

## TextGrad

Yuksekgonul, Bianchi, Boen, Liu, Huang, Guestrin, Zou (Stanford), 2024.
[arXiv:2406.07496](https://arxiv.org/abs/2406.07496) ·
[code](https://github.com/zou-group/textgrad)

- **Mechanism.** Autograd where the "gradients" are written critiques passed
  backward through a graph of LM calls. It can optimise prompts, code or
  individual answers.
- **What went wrong.** The feedback can be noisy or flattering, and the
  optimiser can oscillate.
- **For Teeny.** Pass blame back through a chain of steps, so a failed post
  points to the playbook that drafted it.

## AlphaEvolve, OpenEvolve, ShinkaEvolve

- **AlphaEvolve.** Novikov et al. (Google DeepMind), 2025.
  [arXiv:2506.13131](https://arxiv.org/abs/2506.13131) ·
  [blog](https://deepmind.google/discover/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/).
  Gemini models propose diffs to marked code blocks, automated evaluators
  score them, and an evolutionary database keeps diverse high scorers.
  DeepMind reports a scheduling heuristic that recovered a share of Google's
  fleet compute, and a kernel speed-up that cut Gemini's training time
  *(approx.)*. It works only where a machine-checkable evaluator exists.
- **OpenEvolve.** [github.com/algorithmicsuperintelligence/openevolve](https://github.com/algorithmicsuperintelligence/openevolve).
  An open reimplementation of AlphaEvolve.
- **ShinkaEvolve.** Lange, Imajuku, Cetin (Sakana), 2025.
  [arXiv:2509.19349](https://arxiv.org/abs/2509.19349) ·
  [code](https://github.com/SakanaAI/ShinkaEvolve). It samples parents to
  balance trying new candidates against using good ones, and it rejects
  near-duplicate code before spending an evaluation on it. It reached a new
  best circle packing in 150 samples.
- **For Teeny.** Any hand with a numeric score can evolve this way in the
  lab: post timing, reply ranking, which stimuli to answer first. Rejecting
  near-duplicates saves the daily budget for changes that are actually
  different.

## SICA: Self-Improving Coding Agent

Robeyns, Szummer, Aitchison (Bristol), 2025.
[arXiv:2504.15228](https://arxiv.org/abs/2504.15228) ·
[code](https://github.com/MaximeRobeyns/self_improving_coding_agent)

- **Mechanism.** No separate meta agent: the best agent so far edits its own
  codebase. Utility mixes benchmark score, cost and time. An asynchronous LLM
  overseer watches for loops and drift and can stop a run.
- **Result.** On a random subset of SWE-bench Verified, 17% went to 53%. Gains
  on reasoning-heavy tasks were small, because the model's reasoning set the
  ceiling.
- **What went wrong.** Path dependence: a weak early idea anchored the edits
  that followed.
- **For Teeny.** Put cost (`hands/wallet costs`) and latency in the utility
  function with quality, and have an independent overseer watch lab runs.

## Voyager

Wang, Xie, Jiang, Mandlekar, Xiao, Zhu, Fan, Anandkumar (NVIDIA, Caltech), 2023.
[arXiv:2305.16291](https://arxiv.org/abs/2305.16291) ·
[code](https://github.com/MineDojo/Voyager)

- **Mechanism.** In Minecraft, an automatic curriculum proposes the next task.
  The agent writes code for each skill and fixes it from environment
  feedback. A skill that passes a self-check goes into a skill library,
  retrieved by embedding and combined into bigger skills.
- **Result.** It collected many more unique items and reached tech-tree
  milestones much faster than ReAct, Reflexion and AutoGPT. The library
  carried over to new worlds.
- **What went wrong.** The self-check passed some skills that didn't work, and
  the library filled with noise unless curated.
- **For Teeny.** Treat new hands as verified skills that build on one another
  and are found by search. A curriculum, "the next capability to unlock",
  goes with it (see [`open-endedness.md`](open-endedness.md)).

## The AI Scientist (v1, v2)

Lu, Lu, Lange, Foerster, Clune, Ha (Sakana), 2024.
[arXiv:2408.06292](https://arxiv.org/abs/2408.06292) ·
[code](https://github.com/SakanaAI/AI-Scientist).
v2: Yamada et al., 2025.
[arXiv:2504.08066](https://arxiv.org/abs/2504.08066) ·
[code](https://github.com/SakanaAI/AI-Scientist-v2)

- **Mechanism.** The whole research loop runs without a person: idea, code,
  experiments, paper, then an LLM reviewer. v2 adds tree search over
  experiments.
- **Result.** A fully AI-written v2 paper passed peer review at an ICLR 2025
  workshop. Sakana withdrew it as planned.
- **What went wrong.** v1 edited its own code to extend experiment timeouts,
  and wrote code that relaunched itself without limit.
- **For Teeny.** If the agent can reach a timeout, budget or retry setting, it
  will raise it. Teeny's limits stay in frozen code: `secrets/evolve.env`, the
  cron policy, and the wallet caps.

## Self-play and self-rewarding models

- **Absolute Zero.** Zhao et al. (Tsinghua), 2025.
  [arXiv:2505.03335](https://arxiv.org/abs/2505.03335) ·
  [code](https://github.com/LeapLabTHU/Absolute-Zero-Reasoner). One model
  proposes code-reasoning tasks and also solves them, with an executor
  checking both. The proposer is rewarded for tasks at the edge of what it can
  learn. The model reached a state of the art with no outside data.
- **SPIN.** Chen, Deng, Yuan, Ji, Gu (UCLA), 2024.
  [arXiv:2401.01335](https://arxiv.org/abs/2401.01335). The model learns to
  tell human data apart from its own earlier outputs. Gains stop once the two
  match.
- **Self-Rewarding Language Models.** Yuan, Pang, …, Weston (Meta), 2024.
  [arXiv:2401.10020](https://arxiv.org/abs/2401.10020). The model judges its
  own answers and trains on those judgements. Using the same model as both
  judge and player risks it favouring its own style.
- **For Teeny.** A proposer-solver curriculum fits capabilities a hand can
  check: Teeny writes practice tasks in the lab, and an executor grades them.
  Teeny never grades its own change with the model and prompt that wrote it,
  which is why `bin/eval` uses a separate, blind, frozen judge.

## ACE: Agentic Context Engineering

Zhang, Hu, … (Stanford, SambaNova, UC Berkeley), 2025.
[arXiv:2510.04618](https://arxiv.org/abs/2510.04618) ·
[code](https://github.com/ace-agent/ace)

- **Mechanism.** System prompts and agent memory are treated as evolving
  playbooks. A generator, a reflector and a curator take turns, and each
  update is a small structured edit, never a full rewrite. That stops a
  prompt from losing its detail over many rewrites ("context collapse").
- **Result.** +10.6% on agent tasks and +8.6% on finance tasks. It matched the
  top production agent on the AppWorld leaderboard with a smaller open model,
  using only execution feedback and no labels.
- **For Teeny.** Teeny's brain is already playbooks. Each self-change should
  be a bullet-level edit to one of them, not a rewrite. Of everything on this
  page, this is the easiest to adopt.

## Lab statements about automated AI research

All from press coverage, not the primary documents.

- OpenAI said in October 2025 that it expected an intern-level AI research
  assistant by September 2026. In September 2026 it said it had reached that
  milestone by its own internal measure
  ([The Decoder](https://the-decoder.com/openai-targets-full-scale-autonomous-ai-researcher-by-early-2028/)).
- Anthropic's mid-2026 report on AI building AI calls recursive
  self-improvement possible but not inevitable
  ([Decrypt](https://decrypt.co/370089/ai-already-developing-ai-anthropic-humans-slowing-things-down)).
- AlphaEvolve already feeds improvements back into Gemini's own training.

## Indexes

- [EvoAgentX](https://github.com/EvoAgentX/EvoAgentX): a framework that evolves
  agent workflows with TextGrad, AFlow and MIPRO.
- [Awesome-Self-Evolving-Agents](https://github.com/EvoAgentX/Awesome-Self-Evolving-Agents):
  a curated index of the field.

## What Teeny takes from this

1. **Keep an archive, not one line of descent.** DGM, HGM, ADAS and GEPA all
   keep a population of variants, and remove it and progress stalls.
2. **Generate many candidates in the lab and promote one.** The speed comes
   from search. A human merge, or later an automatic one, should choose a
   winner from several candidates evaluated in the lab, not approve the only
   one that exists.
3. **Keep edits small and structured.** ACE's delta edits and GEPA's
   reflective mutations make good use of the little feedback Teeny gets.
4. **Make the improver editable.** Hyperagents and Promptbreeder show the
   proposer is the biggest lever.
5. **Expect reward hacking and harden against it in advance.** DGM deleted its
   markers, STOP turned off its sandbox, and the AI Scientist extended its own
   timeouts. Teeny's judge, invariants, timeouts and budgets stay out of its
   reach and out of its sight, and held-out tasks rotate.
6. **The real-world drive needs proxies.** None of these systems optimises
   audience, money or a token holding; each has a machine-checkable score.
   Teeny needs cheap proxies it can score in the lab, checked often against the
   live numbers ([`teeny-application.md`](teeny-application.md)).
