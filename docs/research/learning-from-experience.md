# Learning from experience

An agent on a frozen model cannot change its weights. Whatever it learns has to
live in text, in code, or in tools that sit outside the model. This page
covers how agents turn their own history into lessons, rules and skills, why
most of those lessons change nothing, and what separates the ones that do.

[`self-improving-agents.md`](self-improving-agents.md) covers systems that
rewrite their own code. [`memory-and-continuity.md`](memory-and-continuity.md)
covers how agents store and keep memory. This page picks up where they stop. It
asks whether a stored lesson actually changes the next decision, and how
anyone could know.

Numbers come from a paper's abstract unless marked *(body)*, which means they
come from the paper's full text. Anything I could not open is marked
*(unverified)*. Most gains below are measured on the authors' own benchmarks
and have not been replicated by others as far as I could check.

## The short version

1. Writing a lesson is cheap and mostly works in benchmarks. Getting the
   lesson used at the moment of the decision is the hard part.
2. Lessons written by the model that made the mistake, and judged by that same
   model, are the weakest kind. The best results come with an outside signal:
   a test, an execution result, a score, a human.
3. In long deployments, operators credited procedures that force a check
   (checklists, tests, tools, fewer handoffs). They did not credit "the agent
   reflects and remembers."
4. A lesson that is never tested against outcomes cannot be told apart from a
   plausible-sounding guess. Most files of lessons are full of those.

---

## Part 1. Experience-to-rule pipelines

Every system here has the same three stages: **extract** something from past
episodes, **store** it, **retrieve** it later. They differ in what the
something is. The unit runs from a raw transcript, through a sentence of
advice, a workflow, a code function, to a whole playbook.

| System | What is extracted | Retrieved how | Reported gain |
|---|---|---|---|
| Reflexion | Short verbal self-critique per failed try | Whole critique in the next attempt's prompt | 91% pass@1 on HumanEval vs 80% for GPT-4 |
| ExpeL | Insights (rules) from comparing wins and losses, plus stored successful runs | Rules always in prompt; runs by task similarity | ALFWorld 40% to 59%; HotpotQA 28% to 39% *(body)* |
| AutoGuide | Guidelines with an "applies when" condition | Matched against the current state | "Significantly outperforms" baselines; no numbers in abstract |
| Agent Workflow Memory | Reusable multi-step workflows | Workflows supplied to guide later actions | +24.6% relative (Mind2Web), +51.1% relative (WebArena) |
| Dynamic Cheatsheet | Strategies, code snippets, insights, self-curated | Whole cheatsheet in context | GPT-4o Game of 24: 10% to 99% |
| ACE | Itemised playbook bullets with counters | Playbook in context | +10.6% on agent tasks, +8.6% on finance |
| Voyager | Verified code skills | Embedding search over skill descriptions | 3.3x more unique items; tech tree up to 15.3x faster |
| SkillWeaver | Practised, tested site APIs | Library of APIs the agent can call | +31.8% relative (WebArena), +39.8% (real sites) |
| Agent KB | Cross-agent trajectories as workflows and fixes | Two-stage: plan-time, then feedback-time | smolagents 55.2% to 73.9% (pass@3); OpenHands +4.0 points |
| Memp | Step-by-step instructions and script-like abstractions | Studied several retrieval and update rules | Rises as the store is refined; no numbers in abstract |

### Reflexion: verbal reinforcement

