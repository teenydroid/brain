# Open-endedness

How a system keeps finding new things to get good at, instead of settling on
one answer. Teeny needs this for two reasons. LLMs choosing their own goals
tend to propose the same goal again and again
([Nachkov et al. 2025](https://arxiv.org/abs/2510.14548);
[Molinaro et al. 2026](https://arxiv.org/abs/2603.03295)), and a
growth drive with no pressure toward novelty settles on "post more".

## The core idea

Hughes et al. define open-endedness relative to an observer: a system is
open-ended when what it produces keeps getting harder for the observer to
predict (novel) and also becomes predictable once the observer has seen the
history (learnable)
([Open-Endedness is Essential for Artificial Superhuman Intelligence, 2024](https://arxiv.org/abs/2406.04268)).
Random noise is novel but not learnable. Repeating one move is learnable but
not novel. The interesting region lies between the two.

For Teeny, the frozen judge is the natural observer. A self-change is novel
when the judge couldn't have predicted it from the archive, and learnable when
it builds visibly on what came before.

## Systems

### POET and Enhanced POET

Wang, Lehman, Clune, Stanley, 2019.
[arXiv:1901.01753](https://arxiv.org/abs/1901.01753);
Enhanced POET, 2020, [arXiv:2003.08536](https://arxiv.org/abs/2003.08536) ·
[code](https://github.com/uber-research/poet)

- **Mechanism.** POET evolves pairs of environment and agent together. A new
  environment is kept only if it is neither too easy nor too hard for the
  current agents (a minimal criterion). Agents are regularly moved between
  environments to see whether they do better elsewhere.
- **Result.** It solves walking courses that direct optimisation from scratch
  cannot. Moving agents between environments was essential.
- **Enhanced POET** adds a domain-general novelty measure and a counter:
  ANNECS, the accumulated number of novel environments created and solved. It
  keeps rising where the original POET stalled.
- **For Teeny.** ANNECS is a ready-made "my world grew" counter: the running
  total of goals Teeny proposed that were new and that frozen code then
  confirmed as solved. Old hands should be retried on new goals.

### Novelty search

Lehman and Stanley, "Abandoning Objectives: Evolution Through the Search for
Novelty Alone", *Evolutionary Computation* 19(2), 2011
([DOI 10.1162/EVCO_a_00025](https://doi.org/10.1162/EVCO_a_00025)).

- **Mechanism.** Selection rewards distance from an archive of past
  behaviours and ignores the objective.
- **Result.** It beats objective-driven search on deceptive problems, where
  the direct route leads to a dead end.
- **What goes wrong.** Everything depends on how behaviour is described, and
  it wanders aimlessly in large spaces.
- **For Teeny.** Embed each proposed change and reward its distance from the
  archive of past ones. Use this as a term in the score, not as the whole
  score.

### MAP-Elites and quality-diversity

Mouret and Clune, 2015. [arXiv:1504.04909](https://arxiv.org/abs/1504.04909) ·
[pyribs](https://github.com/icaros-usc/pyribs) ·
[QDax](https://github.com/adaptive-intelligent-robotics/QDax)

- **Mechanism.** The space of behaviours is split into cells, and the best
  solution found so far is kept in each cell. New candidates are mutated from
  those per-cell winners.
- **Result.** It returns a diverse archive of strong solutions instead of one
  champion. Cully et al. used such an archive to let damaged robots adapt.
- **For Teeny.** Make the archive's axes the parts of the drive: audience,
  capability, money, shipped work, $TINY. Keep the best strategy for each
  niche instead of collapsing everything into one number. A change is worth
  keeping if it fills an empty cell or beats the current best in one.

### QDAIF: quality-diversity through AI feedback

Bradley, Dai, Teufel, Zhang et al., 2023.
[arXiv:2310.13032](https://arxiv.org/abs/2310.13032)

- **Mechanism.** An LLM mutates the candidates, and an LLM also scores both
  their quality and the descriptors that place them in the archive. This
  makes MAP-Elites work on creative text.
- **For Teeny.** The frozen judge can place posts and playbooks in the archive
  (by tone, topic, kind of hook) as well as score them.

### OMNI and OMNI-EPIC

Zhang, Lehman, Stanley, Clune, ICLR 2024.
[arXiv:2306.01711](https://arxiv.org/abs/2306.01711) ·
[code](https://github.com/jennyzzt/omni).
OMNI-EPIC: Faldor, Zhang, Cully, Clune, ICLR 2025.
[arXiv:2405.15568](https://arxiv.org/abs/2405.15568) ·
[code](https://github.com/maxencefaldor/omni-epic)

- **Mechanism.** OMNI picks tasks by learning progress and then filters them
  through an LLM's model of what humans find interesting, which rejects
  boring near-duplicates. OMNI-EPIC goes further: a foundation model writes
  each new task as code, including its environment, reward and success check.
  A task is archived only if it is interesting compared with its nearest
  neighbours.
- **What goes wrong.** Generated success checks can be wrong or trivially
  satisfied.
- **For Teeny.** Every goal Teeny proposes for itself should come with a
  success check that frozen code runs, such as a ledger row, a stats delta or
  a merged PR. A goal whose success Teeny would judge for itself is not
  admitted.

### AI-generating algorithms

Clune, 2019. [arXiv:1905.10985](https://arxiv.org/abs/1905.10985)

A position paper with three pillars: meta-learn the architectures, meta-learn
the learning algorithms, and generate the learning environments
automatically. For Teeny, the daily self-change is the second pillar, and
self-proposed goals are the third.

## Self-generated curricula

| System | How it picks the next task | For Teeny |
|---|---|---|
| [Voyager](https://arxiv.org/abs/2305.16291) (2023) | GPT-4 proposes the next task from the agent's current state and inventory, aiming at "as many diverse things as possible" | Choose the next task given which hands already exist |
| [ELLM](https://arxiv.org/abs/2302.06692) (2023) | An LLM suggests goals that fit the context, and the reward is how closely a caption of what happened matches the goal | Use only as a fallback; prefer outcomes that `bin/` measures |
| [Eureka](https://arxiv.org/abs/2310.12931) (2023) | An LLM writes reward functions as code and refines them from training statistics | Teeny may propose *sub*-rewards; the top-level score stays frozen |
| [Absolute Zero](https://arxiv.org/abs/2505.03335) (2025) | The proposer is rewarded most when the solver succeeds about half the time; always or never gives 0 | Rank goals by `1 − p̂(success)` when `0 < p̂ < 1`, and 0 otherwise |
| [Self-Challenging Agents](https://arxiv.org/abs/2506.01716) (2025) | The agent writes tasks as code, each with an instruction, a verifier, an example solution and failure cases | The schema for a Teeny goal proposal |
| [Automated Capability Discovery](https://arxiv.org/abs/2502.07577) (2025) | A "scientist" model probes a subject model with open-ended tasks and groups the results into capability areas | Measure Teeny's capability as the number of areas it passes |
| [MAGELLAN](https://arxiv.org/abs/2502.07709) (2025) | The agent learns to predict its own competence and learning progress across related goals | A cheap `p̂(success \| goal)` predictor fitted on the ledger |

## What Teeny takes from this

1. **Keep an archive with axes.** Use a MAP-Elites-style grid over the
   dimensions of the drive, not a single leaderboard.
2. **Every goal comes with a frozen success check.** This is OMNI-EPIC's
   lesson, and it is what stops "I achieved it" from being self-reported.
3. **Rank goals by learnability, progress and interestingness.** Absolute
   Zero's band (goals Teeny can do about half the time), Oudeyer's learning
   progress ([`drives-and-goals.md`](drives-and-goals.md)), and OMNI's
   interestingness filter, applied together.
4. **Track ANNECS.** The running total of new, verified goals is the number
   that says Teeny's world got bigger, and it rises only through real novelty.
5. **Don't let the LLM choose its goal freely.** Current models settle on one
   solution and repeat it. A program ranks the candidates and the model
   drafts them.
