# Evolution loop engineering

How the working self-improvement loops are built, one level below the papers.
[`self-improving-agents.md`](self-improving-agents.md) and
[`open-endedness.md`](open-endedness.md) say what each system is. This page
says what the code does: what the proposer is shown, how a parent is picked,
how a candidate is scored, what is kept, and what the candidate cannot touch.

Everything below was read in the open repositories on 2026-11-22 (shallow
clones of the default branch, read only, never run). File links point at the
default branch, so line numbers will drift. Where a claim comes from a paper
or a web page and not from code, it says so. Where I could not check, it says
*(unverified)*. Defaults quoted are the values in each repo's config at that
date.

## Contents

1. [The shared skeleton](#the-shared-skeleton)
2. [System by system](#system-by-system)
3. [Comparison table](#comparison-table)
4. [Patterns that recur in every loop that worked](#patterns-that-recur-in-every-loop-that-worked)
5. [Things only one system does](#things-only-one-system-does)
6. [A reference design for a slow-feedback loop](#a-reference-design-for-a-slow-feedback-loop)
7. [What I could not verify](#what-i-could-not-verify)

---

## The shared skeleton

Every loop below is the same five steps. The systems differ in how much
engineering sits in each step.

```
pick a parent  ->  show the proposer context  ->  proposer writes a child
      ^                                                     |
      |                                                     v
 update archive  <-  score the child (cheap stage, then more)
```

Two splits matter more than any other detail:

- **The proposer and the diagnoser are often different calls.** DGM first runs
  a model that reads the failure and writes one problem statement. A second
  agent then implements it. GEPA, the AI Scientist and Shinka also separate
  "read the failures" from "write the change".
- **The scorer lives outside the candidate.** Either a frozen benchmark
  harness, a held-out file the candidate cannot edit, or a different process.

---

## System by system

### Darwin Gödel Machine (DGM)

Code: [jennyzzt/dgm](https://github.com/jennyzzt/dgm) ·
[`DGM_outer.py`](https://github.com/jennyzzt/dgm/blob/main/DGM_outer.py),
[`self_improve_step.py`](https://github.com/jennyzzt/dgm/blob/main/self_improve_step.py),
[`prompts/self_improvement_prompt.py`](https://github.com/jennyzzt/dgm/blob/main/prompts/self_improvement_prompt.py),
[`prompts/diagnose_improvement_prompt.py`](https://github.com/jennyzzt/dgm/blob/main/prompts/diagnose_improvement_prompt.py).
Paper: [arXiv:2505.22954](https://arxiv.org/abs/2505.22954).

- **Unit of change.** A diff to the coding agent's own repo (`coding_agent.py`
  and `tools/`). A child is stored as `model_patch.diff`. To rebuild a node,
  `self_improve()` applies the whole chain of ancestor patches in order inside
  a fresh container.
- **What the proposer sees.** Not the scores. The coding agent receives a
  *problem statement* written as a GitHub issue. That statement comes from a
  separate diagnosis call (`diagnose_problem`, default model `o1-2024-12-17`)
  which reads the parent's run log, the task, the parent's predicted patch,
  the private test patch and the test results. It must return JSON with
  `log_summarization`, `potential_improvements`, **one** chosen
  `improvement_proposal`, an `implementation_suggestion`, and the
  `problem_description`. The prompt tells it to favour general abilities over
  fixes for one repo. The diagnoser sees the private tests. The implementer
  does not.
- **Targeted modes.** The outer loop sometimes skips the free-form diagnosis.
  With 25% chance each, subject to conditions, it hands the agent a fixed
  prompt: `solve_empty_patches` (only if at least 10% of the parent's tasks
  gave empty patches), `solve_stochasticity`, or `solve_contextlength` (only
  if the parent's logs show the context-overflow error twice in a row). These
  are hand-written diagnoses of failure classes the authors saw.
- **Parent selection.** Default `score_child_prop`. For each archive member
  with a valid eval: `s = 1/(1+exp(-10*(acc-0.5)))`, `h = 1/(1+children)`,
  weight `s*h`, normalise, sample with replacement
  (`choose_selfimproves` in `DGM_outer.py`). Fixed sigmoid midpoint of 0.5,
  slope 10. Other modes: `random`, `score_prop` (no child penalty), `best`.
  *(Reading note: the `best` branch sorts ascending and takes the head, so it
  looks like it selects the lowest scores. I did not run it.)*
- **Per generation.** `selfimprove_size=2` children, two workers,
  `max_generation=80`, 30-minute timeout on the self-edit and 1.5 hours per
  attempt.
- **Evaluation.** Staged. Every child runs the `small` task subset. Only if it
  resolves at least 40% of that (`test_more_threshold = 0.4`) does it run the
  `medium` subset. A "full evaluation" threshold is the second-highest score
  in the archive, floored at 0.4 (`get_full_eval_threshold`).
- **Noise.** Handled mostly by staging and by the archive being wide.
  `--num_swe_evals` can repeat evaluations. `eval_noise=0.1` is only used by
  the optional `keep_better` archive rule, which keeps a child that scores
  within 0.1 of the initial agent.
- **Archive.** Default `keep_all`: every child that compiled and produced at
  least one non-empty patch (`is_compiled_self_improve`) is added, however it
  scored. Worse nodes stay and can be parents later. State is appended to
  `dgm_metadata.jsonl` each generation, so a run can resume.
- **Post-hoc diagnosis.** Optional `--post_improve_diagnose` asks the diagnoser
  to compare before and after logs for the task and return `impact`,
  `improvements`, `regressions` and a score from -2 to 2.
- **Guardrails.** Each self-edit runs in a Docker container. Only API keys are
  passed in as environment variables. The harness and scoring run outside the
  agent's container. The README warns that it executes untrusted model code and
  gives no stronger guarantee. The paper's hallucination-marker tampering
  story is in the other page.
- **Meta-level.** None. The diagnosis prompts, the selection formula and the
  eval harness are fixed code.

### Huxley-Gödel Machine (HGM)

Code: [metauto-ai/HGM](https://github.com/metauto-ai/HGM) ·
[`hgm.py`](https://github.com/metauto-ai/HGM/blob/main/hgm.py),
[`tree.py`](https://github.com/metauto-ai/HGM/blob/main/tree.py),
[`config.yaml`](https://github.com/metauto-ai/HGM/blob/main/config.yaml).
Paper: [arXiv:2510.21614](https://arxiv.org/abs/2510.21614). Built on the DGM
code, so the diagnose and implement steps are inherited.

- **Parent selection is Thompson sampling.** Each node keeps a list of 0/1
  task outcomes. `TS_sample` draws `theta ~ Beta(1 + successes, 1 + failures)`
  per candidate and takes the argmax. When choosing a node to *expand*, the
  outcomes used are the node's **whole subtree** (`get_decendant_evals`): its
  own results plus all descendants'. A node with fewer than
  `n_pseudo_descendant_evals` (10,000) own evals is padded with pseudo-evals
  at its mean. So a parent is credited for what its children achieved.
- **Expand or evaluate?** The loop is asynchronous (20 workers). Each worker
  call decides: if `n_task_evals ** alpha >= number_of_nodes + pending_expands`
  (`alpha = 0.6`), create a child. Otherwise pick a node by Thompson sampling
  over its own outcomes and run **one more task** on it. Evaluation is one task
  per call, so a new node starts with almost no evidence and earns more evals
  only if it keeps winning draws.
- **Budget.** `max_task_evals: 800` in total, not per child. An optional
  cool-down scales the Beta parameters up over time so sampling sharpens
  toward exploitation.
- **Archive.** The tree itself. Nodes with no positive mean utility are not
  expandable (`mean_utility > 0` filter), but stay in the tree.
- **Reflection and guardrails.** Inherited from DGM: container, diagnosis model
  (here `gpt-5-mini` in `config.yaml`), same warning in the README.
- **Meta-level.** None in the code read.

### Hyperagents (DGM-H)

Code: [facebookresearch/HyperAgents](https://github.com/facebookresearch/HyperAgents) ·
[`meta_agent.py`](https://github.com/facebookresearch/HyperAgents/blob/main/meta_agent.py),
[`generate_loop.py`](https://github.com/facebookresearch/HyperAgents/blob/main/generate_loop.py),
[`utils/gl_utils.py`](https://github.com/facebookresearch/HyperAgents/blob/main/utils/gl_utils.py),
[`select_next_parent.py`](https://github.com/facebookresearch/HyperAgents/blob/main/select_next_parent.py).
Paper: [arXiv:2603.19461](https://arxiv.org/abs/2603.19461).

- **Mutation prompt.** Almost nothing. `MetaAgent.forward` sends the instruction
  "Modify any part of the codebase at `{repo_path}`", with the path to the
  archive of previous agents and their results (`eval_path`) and, optionally,
  the number of iterations left. All the intelligence is in what the agent does
  with tools, not in a prompt template.
- **Parent selection.** `select_parent` in `gl_utils.py`. Modes `random`,
  `latest`, `best`, `score_prop`, `score_child_prop` (CLI default). Sigmoid with
  an adaptive midpoint: `1/(1+exp(-10*(score - mean(top 3 scores))))`. The
  child penalty is `exp(-(children/8)^3)`, which is nearly flat up to about
  five children and then drops fast. Scores are averaged across domains.
- **Evaluation.** Staged: a small sample first, then a full run if the small
  score passes `test_more_threshold` (`generate_loop.py`, `run_full_eval`
  metadata key).
- **Meta-level.** The flag `edit_select_parent` is the interesting part. When
  set, the next parent is chosen by running `utils.run_select_next_parent`
  *inside a container with the lineage's own patches applied*
  (`select_next_parent_container`). So the selection code is part of what the
  agent can rewrite. The copy of `select_next_parent.py` shipped at the repo
  root picks uniformly at random among valid parents. Flags also exist to
  reset the task-agent or meta-agent edits, for transfer experiments. A
  failed selector marks the node `can_select_next_parent: False` and falls back
  to an earlier node's selector, retrying up to 10 times.
- **Guardrails.** Docker per generation, a one-hour timeout on selection, and
  a README warning. Whether an edited selector can reach the scores is not
  fenced in code I read.

### OpenEvolve

Code: [codelion/openevolve](https://github.com/codelion/openevolve) (the
other page links the `algorithmicsuperintelligence` org; I read the
`codelion` clone) ·
[`openevolve/database.py`](https://github.com/codelion/openevolve/blob/main/openevolve/database.py),
[`openevolve/config.py`](https://github.com/codelion/openevolve/blob/main/openevolve/config.py),
[`openevolve/prompts/defaults/`](https://github.com/codelion/openevolve/tree/main/openevolve/prompts/defaults),
[`openevolve/evaluator.py`](https://github.com/codelion/openevolve/blob/main/openevolve/evaluator.py).

- **Unit of change.** One file of code, edited by `SEARCH/REPLACE` diffs
  (`diff_user.txt`), or a full rewrite with a configurable probability
  (`full_rewrite_probability`). The user marks the block to evolve in the file.
- **What the proposer sees** (`diff_user.txt`, `evolution_history.txt`):
  the parent's fitness and its MAP-Elites feature coordinates; "focus areas";
  any **artifacts** from the parent's evaluation (stderr, timeouts, the
  evaluator's own text, if `enable_artifacts`); a section of *Previous
  Attempts* (changes, metrics, outcome); *Top Performing Programs* (default
  3); *Inspirations* (diverse programs, default 2); then the parent code and
  the diff format. The instruction ends with: "Different solutions with
  similar fitness but different features are valuable." Optional
  `use_template_stochasticity` randomises the phrasing.
- **Parent selection** (`_sample_parent`): with probability
  `exploration_ratio=0.2` a random member of the current island; with
  `exploitation_ratio=0.7` a random member of the elite **archive**
  (`archive_size=100`); the remaining 10% a random program from anywhere.
  There is no score weighting inside the draw. Selection pressure comes from
  who gets into the archive.
- **Islands.** `num_islands=5`, evolved separately. `migration_interval=50`
  generations, `migration_rate=0.1`. Inspirations are drawn only from the
  parent's island, to keep islands apart.
- **MAP-Elites.** `feature_dimensions` default to complexity (code length) and
  diversity, `feature_bins=10`. Any metric the evaluator returns can be a
  dimension. One cell holds one program; a better program in the same cell
  replaces the occupant. A cell that scores lower is never a parent unless it
  is also in the archive.
- **Evaluation.** `cascade_evaluation: true` with `cascade_thresholds`
  `[0.5, 0.75, 0.9]`. The user's `evaluate_stage1` is a quick sanity check; a
  candidate must clear the threshold to reach `evaluate_stage2`, then stage 3.
  A timeout is recorded as a result and returned to the proposer as an
  artifact. There is an optional LLM-feedback score
  (`use_llm_feedback`, weight 0.1).
- **Noise.** Nothing specific found in the code read. A deterministic
  evaluator is assumed. A fixed `random_seed` of 42 is the default.
- **Scale.** `max_iterations=10000`, `population_size=1000` as defaults. These
  are ceilings, not what any one task needs.
- **Guardrails.** Candidates run in a worker process with a timeout. I found
  no sandbox or evaluator-integrity check in the core. The evaluator is a
  separate file the user writes. (The Codex provider note in
  `docs/llm-providers.md` sets a read-only sandbox for *that LLM provider*,
  which is unrelated to candidate code.)
- **Meta-level.** Not by default. A `population_strategy` hook lets users
  replace the archive-update rule. A `novelty_judge` can ask an LLM if two
  programs differ in kind.

### ShinkaEvolve

Code: [SakanaAI/ShinkaEvolve](https://github.com/SakanaAI/ShinkaEvolve) ·
[`shinka/database/parents.py`](https://github.com/SakanaAI/ShinkaEvolve/blob/main/shinka/database/parents.py),
[`shinka/core/config.py`](https://github.com/SakanaAI/ShinkaEvolve/blob/main/shinka/core/config.py),
[`shinka/prompts/prompts_meta.py`](https://github.com/SakanaAI/ShinkaEvolve/blob/main/shinka/prompts/prompts_meta.py),
[`shinka/prompts/prompts_prompt_evo.py`](https://github.com/SakanaAI/ShinkaEvolve/blob/main/shinka/prompts/prompts_prompt_evo.py),
[`docs/core_concepts.md`](https://github.com/SakanaAI/ShinkaEvolve/blob/main/docs/core_concepts.md),
[`docs/bandit_selection.md`](https://github.com/SakanaAI/ShinkaEvolve/blob/main/docs/bandit_selection.md).
Paper: [arXiv:2509.19349](https://arxiv.org/abs/2509.19349).

A correction to the other page: the UCB bandit in the docs picks **which
mutation LLM** to call, not which parent. Parent selection is a separate rule.

- **Unit of change.** A program with marked mutable regions (`initial.py` plus
  an `evaluate.py` that returns `combined_score` and a `correct` flag).
  Patch types `diff`, `full`, `cross` with probabilities `[0.6, 0.3, 0.1]`.
  `cross` recombines with another program.
- **Parent selection.** Default `weighted` (`WeightedSamplingStrategy`):
  among correct archived programs on one island, take the median score
  `a0` and the median absolute deviation as scale, then
  `w = sigmoid(10 * (score - a0)/MAD) * 1/(1 + children)`. Same shape as DGM,
  but the midpoint and scale come from the current population rather than a
  fixed 0.5. Alternatives in the same file: `power_law` (rank-based, with a
  chance of drawing from the archive), `beam_search`, `tournament`,
  `best_of_n`, `sequential`. There is also a "fix mode": if a program is
  incorrect, the selector can pick it for a repair attempt.
- **Model bandit.** `llm_dynamic_selection="ucb"` runs `AsymmetricUCB` over the
  configured models. The score is `normalized_reward + c*sqrt(2 ln t / n)`
  blended with a cheapness score by `cost_aware_coef` (default 0.5), with
  epsilon-greedy tie handling (docs above). Reward is the observed
  improvement of children over parents for that model.
- **Islands.** `num_islands=2`, `migration_rate=0.0` by default (so none),
  `island_elitism=True`. Optional stagnation-based spawning of new islands
  after 100 generations without improvement.
- **Inspirations.** One archive inspiration and one top-k inspiration by
  default.
- **Novelty gate.** Before spending an eval, the child's code is embedded
  (`text-embedding-3-small`). If similarity to an existing program exceeds
  `code_embed_sim_threshold=0.99`, a judge LLM decides `NOVEL` or `NOT_NOVEL`
  (`prompts_novelty.py`), with up to 3 attempts. Trivial changes are listed as
  non-novel: renames, formatting, comments.
- **Meta-memory.** Every `meta_rec_interval=10` generations a three-step
  summariser (`prompts_meta.py`) writes (1) a summary per program (implementation,
  performance, feedback), (2) a "global insights scratchpad" with sections
  *Successful algorithmic patterns*, *Ineffective approaches*,
  *Implementation insights*, *Performance analysis*, merged with the previous
  scratchpad, and (3) up to 5 recommendations. One recommendation is sampled
  into the next mutation prompt (`sample_single_meta_rec`).
- **Feedback text.** With `use_text_feedback`, the evaluator's diagnostic text
  goes into the prompt next to the score.
- **Prompt co-evolution.** With `evolve_prompts`, the task system prompt is
  itself a population (`prompt_archive_size=10`), mutated by `diff` or `full`
  edits shown the current prompt and the top-3 programs it produced, and chosen
  by UCB with epsilon 0.1. This is the closest thing to Promptbreeder in the
  set of working loops.
- **Evaluation and budget.** One evaluator run per candidate, local or SLURM.
  `max_api_costs` stops the run. A proposal buffer keeps workers busy
  (`proposal_target_mode="adaptive"`). The paper reports a new best on
  circle packing within 150 samples (other page).
- **Guardrails.** Candidates run as separate jobs (local interpreter, conda or
  Docker on SLURM). Only the marked regions of the file are mutated; edits
  that break the markers are rejected (`edit/marker_validation.py`, not read
  in detail). No tamper check on the evaluator beyond it being a separate file.

### GEPA and DSPy's GEPA optimizer

Code: [gepa-ai/gepa](https://github.com/gepa-ai/gepa) ·
[`src/gepa/api.py`](https://github.com/gepa-ai/gepa/blob/main/src/gepa/api.py),
[`core/engine.py`](https://github.com/gepa-ai/gepa/blob/main/src/gepa/core/engine.py),
[`strategies/`](https://github.com/gepa-ai/gepa/tree/main/src/gepa/strategies),
[`gepa_utils.py`](https://github.com/gepa-ai/gepa/blob/main/src/gepa/gepa_utils.py),
[`proposer/merge.py`](https://github.com/gepa-ai/gepa/blob/main/src/gepa/proposer/merge.py).
Paper: [arXiv:2507.19457](https://arxiv.org/abs/2507.19457). DSPy ships a GEPA
optimizer that wraps this; I did not read the DSPy side *(unverified)*.

- **Unit of change.** A *candidate* is a dict of named text components (for
  example one instruction per module). A mutation edits **one** component.
  `module_selector="round_robin"` cycles through them; `"all"` edits all.
- **What the proposer sees.** `InstructionProposalSignature` in
  `strategies/instruction_proposal.py`. The prompt holds the current
  instruction in `<curr_param>` and, in `<side_info>`, a small set of examples
  with the inputs, the model's actual response, and **plain-language
  feedback** from the metric. The task says: infer the task format, read each
  response and its feedback, "identify all niche and domain specific factual
  information", include generalisable strategies, and write the new
  instruction. Context is the failing traces of the last few rollouts, not the
  score history.
- **Parent selection.** `candidate_selection_strategy="pareto"` by default
  (`gepa_utils.select_program_candidate_from_pareto_front`). For every
  validation example keep the set of candidates that achieved the best score
  on it. Remove dominated candidates. Count how many examples each survivor
  leads on. Sample a survivor **in proportion to that count**. So a candidate
  that is best on a hard niche example survives even if its average is low.
  Others: `current_best`, `epsilon_greedy`, `top-k pareto`. `frontier_type`
  can also be per objective, hybrid, or cartesian when the evaluator returns
  several objective scores.
- **Evaluation: minibatch then full.** Each iteration samples a minibatch of
  `reflection_minibatch_size` training examples (default 3, drawn by an
  epoch-shuffled sampler so every example gets used). Steps in
  `reflective_mutation.py` and `engine.py`:
  1. Evaluate the parent on the minibatch, **capturing trajectories**.
  2. If `skip_perfect_score` and every score is perfect, skip the example.
  3. Reflect and propose a child.
  4. Evaluate the child on the **same** minibatch.
  5. Accept only if the new minibatch sum is **strictly greater**
     (`StrictImprovementAcceptance`; a `>=` variant exists).
  6. Only accepted children get a **full validation-set evaluation** and join
     the pool and the Pareto tables.
  A rejected child costs a few rollouts. That is where the sample efficiency
  comes from.
- **Budget.** `max_metric_calls` (a count of scorer calls) or any stop
  callback: timeout, no-improvement, file flag, or reflection cost cap.
- **Noise.** Not modelled. Strict improvement on three examples is a noisy test
  by itself. GEPA relies on the full-valset step, the Pareto rule, and an
  evaluation cache keyed by candidate hash and example id to avoid repeats.
- **Merge.** With `use_merge`, a second proposer combines two frontier
  candidates that share an ancestor, taking each component from whichever
  parent changed it (`proposer/merge.py`, up to `max_merge_invocations=5`).
  It fires only after a new program was just found.
- **Guardrails.** None needed in the prompt-only case: the candidate is text
  that an adapter plugs into a fixed program. The evaluator is the user's
  function. In `optimize_anything` mode the artifact can be code, and the
  sandbox is the user's job *(not checked in detail)*.
- **Meta-level.** The reflection prompt template is a parameter and can be
  replaced, but nothing evolves it.

### SICA (self-improving coding agent)

Code: [MaximeRobeyns/self_improving_coding_agent](https://github.com/MaximeRobeyns/self_improving_coding_agent) ·
[`runner.py`](https://github.com/MaximeRobeyns/self_improving_coding_agent/blob/master/runner.py),
[`base_agent/src/utils/archive_analysis.py`](https://github.com/MaximeRobeyns/self_improving_coding_agent/blob/master/base_agent/src/utils/archive_analysis.py),
[`base_agent/src/oversight/overseer.py`](https://github.com/MaximeRobeyns/self_improving_coding_agent/blob/master/base_agent/src/oversight/overseer.py).
Paper: [arXiv:2504.15228](https://arxiv.org/abs/2504.15228).

- **Unit of change.** The agent's whole codebase in a working directory. A
  child is a full copy of a chosen base agent's `agent_code`, edited in place,
  plus an `agent_change_log.md` carried forward so each version knows its
  history.
- **Who edits.** The **best** agent so far runs the `improve` command
  (`archive.agent_{best_iter}.agent_code`) on a copy of the selected **base**.
  So the base to change and the agent doing the changing are chosen
  separately.
- **Base selection** (`select_base_agent`). Compute mean score and a confidence
  interval per iteration from per-problem scores. Take the best iteration's
  lower confidence bound. Walk back from the current iteration and return the
  first agent whose mean is at or above that bound. This prefers the *latest*
  agent that is statistically indistinguishable from the best. It is a
  noise-aware rule, and it is the only one of these loops that uses
  confidence intervals for selection.
- **What the improver sees.** The full archive mounted **read-only**
  (`-v exp_dir:/home/agent/archive:ro`), including per-problem results and
  **LLM-written trace summaries** (`generate_contextual_summary`: a one-to-two
  paragraph critique of each run, covering whether the approach was logical,
  where reasoning failed, and what would help). Summaries are written at
  benchmark time, so the improver reads short critiques, not raw traces.
- **Evaluation.** Benchmarks run per problem in containers with a 10-minute
  timeout and a $3 cost cap. `utility()` in `archive_analysis.py` is a **gate**:
  the problem's score counts only if score is positive, cost is at most $3,
  time at most 5 minutes and it did not time out; otherwise it is 0. (The
  paper may describe a weighted mix; the repo code I read is a gate.)
- **Guardrails.** An asynchronous **overseer** LLM watches tool calls, agent
  calls, file events and errors, and returns structured judgements including
  `making_progress` and `is_looping`. The improvement runs in a `sica_sandbox`
  container with the archive read-only and only the new agent directory
  writable.
- **Meta-level.** Yes, by being the same program: the improver is the agent
  being improved. A commented-out branch shows a separate fixed meta agent was
  considered.

### ADAS (Automated Design of Agentic Systems)

Code: [ShengranHu/ADAS](https://github.com/ShengranHu/ADAS) ·
[`_mgsm/search.py`](https://github.com/ShengranHu/ADAS/blob/main/_mgsm/search.py),
[`_mgsm/mgsm_prompt.py`](https://github.com/ShengranHu/ADAS/blob/main/_mgsm/mgsm_prompt.py).
Paper: [arXiv:2408.08435](https://arxiv.org/abs/2408.08435).

- **Unit of change.** A Python `forward()` function for one agent design.
- **No parent selection.** The whole archive, as JSON with each design's
  `thought`, `name`, `code` and `fitness`, is pasted into the meta agent's
  prompt. The LLM decides what to build on. Archive starts as seven hand-made
  designs (chain of thought, self-consistency, self-refine, debate, step-back,
  quality-diversity, role assignment).
- **Reflection before any eval.** After the first draft there are **two forced
  self-critique turns** (`Reflexion_prompt_1`, `_2`). The first asks the model to
  compare the proposal to the archive "CAREFULLY, including their actual
  differences in the implementation", to say whether it is interesting, and to
  check the code against a list of past wrong implementations. The second
  revises with those tips. Only then does it run.
- **Debug loop.** If the code throws or scores under 1%, the error is appended
  to the same conversation and the model fixes it, up to `debug_max=3` times.
- **Evaluation and noise.** Fitness is a bootstrap confidence interval string
  over per-example accuracy (`bootstrap_confidence_interval`), stored with the
  design. `n_generation` defaults to 30.
- **Archive.** Append only. Every valid design joins, including worse ones.
- **Guardrails.** The candidate `forward` is `exec`'d inside the search
  process (`evaluate_forward_fn`). The README warns it runs untrusted code. No
  sandbox.

### AI Scientist v2 (agentic tree search)

Code: [SakanaAI/AI-Scientist-v2](https://github.com/SakanaAI/AI-Scientist-v2) ·
[`ai_scientist/treesearch/parallel_agent.py`](https://github.com/SakanaAI/AI-Scientist-v2/blob/main/ai_scientist/treesearch/parallel_agent.py),
[`journal.py`](https://github.com/SakanaAI/AI-Scientist-v2/blob/main/ai_scientist/treesearch/journal.py),
[`bfts_config.yaml`](https://github.com/SakanaAI/AI-Scientist-v2/blob/main/bfts_config.yaml).

- **Unit of change.** An experiment script (plan plus code) as a node in a
  tree. Node kinds: draft, debug, improve, hyperparameter tuning, ablation,
  and multi-seed replicate.
- **Policy** (`_select_parallel_nodes`). Per worker slot: if fewer than
  `num_drafts=3` root drafts exist, draft a new one. Otherwise with
  probability `debug_prob=0.5` pick a random buggy leaf with
  `debug_depth <= max_debug_depth=3` and ask for a bug fix (its prompt carries
  the code, the terminal output, plot feedback and timing feedback). Otherwise
  improve the best "good" node, preferring trees not already being worked in
  this round.
- **Best node.** `Journal.get_best_node` asks an **LLM** to choose among good
  candidates, shown metrics, training analysis and plot feedback, with an
  instruction not to rely on validation loss alone. `use_val_metric_only`
  switches it to plain `max`.
- **What the proposer sees.** For `_improve`: task idea, a **memory summary**
  of the journal, plot (VLM) feedback, timing feedback and the parent code.
  It does not see siblings' code.
- **Staged.** Four stages with iteration caps (20, 12, 12, 18): get a working
  baseline, tune, creative research, ablations. A sub-stage goal is generated by
  an LLM from the journal; stage completion is judged by an LLM.
- **Noise.** A good node is re-run on `num_seeds=3` seeds and the metrics are
  aggregated (`_run_multi_seed_evaluation`). This is the one loop here that
  re-runs the winner to check it.
- **Guardrails.** Code runs through an interpreter wrapper with timeouts.
  The README warns about executing untrusted code *(not re-read here)*.

### TextGrad

Code: [zou-group/textgrad](https://github.com/zou-group/textgrad) ·
[`textgrad/optimizer/optimizer.py`](https://github.com/zou-group/textgrad/blob/main/textgrad/optimizer/optimizer.py),
[`optimizer_prompts.py`](https://github.com/zou-group/textgrad/blob/main/textgrad/optimizer/optimizer_prompts.py).
Paper: [arXiv:2406.07496](https://arxiv.org/abs/2406.07496).

- **Unit of change.** One text variable with a role description.
- **Proposer context.** The optimiser prompt holds the variable's role, its
  value, and the "context and feedback" gathered by backward calls: the
  downstream critique of each step that used it. The system prompt tells the
  model "the feedback may be noisy, identify what is important and what is
  correct".
- **Extras.** `constraints` (natural-language rules added to the prompt) and
  `TextualGradientDescentwithMomentum`, which adds the last N values and says
  that similar feedback across steps means the change was too small, so make a
  bigger one.
- **No archive, no parent selection.** It is gradient descent: one lineage.
  Whether to keep a step is left to the caller (the repo's tasks typically
  re-evaluate and revert on validation loss; I did not trace this fully
  *(unverified)*).

### Karpathy's autoresearch

Code: [karpathy/autoresearch](https://github.com/karpathy/autoresearch) ·
[`program.md`](https://github.com/karpathy/autoresearch/blob/master/program.md),
[`prepare.py`](https://github.com/karpathy/autoresearch/blob/master/prepare.py),
[`train.py`](https://github.com/karpathy/autoresearch/blob/master/train.py).
Released March 2026 (date from a web search result, not from the repo).

The smallest working loop, and the clearest.

- **Unit of change.** One file, `train.py`. Everything in it is editable.
- **The whole policy is a markdown file** (`program.md`) read by a coding
  agent. Loop: commit an idea, run `uv run train.py > run.log`, grep
  `val_bpb`, log a row to an untracked `results.tsv`
  (`commit, val_bpb, memory_gb, status, description`). If val_bpb improved,
  keep the commit. If equal or worse, `git reset` to where it started.
- **Evaluation.** A **fixed 5-minute wall-clock budget** per run, so every
  candidate is comparable. A run over 10 minutes is killed and counts as a
  failure. VRAM is a soft constraint.
- **Guardrails are rules in the prompt plus file boundaries.** `prepare.py`
  holds the metric (`evaluate_bpb`), data and constants and is declared
  read-only. No new packages. The enforcement is the instruction and the
  `git` history. There is no technical block that I found.
- **Simplicity criterion.** Written into `program.md`: a tiny gain that adds
  ugly code is not worth keeping; deleting code for equal results is a win.
  This is an explicit counter to bloat.
- **Archive.** None beyond the linear git branch and the TSV. Discarded ideas
  survive only as rows in the TSV, which the agent can read.
- **"Never stop".** The prompt orders the agent to run until interrupted, and
  to read papers or combine near-misses when out of ideas.

### Other 2026 work found, read only at abstract level

I could not read the code for these. All claims are from web pages and are
*unverified* against source.

- **Meta-Harness** (Lee, Nair, Zhang, Lee, Khattab, Finn, 2026,
  [arXiv:2603.28052](https://arxiv.org/abs/2603.28052)). The abstract says the
  proposer is an agent that reads the source code, scores and execution traces
  of all prior candidates **through a filesystem**, and argues that existing
  text optimisers "compress feedback too aggressively". Whether the framework
  code is public: the abstract page does not say.
- **PolyHarness** ([PyPI](https://pypi.org/project/polyharness/)). Described by
  its listing as an engine for searching over an agent's harness, borrowing
  Pareto parent selection from GEPA and cascade evaluation from OpenEvolve.
  The page itself would not load for me, so I rely on a search snippet.
- **Hermes Agent Self-Evolution** (Nous Research). Reported by a
  [DEV Community post](https://dev.to/docdavkitty/hermes-agent-self-evolution-nous-research-ships-genetic-prompt-optimization-with-dspy-gepa-2i3g)
  as using DSPy and GEPA to optimise skills, prompts, tool descriptions and
  code of an agent. Not read.
- **RoboPhD** ([arXiv:2604.04347](https://arxiv.org/pdf/2604.04347)) and a July
  2026 preprint on gated quality-diversity harnesses
  ([arXiv:2607.13683](https://www.alphaxiv.org/abs/2607.13683.md)). Titles only.
  The second describes separating proposing a change from crediting it, per
  a search snippet. Not read.

---

## Comparison table

"n/a" means the system has no such mechanism. "?" means not found in the code
read.

| System | Unit of change | Proposer sees | Parent selection | Candidates per step | Eval stages | Noise handling | Archive keeps | Reflection step | Evaluator protection | Meta-level |
|---|---|---|---|---|---|---|---|---|---|---|
| DGM | diff to agent repo | diagnosed issue text (from parent logs + private tests) | sigmoid(10(acc-0.5)) x 1/(1+children) | 2 | small subset, then medium at >=40%, then full | repeat evals option; wide archive | every child that compiled and made a patch | separate diagnosis model, picks ONE improvement | Docker; harness outside | none |
| HGM | diff to agent repo | as DGM | Thompson sampling on subtree outcomes | async, 1 task per call | one task at a time, per-node | Beta posterior | whole tree | as DGM | Docker | none |
| Hyperagents | diff to whole repo | "modify any part"; archive path | sigmoid around top-3 mean x exp(-(c/8)^3) | 1 per loop | small sample, then full | ? | all valid nodes | agent's own | Docker | selector editable (`edit_select_parent`) |
| OpenEvolve | SEARCH/REPLACE on a code block | parent, top 3, inspirations 2, prior attempts, artifacts, feature coords | 20% island, 70% elite archive, 10% random | 1 per worker | cascade 0.5 / 0.75 / 0.9 | ? (deterministic eval assumed) | MAP-Elites cells, 100-program archive, islands | evaluator artifacts only | worker timeout | hook for archive rule |
| ShinkaEvolve | diff, full or cross on marked regions | parent, 1 archive + 1 top-k inspiration, meta-rec, text feedback | sigmoid((s-median)/MAD, 10) x 1/(1+children) | async, many | evaluator, novelty gate first | ? | correct programs, islands, archive | 3-step meta-summary every 10 gens | separate job; marker validation | prompt co-evolution (UCB) |
| GEPA | one text component | failing traces + metric feedback (3 examples) | sample by Pareto-front frequency | 1, plus merges | 3-example minibatch, then full val | strict gain, cache | all accepted candidates + per-example fronts | the core step | candidate is text | none (template replaceable) |
| SICA | whole agent codebase | read-only archive + LLM trace critiques + change log | latest agent whose CI reaches best lower bound | 1 | per benchmark, cost+time gate | confidence intervals | all iterations | trace critique at benchmark time | container, read-only archive, overseer | improver = improved |
| ADAS | one `forward()` | whole archive with fitness | none: LLM chooses | 1 | debug loop x3 | bootstrap CI | all valid designs | 2 forced self-critique turns | none (exec in process) | none |
| AI Scientist v2 | experiment script | memory summary, parent code, plot and timing feedback | best-first (LLM picks), 50% debug of buggy leaf | 4 workers | 4 stages, 3 seeds | 3-seed rerun | journal tree | VLM + LLM analysis per node | interpreter timeout | none |
| TextGrad | one text variable | downstream critiques, optional momentum | none | 1 | n/a | "feedback may be noisy" | none | backward critique | n/a | none |
| autoresearch | `train.py` | its own TSV and git log | linear: keep if better else reset | 1 | one fixed 5-min run | ? (single run) | git branch + TSV | agent's own reading of log | read-only `prepare.py` by instruction | none |

---

## Patterns that recur in every loop that worked

1. **The evaluator is a separate artifact the candidate does not edit.**
   Benchmark harness (DGM, HGM, SICA), `evaluate.py` (OpenEvolve, Shinka),
   a metric function (GEPA), `prepare.py` (autoresearch). In the loops that
   mutate code, the protection is Docker and file placement. In the prompt-only
   loops it is structural: the candidate is text that cannot reach the scorer.
   The weakest protection (a prompt rule, in autoresearch) is used only where a
   human can read the git history.
2. **A cheap check before an expensive one.** DGM's small then medium subset,
   OpenEvolve's cascade thresholds, GEPA's 3-example minibatch before a full
   validation run, Shinka's novelty gate before any eval, ADAS's debug loop
   before scoring, the AI Scientist's stage caps. Nobody pays full price for
   every guess.
3. **Failures are read by a model and turned into text before the proposal.**
   DGM's diagnosis JSON, GEPA's feedback strings, Shinka's insights
   scratchpad, SICA's trace critiques, the AI Scientist's VLM and LLM
   analysis, OpenEvolve's artifacts. The proposer is never asked to guess from
   a bare number.
4. **A pool of candidates, not one line.** Seven of the eight systems with
   selection keep an archive. Linear ones (autoresearch, TextGrad) are the
   exception, and autoresearch compensates with a very fast, very cheap run.
5. **Weak candidates are kept.** DGM `keep_all`, ADAS append-only, Hyperagents
   all valid nodes, GEPA's per-example fronts, MAP-Elites cells. A node that
   scores worse can still be the only holder of a useful trick.
6. **Selection mixes quality with a penalty on being picked too often.**
   `score x 1/(1+children)` appears in DGM, Shinka and (with a flatter form)
   Hyperagents. GEPA's front-frequency, OpenEvolve's 10% random draw and
   HGM's Beta sampling do the same job a different way. Pure "pick the best"
   is available in every code base and is not the default in any.
7. **Strict acceptance or staged gates against noise-driven wins.** Strict
   improvement in GEPA, the 0.4 threshold in DGM, the lower confidence bound
   in SICA, 3 seeds in the AI Scientist.
8. **A hard cap on spend.** Timeouts per attempt everywhere. A $3 per-problem
   cap in SICA, `max_api_costs` in Shinka, `max_metric_calls` in GEPA, a fixed
   5-minute budget in autoresearch.
9. **State written to disk every step.** `dgm_metadata.jsonl`, SQLite in
   Shinka and GEPA, `results.tsv`. Runs resume, and the record is readable by
   the next proposer.
10. **One change at a time, with a stated reason.** DGM asks for ONE
    improvement. GEPA edits one component per step. The AI Scientist's debug
    prompt asks for a 3-5 sentence fix sketch. Autoresearch's loop is one
    commit per idea. Changes that mix several ideas cannot be credited.

## Things only one system does

- **Credit by descendants** (HGM): Thompson sampling over the whole subtree's
  outcomes, so a parent is rewarded for its children.
- **Per-example Pareto frontier** (GEPA): a candidate survives by being best
  at one example, even with a poor mean.
- **Confidence-interval parent choice** (SICA): the latest agent not
  distinguishable from the best.
- **Separate roles for base and editor** (SICA): the best agent edits a copy
  of a different base.
- **LLM bandit over mutation models, cost-aware** (Shinka).
- **Embedding plus LLM novelty gate before an eval** (Shinka; OpenEvolve has
  a similar optional judge).
- **Three-step running insights scratchpad** (Shinka): per-program summary,
  merged global insights, a sampled recommendation.
- **Evolved system prompt for the mutator** (Shinka `evolve_prompts`).
- **Editable selection rule** (Hyperagents `edit_select_parent`).
- **Re-run the winner on several seeds** (AI Scientist v2).
- **Forced self-critique that compares against the archive** (ADAS).
- **Component-wise merge of two frontier candidates** (GEPA).
- **A written simplicity criterion** (autoresearch).
- **An asynchronous overseer** that can stop a run (SICA).
- **Hand-written targeted diagnoses for known failure classes** (DGM's empty
  patch, stochasticity and context length modes).
- **MAP-Elites over code features with islands and migration** (OpenEvolve).

---

## A reference design for a slow-feedback loop

The setting: a daily agent whose real fitness (engagement, replies, outside
reaction) arrives hours to days later, is noisy, and cannot be re-run. None of
the systems above work in that setting. All of them score a candidate in
seconds to minutes and can repeat the run. What follows is mechanism only,
assembled from the pieces above. It has not been tested.

### What breaks when feedback is slow and noisy

- **No rollouts to spend.** GEPA's 3-example minibatch is cheap because a
  rollout costs a model call. Here a rollout is a public post.
- **One sample per candidate, wide variance.** DGM's thresholds and SICA's
  confidence bounds assume repeated evals. Day-to-day variance in reach can
  swamp a real effect.
- **Credit lag.** By the time a score lands, two more changes have merged.
- **The scorer is the world.** It cannot be frozen, and anything the agent
  controls (timing, topic, who it tags) can move it.

### Two evaluators, not one

Split fitness the way OpenEvolve and DGM stage it, but make stage two a
different *kind* of evaluator, not just a bigger one.

1. **Fast proxy stage (minutes, replayable).** A frozen scorer run on stored
   examples: past drafts, past replies with their outcomes, or the judge on
   pairs. This is GEPA's minibatch and DGM's small subset. It rejects
   obvious failures, catches breakage, and is cheap enough to run on every
   candidate. Calibrate it against the slow stage and report the correlation.
   A proxy that stops predicting the slow stage must be retired, because
   every system here that optimised a visible proxy long enough gamed it.
2. **Slow real stage (days, one shot).** The candidate is live for a defined
   window and measured against a control. Treat this like an experiment,
   not a score:
   - Run the candidate on a **fraction of slots** and the parent on the rest,
     interleaved in time, so day effects hit both. Autoresearch gets
     comparability from a fixed budget. A daily agent has to get it from
     interleaving.
   - Pre-register the metric and the window before the change goes live, in a
     file the proposer cannot edit.
   - Record exposure (how many slots, how many impressions), since a mean
     over three posts is not a measurement.

### Spend slow evaluations like HGM spends tasks

HGM's insight transfers directly: do not give each candidate a full eval.
Give each a first small slice, then give more slices only to candidates that
keep winning draws. Use the same Beta posterior over a success definition
(for example "beat the control's median on the window") and Thompson sample
which variant gets the next slot. A variant with two good days gets more
slots. One with two bad days gets starved. This also handles noise: the
posterior stays wide until there is data.

### Archive of variants with lineage

- Store every variant as a row: parent id, the diff, the *reason* given for
  it, the proxy score, the live slots, the live outcomes and the window dates.
  This is `dgm_metadata.jsonl` plus ADAS's `thought`.
- Keep variants that lost. Keep them tagged by *what kind* of change they
  were (tone, format, timing, topic), so a niche winner survives. GEPA's
  per-example front and OpenEvolve's feature cells are two ways to do this.
  Here the "examples" or "cells" are contexts: reply versus original post,
  topic, time of day.
- Pick parents by `score x 1/(1+children)` with a robust midpoint (Shinka's
  median and MAD rather than DGM's fixed 0.5), or by descendant outcomes
  (HGM). Do not pick by the most recent score alone.
- Credit lineage: when a merged variant's descendants do better or worse,
  write it back onto the ancestor. This needs the lag window above.

### Reflective mutation from traces, not from scores

Copy the diagnose-then-implement split from DGM and the feedback strings
from GEPA.

- The **diagnosis call** reads, for the parent variant: the actual posts or
  replies, the outside reaction in words (what people said, not only the
  counts), the proxy judge's note, and the exposure. It outputs ONE proposed
  change, a prediction of which proxy metric and which live metric will move,
  and the direction.
- The **implementation call** receives only the problem statement, not the
  scoring rules or held-out examples. DGM's diagnoser sees the private tests
  and the implementer does not. That asymmetry is the cheapest guardrail
  here.
- The proposal is **one component** (one playbook, one prompt block), as in
  GEPA. A diff that touches six things cannot be credited.
- Before any live slot, run ADAS's forced self-critique: "compare this
  change with the archive's variants and list how it differs, and say which
  past variant it resembles." Run Shinka's novelty gate. A near-duplicate of
  a past loser should be refused before it costs a live slot.

### A running insights file

Add Shinka's meta-memory. Every few days a summariser writes (1) one line per
variant (what changed, what happened, what people said) and (2) a merged
"what worked / what did not / what is unknown" file. One recommendation from
it is sampled into the next diagnosis. Merge into the previous file, do not
rewrite it, so earlier findings are not lost.

### Acceptance and rollback

- **Accept into the pool** on the proxy stage (GEPA's strict gain on a
  minibatch). **Promote to default** only after the live stage, and only if
  the posterior says it beats the control with a stated margin. Use SICA's
  rule in reverse: do not demote a long-standing variant unless the new one's
  lower bound is above the old one's mean.
- **Re-run the winner.** The AI Scientist's three seeds become "give a
  promoted variant a second window with different slots before it becomes the
  baseline". A day-one win that does not repeat is noise.
- **Keep a simplicity rule** from `program.md`: if a change adds length
  without a measured gain, drop it. Slow loops accumulate clutter faster
  because rejected ideas are expensive to retest.
- **Cap spend and change rate.** A maximum number of live variants at once, a
  maximum share of slots for unproven ones, and a hard stop if the control
  itself degrades.

### What the proposer must not touch

From the pattern in every loop that worked: the pre-registered metric, the
proxy judge, the slot allocator, the promotion rule, and the log of live
outcomes live outside what a candidate can edit, and outside what the
proposer is shown in raw form. Show the proposer written reactions and
summaries, not the formula that scores them. DGM's hallucination-marker
episode (other page) and the Hyperagents `edit_select_parent` flag are the two
warnings: a loop that can edit its own selection rule needs the evaluator out
of reach before that switch is turned on.

### Meta-level, last

Only after the base loop shows a promoted variant that repeats. Shinka's
prompt co-evolution and Hyperagents' editable selector both sit on top of a
loop that already produced signal. Evolving the proposer is another candidate
population that needs its own slow evaluation. In a daily loop, do it at a
much slower cadence than the content loop, one change at a time.

---

## What I could not verify

- **DSPy's GEPA wrapper.** Not read. GEPA statements are from `gepa-ai/gepa`.
- **Meta-Harness, PolyHarness, Hermes Agent Self-Evolution, RoboPhD, and the
  July 2026 harness preprint.** Web snippets and an abstract page only. No code
  read.
- **TextGrad revert behaviour.** I read the optimiser and prompts, not how each
  task decides to keep or revert a step.
- **OpenEvolve noise handling and sandboxing.** Searched for both and found
  none in the core. A feature elsewhere in the repo is possible.
- **ShinkaEvolve's `marker_validation`** and what a rejected edit does in
  detail. Mentioned from file names and the docs.
- **AI Scientist v2 guardrails.** Only the config and the search code were read.
- **DGM `best` selection.** The sort direction looks reversed. Not run.
- **SICA's weighted utility.** The repo code is a gate. Any weighted form in the
  paper was not compared.
- **Whether Hyperagents' shipped selector is what the paper's edited selector
  starts from.** I read the flag and the container path, not any evolved
  selector.
- **Total cost per run** for each system. I report configured limits, not
  measured spend. Papers' cost numbers were not re-checked.
- **Default branch line numbers and later changes.** Links go to the default
  branch and may move.