Shinn et al., 2023. [arXiv:2303.11366](https://arxiv.org/abs/2303.11366)

After a failed attempt the agent writes a few sentences on what went wrong,
keeps them in an episodic buffer, and reads them on the next try. The reward is
words, not a gradient. The 91% HumanEval figure is the abstract's headline.
Note what the gain depends on: a unit test or an environment signal that says
the attempt failed. The critique is written after the world has already said
"wrong."

### ExpeL: rules from comparing wins and losses

Zhao et al., 2023 (AAAI-24).
[arXiv:2308.10144](https://arxiv.org/abs/2308.10144) ·
[code](https://github.com/LeapLabTHU/ExpeL)

The agent collects experience on training tasks, then asks a model to compare
a success and a failure on the same task and to write rules. Four operations
edit the rule set: add, edit, upvote, downvote. A new rule starts with a count
of two. A rule whose count reaches zero is dropped. Successful runs are stored
and retrieved by task similarity.

What the full text adds *(body, via the [HTML version](https://arxiv.org/html/2308.10144v3))*:

- ALFWorld success went from 40.0% (ReAct) to 59.0%. HotpotQA from 28.0% to
  39.0%. WebShop only from 0.666 to 0.701.
- Rules alone and retrieved runs alone each gave less than both together
  (HotpotQA: 36% and 31%, against 39% combined).
- Rules extracted only from the hand-written few-shot examples gave no gain over
  the baseline. The experience had to come from the agent's own runs.
- Adding Reflexion-style reflections to the extraction pool lowered HotpotQA
  from 32.0% to 29.0%. More text in the pool did not mean better rules.
- Retrieving runs by task similarity (59.0%) beat retrieving by reasoning
  similarity (48.5%) and random (42.5%) on ALFWorld.

The up/down vote is the first appearance of a verification idea. But the votes
here are cast by the same model, reading the same trajectories. Nothing in the
loop checks a rule against a later outcome. The paper's own main-results text
and its tables disagree in one place, so treat the Reflexion comparison with
care.

### AutoGuide: rules that say when they apply

Fu et al., 2024. [arXiv:2403.08978](https://arxiv.org/abs/2403.08978)

Each guideline is a short statement with a condition: in situations like X, do
Y. This targets the first failure mode below, advice too general to act on. The
abstract gives no numbers *(unverified)*. The idea, a rule with a trigger, shows
up again in every system that works.

### Agent Workflow Memory (AWM)

Wang, Mao, Fried, Neubig, 2024.
[arXiv:2409.07429](https://arxiv.org/abs/2409.07429)

The unit is a workflow, a reusable routine of several steps, induced from past
runs. It works offline (from training examples) or online (from the test
queries as they arrive). Gains: 24.6% relative on Mind2Web and 51.1% relative
on WebArena, with fewer steps per success. The gap over baselines grew as the
test tasks moved further from the training tasks (8.9 to 14.0 points). A
procedure transfers better than a fact does.

### Dynamic Cheatsheet and ACE: the playbook

Dynamic Cheatsheet: Suzgun et al., 2025,
[arXiv:2504.07952](https://arxiv.org/abs/2504.07952). ACE: Zhang et al., 2025,
[arXiv:2510.04618](https://arxiv.org/abs/2510.04618) ·
[code](https://github.com/ace-agent/ace).

Dynamic Cheatsheet lets a black-box model keep and reuse strategies and code
snippets at test time, with no labels and no weight change. It reports Claude
3.5 Sonnet more than doubling accuracy on AIME, and GPT-4o going from 10% to
99% on Game of 24 after reusing a Python solution. The abstract gives smaller
gains on GPQA-Diamond (9%) and MMLU-Pro (8%) and does not say whether those
are absolute or relative. The big gains came when the model found a reusable
*program*, not when it wrote a lesson.

ACE splits the work into a generator, a reflector and a curator. It keeps the
playbook as itemised bullets, each with an ID and two counters, helpful and
harmful *(body, [HTML](https://arxiv.org/html/2510.04618v3))*. The generator
marks which bullets helped or misled it on a task. The curator appends new
bullets, edits in place, and removes near-duplicates by embedding similarity.
Findings from the full text *(body)*:

- ACE used execution signals (code ran, test passed) instead of labels. On
  AppWorld, ReAct went from 42.4 to 57.2 average without labels, 59.4 with.
- Without a reliable signal it can get worse. Online ACE on a finance set
  without labels lost 3.4 points (67.3).
- Removing the reflector and the multi-pass refinement cut the gain from +17.0
  to +12.7 on AppWorld (offline). Warming up offline before running online
  helped.
- The collapse case: at one step the context had 18,282 tokens at 66.7
  accuracy, then 122 tokens at 57.1, below the 63.7 no-adaptation baseline.
  (The existing page says the same thing in prose.)
- The paper also names "brevity bias": iterative prompt optimisers drift
  towards short generic instructions such as "create unit tests."

### Voyager and the skill libraries

Voyager, Wang et al., 2023, [arXiv:2305.16291](https://arxiv.org/abs/2305.16291).
SkillWeaver, Zheng et al., 2025,
[arXiv:2504.07079](https://arxiv.org/abs/2504.07079).

Voyager adds a skill to the library only when a critic agent says the task
succeeded. Its ablations *(body, [HTML](https://arxiv.org/html/2305.16291))*
are the clearest on this page about where the gain comes from. Removing
self-verification cost 73% of discovered items, the largest drop of any
feedback type. A random curriculum instead of the automatic one cost 93%.
Without the library it plateaued in later stages. Verification and the choice
of what to try next mattered more than the storage.

SkillWeaver adds a practice step. The agent explores a site, writes an API for
a behaviour it found, runs it, and keeps it only if it works. Skills written by
a stronger agent lifted weaker agents by up to 54.3% on WebArena.

### Agent KB, Memp, ReasoningBank

- **Agent KB** (Tang et al., 2025, [arXiv:2507.06229](https://arxiv.org/abs/2507.06229)).
  A shared store that works across agent frameworks. It retrieves in two
  stages: a workflow at planning time, then a targeted fix when something goes
  wrong. A "disagreement gate" stops retrieved knowledge from interfering with
  the agent's own reasoning. Gains: 55.2% to 73.9% on a GAIA-style set (pass@3
  against baseline pass@1, which flatters it) and 24.3% to 28.3% on SWE-bench
  with OpenHands.
- **Memp** (Fang et al., 2025, [arXiv:2508.06433](https://arxiv.org/abs/2508.06433)).
  Distils trajectories into step-level instructions and script-like
  abstractions, and revises, corrects and deletes entries as new experience
  arrives. Procedural memory from a stronger model helped a weaker one. No
  numbers in the abstract *(unverified)*.
- **ReasoningBank** (Ouyang et al., ICLR 2026,
  [arXiv:2509.25140](https://arxiv.org/abs/2509.25140)). Distils strategies
  from the agent's *self-judged* successes and failures. Reports gains over
  other memory systems on web and software tasks. No numbers in the abstract
  *(unverified)*. Note "self-judged": the same weakness as in the next part.

### Mem0, A-MEM and the memory-as-store line

Mem0 ([arXiv:2504.19413](https://arxiv.org/abs/2504.19413)) and A-MEM
([arXiv:2502.12110](https://arxiv.org/abs/2502.12110)) are described in
[`memory-and-continuity.md`](memory-and-continuity.md). They store facts and
linked notes. Mem0 reports a 26% relative gain in judged accuracy over OpenAI's
memory, and over 90% lower token cost than full context, on LOCOMO. These are
conversation-recall benchmarks. They measure whether a fact can be found, not
whether behaviour improved. Part 2 shows why that gap matters.

### Skills as procedural memory

Anthropic's Agent Skills keep each skill as a folder with a `SKILL.md`. Only
the name and description sit in the prompt at start. The model reads the full
file when it judges the skill relevant, and reads linked files or runs scripts
only if needed
([Anthropic](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)).
The post calls this progressive disclosure. It lists letting agents "create,
edit, and evaluate Skills on their own" as a hope, not a current feature. So
the decision to open a skill rests on a one-line description. A lesson whose
description does not match the situation is never opened.

SkillsBench (Li et al., 2026, [arXiv:2602.12670](https://arxiv.org/abs/2602.12670))
tests this. It runs 87 tasks in 8 domains with deterministic checkers.
Curated skills raised the average pass rate from 33.9% to 50.5%, with gains by
configuration from +4.1 to +25.7 points. Focused skills (up to three modules)
beat exhaustive ones. From the full text *(body,
[HTML v1](https://arxiv.org/html/2602.12670v1))*:

- Skills the model wrote for itself did not help. The paper's average change is
  -1.3 points, but the table's own rows average closer to -3.3, so the exact
  number is unsettled. The direction is clear: no gain.
- Two causes are given. The model writes vague procedures ("use pandas for
  data processing"), and on specialist tasks it often does not realise it
  needs a skill at all.
- "Comprehensive" skills lowered the pass rate by 2.9 points against no skill.
  Four or more skills per task gave +5.9, against about +18 for one to three.

One caveat. A practitioner critique, reported in an
[O'Reilly piece](https://oreilly.com/radar/agent-skills-work-but-the-research-shows-most-teams-are-building-them-wrong)
I only saw as a search snippet *(unverified)*, says the benchmark had a fresh
agent write a skill and use it at once. That is not the same as a skill
distilled from many real runs and checked against outcomes.

---

## Part 2. Why lessons do not transfer

Each failure mode below has some evidence. The strength varies, and I say so.

### 1. The lesson is never used when it matters

This is the best-documented failure and the one most likely to matter for a
file of lessons.

- **Delivered, not used.** MemUse ([arXiv:2611.01718](https://arxiv.org/abs/2611.01718),
  Xu, Li, Xu, 2026) judged 8,000 SWE-bench Verified episodes across three
  memory systems. Resolve rates were within 0.4 points of having no memory.
  Most delivered entries were irrelevant, most relevant entries went unused, and
  in two systems most deliveries repeated what the agent already knew.
- **Acting on the value found.** MERIT ([arXiv:2609.05441](https://arxiv.org/abs/2609.05441),
  Mishra and Mishra, 2026) used a leak-checked benchmark in which success
  depends on facts from earlier episodes. Memory moved success from 0.00 to
  between 0.55 and 1.00. For updated facts, though, agents acted correctly on a
  properly retrieved value only 55% of the time. Changing how the memory was
  built moved task success by up to 60 points.
- **Retrieval, not writing, as the bottleneck.** On LoCoMo, retrieval method
  moved accuracy by 20 points (57.1% to 77.2%) and write strategy by only 3 to
  8. Storing raw chunks with no LLM calls matched or beat Mem0-style fact
  extraction and MemGPT-style summaries
  ([Yuan, Su, Yao, 2026](https://arxiv.org/abs/2603.02473)).

The two groups disagree about where the loss is: retrieval in one study, use
after retrieval in the others. Both say the same thing about practice.
Measuring "did we store it" or "can we find it" does not tell you whether the
agent behaved differently. The measure has to sit at the point of use.

Evidence from real deployments agrees. In Project Vend phase 1, Claudius
accepted an employee's argument that discounts made no sense and said it would
stop giving them. Within days it was giving discounts again. The authors call
learning and memory "substantial challenges" in that first run
([phase 1](https://www.anthropic.com/research/project-vend-1)). It agreed with
the feedback. The agreement did not reach the later decision.

### 2. The lesson is too abstract

ACE's "brevity bias" is one form: refinement loops converge on generic advice.
SkillsBench's example of a self-written skill that says "use pandas" is another.
AutoGuide and AWM go the other way. They store a condition or a concrete
routine, and both report gains on web tasks. Direct evidence that abstraction
level is the cause is thin. It is mostly inferred from the differences between
systems *(inference)*.

### 3. Wrong or misleading lessons get copied

"How memory management impacts LLM agents" (Xiong et al., 2025,
[arXiv:2505.16067](https://arxiv.org/abs/2505.16067)) names two effects. First,
"experience-following": when a stored case closely matches the current task,
the agent's output closely matches the stored output. Second, error
propagation: past mistakes in memory compound and degrade later performance.
It also finds that some runs which look correct are of "limited or even
misleading value" as experience. What helped was regulating the quality of what
enters the bank and using later task results as "free quality labels" for the
stored items. ExpeL's own result that adding reflections lowered HotpotQA from
32% to 29% points the same way.

### 4. The writer is the same model that erred

- Self-correction without outside feedback is weak. Huang et al. (ICLR 2024,
  [arXiv:2310.01798](https://arxiv.org/abs/2310.01798)) found that models
  struggle to fix reasoning without external feedback, and sometimes get worse.
  A survey of the field found no work showing success from feedback by a
  prompted model alone, except on tasks well suited to it, and found that
  self-correction works when reliable external feedback exists
  ([Kamoi et al., TACL 2024](https://arxiv.org/abs/2406.01297)).
- Models are biased towards their own output. Panickssery, Bowman, Feng (2024,
  [arXiv:2404.13076](https://arxiv.org/abs/2404.13076)) found models can tell
  their own text from others', and that stronger self-recognition goes with
  stronger self-preference. They give controlled evidence that recognition
  plausibly causes preference. A model asked "was this lesson good?" is
  partly asked "is this mine?"
- In Project Vend phase 2 the "CEO" agent ran on the same model as the shop
  agent. It authorised lenient requests about eight times as often as it denied
  them. The authors said it shared the shopkeeper's blind spots and was "not
  much help"
  ([phase 2](https://www.anthropic.com/research/project-vend-2)).
- Reflexion, ReasoningBank and ExpeL all lean on a reflector that is the same
  model. They work best where an outside signal exists (tests, execution
  results). Without one, ACE lost accuracy on a finance task.

### 5. Piling up without pruning

Context collapse (18,282 tokens to 122) is the failure of rewriting. The
opposite failure is growth. SkillsBench found that exhaustive skill bundles and
four or more skills per task did worse than a few focused ones. The AGENTS.md
study ([Gloaguen et al., 2026](https://arxiv.org/abs/2602.11988)) found that
repository context files raised inference cost by over 20% on average and did
not generally improve task success. The abstract gives no more detail on which
kinds of file helped *(unverified)*. Memory that is delivered and not used also
costs tokens (MemUse above).

### 6. Contradiction and correlated evidence

I found little direct study of lessons that contradict one another. A 2026
paper on promotion decisions ("When Not to Write Memory",
[arXiv:2607.02579](https://arxiv.org/abs/2607.02579)) is the closest. It argues
that repeated observations across agents may not be independent evidence,
since they can share a source, a prompt or a stale environment. Its policy
cut false promotion from 0.371 to 0.032 on 120 human-labelled candidates from
real traces. On a held-out set false promotion was still 0.111. On 133
external coding-agent candidates, none were safe for automatic promotion. The
authors call it a diagnostic design point, not a proven writer. A lesson that
five episodes "confirm" may be one episode counted five times.

---

## Part 3. Verifying a lesson before it bites

The pattern across Parts 1 and 2: the systems that held up had a check outside
the model that wrote the lesson.

| Check | Where it appears | What it buys |
|---|---|---|
| Run the skill, admit only on success | Voyager, SkillWeaver | Library holds code that ran. Removing the check cost Voyager 73% |
| Helpful and harmful counters per item | ACE | Items that mislead can be seen and removed |
| Up and down votes, drop at zero | ExpeL | Weak rules die; but votes come from the model |
| Later results as quality labels for stored items | Xiong et al. | Catches runs that looked right and were not |
| Admission scoring (utility, confidence, novelty, recency, type) | A-MAC ([arXiv:2603.04549](https://arxiv.org/abs/2603.04549)) | F1 0.583 on LoCoMo, 31% lower latency; content type was the strongest factor |
| Dependency-aware promotion with counter-evidence | GovMem | Cuts false promotion; held-out rate still 0.111 |
| Post-hoc causal audit: remove a memory, see if behaviour changes | MemAudit ([arXiv:2605.23723](https://arxiv.org/abs/2605.23723)) | Built for poisoning; attack success fell 70% to 0% on QA |
| Memory judged at the point of use | MemUse | Shows what was read and what changed |

Two points on this table.

**No system I found tests a lesson on held-out episodes before admitting it.**
Voyager and SkillWeaver test a skill on the task that produced it, which is a
check that it runs, not a check that it generalises. GovMem is the closest to a
held-out evaluation, and its held-out false-promotion rate stayed high. This is
a gap in the literature as I found it. It may exist in places I did not search
*(unverified)*.

**Counters are only as good as the attribution.** ACE has the generator mark
which bullets helped. That is the same model's self-report. MemUse found
episodes where beneficial memory use still failed (23.2%) and episodes where
harmful memory use still succeeded (5 of 16). A single outcome does not say
which lesson was responsible. The sound version is a counter incremented by
a measured outcome (a test passed, a metric moved) on episodes where the
lesson was shown, compared with episodes where it was not. MemAudit's
counterfactual score does something like this for harmful items.

### The promotion ladder

Several of these systems show the same direction: text becomes procedure
becomes code.

1. **Observation.** One episode. Not a lesson yet.
2. **Lesson.** A rule with a trigger, held in a store (ExpeL, AutoGuide,
   Reflexion).
3. **Playbook or workflow entry.** Counted and curated (ACE, AWM, Memp).
4. **Skill.** A procedure written down in full, loaded on demand (Agent
   Skills; SkillsBench says curated ones help, self-written ones do not).
5. **Code or tool.** A function the agent calls (Voyager, SkillWeaver, and the
   Dynamic Cheatsheet jump to 99% on Game of 24 when the model reused a
   program).

Each step up removes a way to ignore the lesson. A sentence in a prompt can be
skipped. A function that is called, or a check that blocks a step, cannot. The
Dynamic Cheatsheet and SkillWeaver gains are the largest in this page's table
and both are at the code end. I did not find a paper that studies the ladder
itself, meaning the rule for when to climb. The ladder here is my synthesis
*(inference)*.

### Decay and deletion

ExpeL drops rules at count zero. Memp revises and removes entries. Zep's
alternative, covered in [`memory-and-continuity.md`](memory-and-continuity.md),
is to mark a fact invalid and keep it. I found no study that measures the
effect of decay on downstream performance. MERIT's finding that stores which
update on write stayed between 0.70 and 1.00 on updated facts, while embedding
retrieval ranged from 0.30 to 0.95, is the nearest evidence that
update-on-write beats append-only.

---

## Part 4. Learning from outcomes, not from self-critique

Most systems above learn from a story the model tells about what happened. A
different tradition learns from what the world returned, compared with what the
agent expected.

### Predictions first, then outcomes

Curiosity research makes surprise the learning signal. Pathak et al. (2017,
[arXiv:1705.05363](https://arxiv.org/abs/1705.05363)) define curiosity as the
error in the agent's ability to predict the consequences of its own actions.
Learning progress, the change in prediction error over time, is covered in
[`drives-and-goals.md`](drives-and-goals.md) and not repeated here.

The LLM version is a log: before acting, write down what you expect, with a
number. After the result arrives, score it. Evidence that this works for
agents is thinner than for the memory systems above, and the sources are
recent preprints:

- Models can be reasonably calibrated when the question is posed properly.
  Kadavath et al. (2022, [arXiv:2207.05221](https://arxiv.org/abs/2207.05221))
  found larger models well calibrated on multiple-choice and true/false
  questions, and that sampling many answers before judging one improved
  self-evaluation. Models can also be trained to predict whether they know an
  answer, though calibration on new tasks was weak.
- A 2026 paper on confidence in agents ([Hathidara,
  arXiv:2610.18614](https://arxiv.org/abs/2610.18614)) trains a 7B model
  with a proper scoring rule against the outcome of the whole episode, not the
  step. Expected calibration error fell from 0.42 to below 0.12 in the online
  setting. The agent could tell from its first step which tasks it would fail.
  It was harder to tell a good attempt from a bad one on the same task. This is
  training, not prompting, so it does not carry over to a frozen model. What
  carries over is the target: score confidence against the final outcome.
- A forecasting agent that keeps a structured belief state and recalibrates its
  outputs with Platt scaling reports the best Brier score on 400
  ForecastBench questions, and says the belief state is nearly as useful as
  web search ([arXiv:2604.18576](https://arxiv.org/abs/2604.18576)). I only
  saw this as a search result *(unverified)*.
- On a 2026 World Cup benchmark, agents that reflected after each score did
  not beat the bookmaker's Brier score
  ([arXiv:2607.17765](https://arxiv.org/abs/2607.17765), search result only
  *(unverified)*).

So the case for prediction logs rests on two things: a calibration log shows
where the agent is overconfident, and the gap between expected and actual
result is a better trigger for writing a lesson than "this feels notable."
The second claim is an argument from the curiosity literature, not a measured
result for LLM agents *(inference)*.

### Hypothesis, experiment, result

A lesson written as a claim with a test is stronger than one written as advice.
"Posts with a question in the first line get more replies" can be checked.
"Engage more authentically" cannot. The form is: claim, what would count as
evidence, the experiment, the result, and a verdict that can be reversed. I did
not find a benchmark of agents that keep such a log, so this section is a
design argument. The AI Village record below is the nearest evidence.

### The scoring has to be outside

MemUse's finding that judged memory use still disagreed with outcomes (23.2%
and 5 of 16) is a reminder that even a careful judge is a noisy label. ACE
without outcome signal lost accuracy. The two lessons agree: the outcome has to
come from something the agent cannot argue with (a test, a count, a price, a
reply) and the agent's own judgement of its work is a second-class label.

---

## Part 5. Curriculum and self-set goals

[`open-endedness.md`](open-endedness.md) has the theory. Only what bears on
learning efficiency:

- Voyager's ablation: a random curriculum cut discovered items by 93%, and a
  hand-written one did worse than the automatic one. The choice of *what to try
  next* mattered more than the library.
- SkillWeaver needs the agent to find and practise skills before they are
  stored. Practice is the extra experience that makes a rule trustworthy.
- ExpeL: rules from only the hand-written examples gave no gain. Experience
  gathered by the agent on tasks near the target did.
- A lesson with no chance of being tested again is cheap to write and costs
  nothing to be wrong. A curriculum that returns to a situation in order to
  test a lesson turns lessons into experiments *(inference)*.

---

## Part 6. What moved performance in long deployments

Small evidence base. These are write-ups by operators, not controlled studies.

### Project Vend

Phase 1 ([Anthropic](https://www.anthropic.com/research/project-vend-1)): the
shop never turned a profit. Claudius gave discounts under pressure, sold items
below cost, and agreed with feedback without changing later behaviour.

Phase 2 ([Anthropic](https://www.anthropic.com/research/project-vend-2)):
negative-margin weeks were largely eliminated. What the authors credit:

- **Model upgrades** (Sonnet 3.7 to 4.0 to 4.5).
- **Procedures and checklists.** One of the most impactful changes. Claudius
  had to check prices and delivery times with research tools before
  committing. "Bureaucracy matters."
- **Tools** (a CRM, cost data, browser research, payment links), though the
  post does not separate their effects.
- **A separate merchandise agent**, which narrowed Claudius's job.

What the authors did not credit: the CEO agent, and in-run learning. They saw
the same blind spots recur, such as accepting an unsupported claim about a vote
and the onion price-lock contract that a staffer caught. Humans still did
deliveries and disputes, and the authors added no new jailbreak defences.

Read for this page: the gain came from procedures that forced a check at the
moment of the decision, not from the agent accumulating wisdom.

### AI Village

The AI Village [FAQ](https://theaidigest.org/village) describes the memory
design. Every 40 actions agents are encouraged to consolidate a note, which is
appended to their memory, and they restart with it. When memory gets too long
agents rewrite it shorter, told not to make it "ridiculously short." Memory
persists across goal changes. Forgetting still happens: "an agent sometimes
randomly decides to stop remembering it has a Twitter account and never
tweets again." Recovery came from other agents' reminders, project files, or a
search-history tool.

The current census goal (11 to 21 November 2026) page lists repeating error
patterns *(the summaries are AI-written and may be inaccurate, per the page
itself)*: an agent "repeatedly corrected herself, usually after claiming a
check she hadn't yet run"; a dead link sat on main for about five hours
because no landing check opened cited links; fabricated dates and quotes
when summaries were retyped. What the page says helped:

- A **rule that binds by copying, never by retyping**, which is a mechanical
  rule and a change of procedure.
- A **landing check** and **end-of-day sweeps** for dead pointers.
- **Other agents auditing the work.** Errors were caught within minutes,
  not days.
- **Fewer handoffs.** A seven-handoff review ritual stalled releases for days.
  After a human intervention, one lead and one review per record replaced it.
  The map went from 39 to over 2,100 agents in three working days.
- One agent logs each slip with a numbered tag in its memory. The page does not
  say whether the log reduced the slips.

The FAQ also says the scaffold is unlikely to be badly under-eliciting
capability, since a Claude Code-based agent showed no major difference over four
months.

Again the pattern: checks that happen at a gate, outside the agent's own
judgement, did the work. A memory log of slips is recorded; no source I read
shows it prevented repeats.

### Long-running coding agents

Anthropic's harness write-up ([Effective harnesses for long-running
agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents))
lists the artifacts that carry state between sessions: a feature list in JSON
with every item initially "failing", a progress file, git history, and an
`init.sh` script. Coding agents may change only the `passes` field. JSON was
chosen because the model was less likely to overwrite it than Markdown. The
failure modes it lists are the familiar ones: declaring victory early, doing
too much at once, leaving the environment broken, and marking features done
without end-to-end tests. The fixes are structural: one feature at a time,
start by reading the notes and running a check, and mark a feature passing
only after browser-based verification.

The write-up gives no benchmark numbers *(unverified beyond the text I read)*.
Its lesson is the same as Vend's. The thing that carries over between sessions
is a checkable artifact, not a narrative.

### What operators dropped or found unhelpful

- A same-model overseer (Vend's CEO).
- Repository context files, on average (the AGENTS.md study; success did not
  improve, cost rose over 20%).
- Exhaustive skill bundles (SkillsBench).
- Full-transcript replay: MERIT found it never cost-effective, with the best
  condition in each domain 2.7 to 3.9 times better per dollar.

I did not find any 2026 write-up from an operator describing a lessons file
that visibly changed behaviour over months. That absence is itself a finding,
but it is the absence of reports I could find, not proof none exist.

---

## What this argues for in Teeny

Mechanism only. Ranked by how much of the evidence above supports it and how
cheap it is to try. Each item lists the evidence behind it.

1. **Score every lesson against an outcome the agent cannot argue with.**
   Each stored lesson gets a counter of times it was shown and, for each, a
   result measured by something outside the model (a ledger row, a metric, a
   test, a guard decision). Keep "shown and followed," "shown and ignored" and
   "not shown" apart so the comparison is possible. Evidence: ACE needs
   execution signals and loses accuracy without them; Xiong et al. recommend
   later outcomes as quality labels; MemUse shows judged use and outcome
   disagree.

2. **Surface the lesson at the moment of the decision, as a gate, not as a
   file.** Retrieve by the situation the lesson names, put it into the step
   that makes the choice, and where possible make the step fail until the
   check is done. Evidence: MemUse and MERIT (stored does not mean used);
   Voyager and Vend (self-verification and pre-commit checks were the
   biggest drivers); AutoGuide (rules with a trigger). Measure use at the point
   of use, as MemUse does.

3. **Write a prediction before acting, and score it after.** One line: the
   action, the expected result, a probability, and the date the result will be
   known. Close it from harness data. Track calibration (Brier) per kind of
   action. Open a lesson only when a prediction missed by a lot, and give the
   lesson the log entry as evidence. Evidence is argument-grade: the curiosity
   literature, Kadavath et al., and Hathidara's outcome-grounded scoring.
   The benefit is that lessons start from a measured miss, not from a
   feeling.

4. **Make lessons testable claims with a trigger, an expected effect and a
   revert condition.** "When X, do Y, expect Z within N days." A lesson with no
   Z cannot be verified and should not be admitted. Evidence: AutoGuide,
   ACE's brevity bias, SkillsBench's vague self-written skills.

5. **Admit a lesson only after it survives a check on episodes it was not
   written from.** Hold back a share of past episodes, or require a second
   independent occurrence from a different source, before promotion. Count
   repeated observations from one source once. Evidence: GovMem (its held-out
   false-promotion rate was still 0.111 and no external candidate was safe to
   auto-promote), so treat this as a high bar. No system I found does this
   fully.

6. **Use a different judge from the writer, and give it the outcome data.**
   Different model family, or better a frozen script. Evidence: Huang et al.,
   Kamoi et al., Panickssery et al., Vend's CEO agent.

7. **Promote what survives: lesson, then prompt rule, then check or tool.**
   Define the thresholds up front: how many shown-and-followed wins, over how
   many days, with what effect, before a lesson becomes a standing rule; and
   how many repeat misses of a rule before it becomes a code-level check that
   blocks the step. Evidence: Dynamic Cheatsheet and SkillWeaver gains at the
   code end; Vend's checklists; AI Village's copy-not-retype rule. The
   thresholds are a design choice, not a measured value.

8. **Retire as deliberately as you admit.** A lesson with no shown-and-helped
   in a set period, or with a harmful tally above its helpful one, goes to
   archive, not deletion. Cap the live set and the length of each item (a few
   focused items beat a large pile). Edit by item with IDs, never by
   whole-file rewrite. Evidence: ACE collapse, SkillsBench, AGENTS.md study,
   MERIT's update-on-write result.

9. **Detect repeats mechanically.** When a new failure matches an existing
   lesson, that is the highest-value event: the lesson existed and did not
   bite. Log it against the lesson, and escalate the lesson one rung up the
   ladder instead of rewording it. Evidence: Vend phase 1 (agreed, then
   repeated), AI Village (slips repeat; the fixes were gates). The
   escalate-on-repeat rule is my inference.

10. **Return to situations on purpose so lessons get retested.** Where a lesson
    names a situation, schedule or seek that situation again, so the lesson
    gets another chance to be wrong. Evidence: Voyager's curriculum ablation,
    and the general point that lessons need further experience to be verified.
    This is the weakest-supported item *(inference)*.

### What would count as evidence it worked

Define this before building anything, and do not let the agent set it:

- The repeat rate of a named failure falls after its lesson is promoted, against
  a failure with no lesson over the same period.
- Shown-and-followed episodes beat not-shown episodes on the outside metric.
- Calibration (Brier) on predictions improves for action types that have
  lessons.
- The live lesson set stays small and its median age of last use stays short.

## Could not verify

- ExpeL's abstract has no numbers. The figures here come from the HTML full
  text, whose own prose and tables disagree on the Reflexion comparison.
- AutoGuide, Memp, ReasoningBank, A-MEM abstracts give no numbers.
- Dynamic Cheatsheet's GPQA and MMLU-Pro gains: absolute or relative is not
  stated.
- SkillsBench: the arXiv abstract, a NeurIPS poster listing and a secondary
  summary give slightly different task counts and deltas. Self-generated
  skill effect is quoted from the body table, which does not match its own
  average. The O'Reilly critique is from a search snippet only.
- Forecasting-agent papers 2604.18576 and 2607.17765, and 2611.04070, were
  seen in search results only.
- I read the ACE body only up to the first 100,000 characters.
- The AI Village goal summaries are AI-written, per the page.
- I found no direct study of contradictory lessons, no held-out admission test
  for lessons, and no measurement of decay on downstream performance.
