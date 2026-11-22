# Why self-improvement loops stall

Why loops that change their own prompts, scaffold or code plateau, churn, or
ship changes that aren't real improvements, and what the loops that did
compound did differently. This page is about the failure modes. The systems
themselves are described in [`self-improving-agents.md`](self-improving-agents.md),
and Goodhart and reward hacking as safety problems in
[`rsi-theory-and-safety.md`](rsi-theory-and-safety.md). Neither is repeated
here except where a number is needed.

Numbers come from the linked paper's abstract, tables or text. Where a number
is my own arithmetic it says *(computed)*. The research for this page was done
on 2026-11-22.

## The short answer

A loop compounds only when each step is real: the change it keeps has to be
better, not just luckier. Five things decide that.

1. **The signal.** The scorer's noise has to be small next to the size of the
   change. Usually it isn't. Small prompt edits make small effects, and judges
   and benchmarks are noisy. A loop that picks winners from noise ships noise
   and calls it progress.
2. **The search.** Keeping only the latest or the best variant stalls fast.
   Every loop that compounded kept an archive and built on stepping stones that
   scored worse at first.
3. **The proposals.** A model asked for "an improvement" gives its most typical
   idea, again and again. Without fresh evidence about what failed, proposals
   stay near the model's prior and repeat.
4. **Reflection on failure.** Reading real traces and real errors is where the
   gains in Reflexion, GEPA, ACE and DGM come from. Reflection without an
   outside signal adds little or nothing.
5. **The ceiling.** A frozen model sets a ceiling. Scaffold changes that move
   it add a verifier, a tool or more tries. Prompt rewording mostly doesn't.

Goodharting sits across all five. The weaker the signal, the more of the
apparent progress is the loop learning the judge.

---

## 1. The signal is weaker than the effect

### Evaluation noise against effect size

- **Format alone moves scores a lot.** Changes to prompt formatting that keep
  the meaning, such as separators, casing and spacing, moved few-shot accuracy
  by "up to 76 accuracy points" on LLaMA-2-13B. The spread did not go away
  with bigger models, more examples or instruction tuning
  ([Sclar et al., 2023](https://arxiv.org/abs/2310.11324)). So a small edit to
  a prompt can move a score by more than any real effect it has, in either
  direction.
- **Evals are experiments and need error bars.** Anthropic's guidance: report
  the standard error with every score, cluster it when questions share a
  source ("clustered standard errors on popular evals can be over three times
  as large as naive standard errors"), resample each question several times,
  compare models on paired differences, and run a power analysis before the
  eval to know how many questions a given difference needs
  ([Anthropic, 2024](https://www.anthropic.com/research/statistical-approach-to-model-evals);
  [Miller, arXiv:2411.00640](https://arxiv.org/abs/2411.00640)).
- **Sample size grows with the square of the effect.** The rule of thumb from
  online experiments is n = 16σ²/Δ² per arm for 95% confidence and 80% power.
  Halving the effect you want to detect needs four times the data. Moving from
  detecting a 5% change to a 20% change cut one example from 122,000 users to
  7,600 ([Kohavi et al., 2009](https://ai.stanford.edu/~ronnyk/2009controlledExperimentsOnTheWebSurvey.pdf),
  §3.2).

**What that means for a pairwise judge** *(computed, normal approximation,
two-sided 5%, 80% power)*. Suppose each pair is one judge call that prefers
the candidate or the baseline. To tell a true preference rate from 50%:

| True candidate win rate | Pairs needed (no ties) |
|---|---|
| 0.70 | about 47 |
| 0.65 | about 85 |
| 0.60 | about 194 |
| 0.55 | about 783 |

A prompt edit that really improves one behaviour a little is a 0.55 change,
not a 0.70 one. A loop that judges it on tens of pairs can't see it.

### Too few samples per candidate, and the code-evolution case

The clearest measurement is Gideoni, Risi and Gal,
[*Simple Baselines are Competitive with Code Evolution*](https://arxiv.org/abs/2602.16805)
(2026). Two plain baselines, independent random sampling and sequential
sampling conditioned on earlier tries, matched or beat purpose-built
evolution systems in three domains. For agent scaffolds:

- "High variance in the scaffolds coupled with small datasets leads to
  suboptimal scaffolds being selected."
- Validation sets were about 100 questions. Even with 10 evaluations per
  question, accuracy still had a standard deviation of more than 1% on AIME
  2025. Getting it to 0.5% needed more than 60 evaluations per question, over
  20 times the default cost.
- Automated methods lost more than 10 points from validation to test. The
  majority-vote baselines lost about 5. All automated methods, random sampling
  included, ended below a hand-written majority-vote scaffold on test.
- The authors recommend an effective test set of at least 300 questions with
  95% confidence intervals.

Their other finding matters just as much. Changing the *search space*, by
reformulating one maths problem, improved the bound for every method more
than any search algorithm did inside the old setup. "The main difficulty"
lies in "designing good search spaces … and not the search itself."

The successful loops paid for evaluation. DGM scored each child on 10 tasks
first, then on 50 or 60, and only the best on 200. Its threshold for moving
up a stage was "chosen based on the noise observed in preliminary runs." A
full SWE-bench run cost about USD 22,000 and took about two weeks
([Zhang et al., 2025](https://arxiv.org/abs/2505.22954), §3 and App. E).

### The winner's curse and regression to the mean

When you keep the candidate that scored best, its score is biased upward by
the noise that made it win. The same holds for one candidate that passed a
threshold.

- **Exaggeration at low power.** Gelman and Carlin call this a Type M error.
  When power is 0.06, a result that comes out "significant" has a 24% chance
  of having the wrong sign. On average it overstates the true effect 9.7
  times. Exaggeration problems start below 50% power, and sign errors below
  10% ([Gelman and Carlin, 2014](https://sites.stat.columbia.edu/gelman/research/published/retropower_final.pdf)).
- **The same bias in A/B tests.** Selecting experiments for significance
  "introduces bias, also known as 'winner's curse'", so the launched effects
  overstate what launched ([Deng et al., KDD 2021](https://arxiv.org/abs/1910.03788),
  citing Lee and Shen, KDD 2018). I could not open the Lee and Shen paper or
  Airbnb's blog post on it (403), so their numbers are not quoted here.
- **Most ideas don't work.** At Microsoft "only about 1/3 of ideas improve the
  metrics they were designed to improve", and at Amazon, where every feature
  was tested, the success rate was below 50%
  ([Kohavi et al., 2009](https://ai.stanford.edu/~ronnyk/ExPThinkWeek2009Public.pdf)).
  A loop whose own ideas pass far more often than that is probably measuring
  something other than the goal.

**Selecting among several candidates makes it worse** *(computed, exact
binomial)*. A change with no effect at all reaches a 0.6 win rate on 30 fair
coin-flip pairs 18% of the time. With four such candidates, at least one
reaches 0.6 about 55% of the time. A tournament with a 0.6 bar on 30 pairs
promotes a no-effect change about as often as not. Afterwards the "winner"
falls back toward 0.5. That isn't decay. It was never better.

**A non-regression test has the opposite problem.** It asks "did anything get
worse?", and on few pairs the answer is almost always "no evidence it did"
*(computed, one-sided sign test at 5%)*:

| Pairs | Chance of catching a change that loses 60% of pairs | … loses 70% |
|---|---|---|
| 8 | 11% | 26% |
| 16 | 17% | 45% |
| 30 | 29% | 73% |
| 60 | 45% | 94% |

A pass on 8 or 16 pairs says little about whether the change is harmless,
and nothing about whether it helps.

### LLM-judge variance and bias

- **Position, length and self-preference.** Zheng et al. name three biases in
  LLM judges: position, verbosity and self-enhancement. They also find strong
  judges agree with people over 80% of the time, about as often as people
  agree with each other ([arXiv:2306.05685](https://arxiv.org/abs/2306.05685)).
  Just by swapping the order of answers, Vicuna-13B "could beat ChatGPT on 66
  over 80 tested queries" with ChatGPT as the judge
  ([Wang et al., 2023](https://arxiv.org/abs/2305.17926)).
- **Length.** AlpacaEval "is known to favor models that generate longer
  outputs". Controlling for length raised its Spearman correlation with
  Chatbot Arena from 0.94 to 0.98 ([Dubois et al., 2024](https://arxiv.org/abs/2404.04475)).
- **Self-preference.** Models can tell their own outputs from others'. In
  fine-tuning, self-recognition correlated linearly with how strongly a model
  favoured its own text ([Panickssery et al., 2024](https://arxiv.org/abs/2404.13076)).
  A judge from the same family as the proposer tilts toward the proposer's
  style.
- **One sample is not a verdict.** Fixed seeds and temperature 0 "can improve
  consistency" but "do not guarantee reliability". A single judgment "can
  still be misleading" ([Schroeder and Wood-Doughty, arXiv:2412.12509](https://arxiv.org/abs/2412.12509)).
- **Judges are weak where taste matters.** Judging research ideas, the best LLM
  ranker agreed with expert reviewers 53.3% of the time, and the others 43.3%
  to 51.7%. Two human reviewers agreed with each other 56.1% of the time
  ([Si et al., 2024](https://arxiv.org/abs/2409.04109)). I found no study
  measuring how well an LLM judge's pairwise preference between two posts
  predicts real engagement on X. Zero-shot virality prediction from post text
  looks weak in the few papers I found, but none tests that exact question.

---

## 2. Credit in a slow, noisy, non-stationary world

A loop scored on audience metrics has a harder problem than any benchmark
loop: one history, delayed effects, and outcomes driven partly by luck.

- **Success in social systems is partly luck by construction.** In MusicLab,
  14,341 people downloaded unknown songs, either with or without seeing others'
  downloads. "Increasing the strength of social influence increased both
  inequality and unpredictability of success." The best songs rarely did
  badly and the worst rarely did well, "but any other result was possible"
  ([Salganik, Dodds and Watts, 2006](https://www.cs.toronto.edu/~bor/303s19/docs/salganik_dodds_watts.pdf)).
- **There is a ceiling on predicting cascades.** On Twitter, the best models
  explained "less than half of the variance in cascade sizes", and "even with
  unlimited data predictive performance would be bounded well below
  deterministic accuracy" ([Martin et al., 2016](https://arxiv.org/abs/1602.01013)).
  So one day's engagement after a change is mostly noise about that change.
- **Short-term effects mislead.** At Google, "the short-term effect is not
  always predictive of the long-term effect". Users' learning "may take months"
  to show fully. For changes on every results page, about half of it happened
  within 14 to 21 days. Their fix was a model that predicts the long-term
  effect from short-term metrics ([Hohnhold, O'Brien and Tang, KDD 2015](https://www.gwern.net/doc/economics/advertising/2015-hohnhold.pdf)).
- **Peeking breaks the statistics.** Ordinary p-values are "wholly unreliable"
  if you watch a test and stop when it looks good. Always-valid p-values fix
  this ([Johari et al.](https://arxiv.org/abs/1512.04922)). A loop that checks
  a metric every hour and acts on the first good reading is peeking.
- **Off-policy credit needs known randomisation.** The replay method that
  evaluates a new policy from old logs without bias assumes events are i.i.d.
  and the logging policy "chose each arm at each time step uniformly at
  random" ([Li et al., 2011](https://arxiv.org/abs/1003.5956), §3). Logs from
  a policy that always did what it thought best can't credit one alternative
  over another.
- **Paired comparisons are far cheaper.** Interleaving shows two rankers'
  results to the same users on the same queries. It needed "over an order of
  magnitude" fewer queries than the best absolute click metric. The authors
  attribute this to it being "a paired test, paired on both queries and users"
  ([Chapelle et al., 2012](https://www.cs.cornell.edu/people/tj/publications/chapelle_etal_12a.pdf)).
  Industry write-ups claim 10 to 100 times less traffic. I couldn't open
  Netflix's post (403), so that range is unverified here.
- **Reusing a holdout overfits it.** "Reusing a holdout set adaptively
  multiple times can easily lead to overfitting to the holdout set itself"
  ([Dwork et al., 2015](https://arxiv.org/abs/1506.02629)). A fixed eval set
  that a daily loop checks against for months stops being held out.

**What this means.** A daily loop can't credit one change from the next day's
audience numbers. It can use real-world outcomes to check a *policy* over
weeks: many changes, many posts, against a held-back or alternating control.
Short-term numbers are useful only through a proxy that has been checked
against the long-term outcome, as Google did.

---

## 3. The search space

### Greedy search stalls; archives and stepping stones carry it

The ablations agree, and the gaps are large.

| System | Full method | Without the archive / Pareto set | Source |
|---|---|---|---|
| DGM, SWE-bench | 50.0% | 23.0% (keeps only the latest agent); 39.7% ("DGM Greedy") | [arXiv:2505.22954](https://arxiv.org/abs/2505.22954), Table 1 |
| DGM, Polyglot | 38.0% | 14.0%; 30.0% (Greedy) | same |
| GEPA, Qwen3 8B, 4 tasks | 61.28 | 54.89 (always pick the best); 53.95 (beam of 4) | [arXiv:2507.19457](https://arxiv.org/abs/2507.19457), Table 3 |

- In GEPA, picking the best candidate every round "led to a local-optima after
  one iteration" and then "quickly stalls, wasting rollouts on a single
  candidate." Pareto selection keeps every candidate that is best on at least
  one example.
- In DGM, "many paths to innovation traverse lower-performing nodes." The best
  agent's lineage had two dips in score, and at iterations 4 and 56 a child
  scored below its parent, yet a later child beat every agent before it.
- HGM measured why greedy choice fails. An agent's own score correlated only
  0.27 to 0.44 (Pearson) with how good its descendants turned out to be. HGM's
  lineage estimate correlated 0.51 to 0.87. Choosing parents by lineage
  reached higher scores in 2.4 to 6.9 times fewer CPU hours than DGM
  ([Wang et al., 2025](https://arxiv.org/abs/2510.21614), Tables 1 and 2).
- AlphaEvolve's ablation found that removing evolution (re-prompting from the
  same program), the prompt's context, meta-prompt evolution, full-file
  evolution or the mix of models each made results worse. The paper reports
  the sizes only in a figure ([arXiv:2506.13131](https://arxiv.org/abs/2506.13131), §4).

### Path dependence and proposal collapse

- **Early ideas anchor later ones.** In SICA, early feature ideas "would often
  heavily influence later feature ideas as variations on the same theme," and
  a poor early idea lowered the quality of what came after
  ([Robeyns et al., 2025](https://arxiv.org/abs/2504.15228)).
- **The model's ideas run out.** Of 4,000 seed ideas generated per topic, only
  about 200 were not near-duplicates (cosine similarity above 0.8). Generating
  more "simply leads to repeating duplicate ideas", and the count of distinct
  ideas plateaus ([Si et al., 2024](https://arxiv.org/abs/2409.04109)).
- **Aligned models collapse to typical answers.** Verbalized Sampling traces
  mode collapse to "typicality bias in preference data": annotators favour
  familiar text. Asking for a distribution of answers with probabilities
  raised diversity 1.6 to 2.1 times in creative writing
  ([Zhang et al., 2025](https://arxiv.org/abs/2510.01171)).
- **Self-training loses diversity.** In iterative self-improvement, pass@k at
  large k fell with each round, "indicating that the diversity of the
  generations is reduced" ([Song et al., 2024](https://arxiv.org/abs/2412.02674)).
- **Complexity is not capability.** Re-run fairly, automatically designed
  multi-agent systems "consistently underperform CoT-SC despite being up to
  10x more expensive". On GPQA-Diamond with GPT-5, ADAS scored 85.23% at
  $832.10 against 87.35% at $46.39 for self-consistency. In AFlow, 7 of 14
  final workflows simply ran one prompt three times and aggregated. The
  authors call the pattern "architectural bloat": designs that "prioritize
  superficial complexity" ([Jwalapuram et al., 2026](https://arxiv.org/abs/2606.13003)).

**What this means.** "One learning, one smallest change" is a good unit for
safety and review. As a *search*, it is a greedy local step whose proposals
come from the model's most typical idea. It needs an archive to build on, a
novelty check against past proposals, and now and then a bigger move.
Otherwise it can't leave the area it starts in.

---

## 4. Reflection: where the gain actually comes from

The best-supported claim in this literature is that *reading specific
feedback about real failures* is what helps. Reflecting without new
information doesn't.

| Study | With the component | Without it | Source |
|---|---|---|---|
| Reflexion, HumanEval Rust (50 hardest, GPT-4) | 0.68 full | 0.60 without self-reflection (no gain over base); 0.52 without test generation | [arXiv:2303.11366](https://arxiv.org/abs/2303.11366) |
| Reflexion, HotPotQA | +8 points absolute for self-reflection over episodic memory alone | | same |
| Self-Refine, sentiment reversal | 43.2 specific feedback | 31.2 generic feedback; 0 none | [arXiv:2303.17651](https://arxiv.org/abs/2303.17651), Table 2 |
| Self-Refine, code optimisation | 27.5 | 26.0 generic; 24.8 none | same |
| ACE, AppWorld average | 59.4 full (offline) | 56.8 without multi-epoch; 55.1 without reflector or multi-epoch; 42.4 base | [arXiv:2510.04618](https://arxiv.org/abs/2510.04618), Table 3 |
| ExpeL, HotpotQA success | 39.0 full | 36 insights only; 31 retrieval only; 32.0 hand-written insights; 32.0 insights from GPT-3.5 | [arXiv:2308.10144](https://arxiv.org/abs/2308.10144), Table 3 and §5.2 |

What the ablations say:

- **The external check carries the gain.** In Reflexion's Rust ablation,
  reflection without generated tests did worse than the base model, and tests
  without reflection did no better. The combination gained. Where its tests
  were wrong, on MBPP Python with a 16.3% false-positive test rate, Reflexion
  scored 77.1% against GPT-4's 80.1%. It was the only benchmark where it did
  not beat the prior best.
- **Intrinsic self-correction doesn't work.** Without outside feedback, LLMs
  "struggle to self-correct", and sometimes get worse
  ([Huang et al., 2023](https://arxiv.org/abs/2310.01798)). Self-Refine's
  maths gains were near zero (GPT-4: 92.9 to 93.1), because the model said
  "everything looks good" on 94% of ChatGPT's maths answers.
- **Generic critique is weak.** Self-Refine's generic feedback kept less than
  half of the gain on code optimisation and lost much of it on sentiment
  reversal.
- **Comparing successes with failures helps.** ExpeL's insights come from
  pairs of a success and a failure. Insights plus retrieval of similar past
  tasks beat either one alone. Adding Reflexion's reflections to the insights
  "hurt performance" (29.0). Insights written by a weaker model were worse.
- **Reflection without labels can pollute.** ACE without ground truth still
  gained on AppWorld (57.2 against 59.4 with labels). On finance, Dynamic
  Cheatsheet without labels fell below the base model (65.4 against 69.1).
  ACE warns that a context "can be polluted by spurious or misleading
  signals", and that if the reflector extracts nothing useful the context "may
  become noisy or even harmful."
- **Rewriting loses detail.** Dynamic Cheatsheet's context shrank from 18,282
  tokens to 122 in one rewrite, and accuracy fell from 66.7 to 57.1, below the
  63.7 of no adaptation at all (ACE §2.2).
- **DGM diagnoses from its own logs.** "Each selected parent analyzes its own
  benchmark evaluation logs, proposes the next feature to implement," and
  then implements it. The proposal starts from evidence of failure, not from a
  blank request to improve.
- **GEPA.** The reflective step reads traces and feedback text. I found no
  ablation in the GEPA paper that removes the trace or feedback text alone,
  so its gain can't be split between reflection and Pareto selection from
  that paper. Its authors note that most of the rollout budget goes on
  validation, used "solely for candidate selection."

---

## 5. The ceiling set by the frozen model

- **Iterated self-improvement saturates fast.** The generation-verification
  gap "diminishes nearly to zero within two or three rounds," and
  self-improvement "typically saturates after two or three rounds, regardless
  of the model's capacity" ([Song et al., 2024](https://arxiv.org/abs/2412.02674)).
- **RL sharpens; it doesn't widen.** With many samples, base models solve more
  problems than their RL-tuned versions. The reasoning found "originate[s] from
  and [is] bounded by the base model" ([Yue et al., 2025](https://arxiv.org/abs/2504.13837)).
  Prompt and scaffold search are weaker than RL, so the same bound applies to
  them at least as strongly.
- **Scaffolds barely move reasoning.** SICA's reasoning benchmarks showed
  "less improvement", and crude reasoning components "may in fact interrupt"
  a reasoning model's chain of thought. "There seem to be limits to what can
  be achieved with such purely scaffolding based systems"
  ([Robeyns et al., 2025](https://arxiv.org/abs/2504.15228)).
- **Gains rise with the model.** AlphaEvolve "performs increasingly better as
  the underlying LLM improves" ([arXiv:2506.13131](https://arxiv.org/abs/2506.13131)).
  ExpeL's insights were better from a better model.

**Which scaffold changes moved capability.** DGM's own discoveries were patch
validation, better file viewing, finer editing tools, generating several
solutions and ranking them, and a memory of past attempts and why they failed
([Sakana](https://sakana.ai/dgm/)). Reflexion's gain needed generated tests.
Self-consistency, more samples with a vote, beat most automated designs in
the multi-agent re-evaluation above. The pattern: changes that add a
*verifier*, a *tool that acts on the world*, *more attempts with selection*,
or *memory of specific failures* move results. Changes to wording, roles and
topology mostly don't, once evaluation is fair.

---

## 6. Goodharting: progress on the judge, not the goal

How judges get gamed is covered in
[`rsi-theory-and-safety.md`](rsi-theory-and-safety.md). Here is how gaming
shows up as progress that isn't real.

- **Proxy up, truth down.** Optimising against a learned reward model raises
  the true reward at first and then lowers it: optimising it "too much can
  hinder ground truth performance". The curve's shape depends on the
  optimisation method ([Gao, Schulman and Hilton, 2022](https://arxiv.org/abs/2210.10760)).
  A loop that keeps promoting against one fixed judge travels along that
  curve.
- **A constant answer beats the judges.** A "null model" that always gives
  the same output got an 86.5% length-controlled win rate on AlpacaEval 2.0,
  83.0 on Arena-Hard-Auto and 9.55 on MT-Bench
  ([Zheng et al., 2024](https://arxiv.org/abs/2410.07137)).
- **Single tokens fool judges.** Non-word symbols or openers such as
  "Thought process:" "can consistently elicit false positive rewards" from
  generative reward models, including frontier ones
  ([Zhao et al., 2025](https://arxiv.org/abs/2507.08794)).
- **Benchmarks without holdouts invite shortcuts.** Many agent benchmarks have
  "inadequate holdout sets, and sometimes none at all", which leads agents to
  "take shortcuts and overfit" ([Kapoor et al., 2024](https://arxiv.org/abs/2407.01502)).

**The tell.** Judge win rate rises while outside measures stay flat, and the
winning changes share a surface trait: longer, a stock phrase, a format.
Regressional Goodhart, selecting on noise, looks the same from inside, so
both need checking.

---

## What this argues for in Teeny

Ranked by how much each is likely to change whether the loop's merged changes
add up. Each is a question to answer with Teeny's own records first, then the
change it argues for.

1. **Measure the noise floor before trusting any verdict.**
   *Question:* run the same brain against itself (main against main, new
   drafts) through the exact eval path. How often does it say "pass",
   "regressed" or "inconclusive"? What is the spread of the judge's win rate
   across repeats? *Change:* report each verdict as an effect with a
   confidence interval and its power. Set the pair count from a power
   calculation for the effect size a single playbook edit can plausibly have.
   If that count can't be afforded, label the change unmeasured, not passed.

2. **Separate "harmless" from "better", and require evidence of better for
   anything claimed as an improvement.**
   *Question:* of merged changes, how many had a measured positive effect, and
   how many only failed to show a regression? *Change:* keep non-regression as
   the gate for shipping. Track "improvement" separately, credited only from a
   positive paired result or a checked outcome. A loop that counts only
   harmless changes will look busy and learn nothing.

3. **Put real failure evidence in front of the proposer.**
   *Question:* when a change is proposed, does the proposer see the specific
   failing outputs, the judge's notes on lost pairs, and the live posts that
   flopped, or only a summary learning? *Change:* GEPA, DGM and ExpeL style:
   give the proposer concrete success and failure pairs and real traces, ask
   it to diagnose before it edits, and keep the hidden set's notes out of
   reach. Specific feedback beat generic feedback in every ablation above.

4. **Correct for the winner's curse and watch regression to the mean.**
   *Question:* after merge, does a change's effect on fresh drafts match its
   eval effect, or shrink toward zero? *Change:* re-score each merged change
   on new samples a week later, and shrink reported effects by the gap seen.
   When several candidates compete, count the comparisons and raise the bar
   to match.

5. **Pair everything.** Same tasks, same seeds where possible, the judge
   called in both orders, ties counted. Interleaving got more than ten times
   the sensitivity from pairing alone. For live posting, alternate old and
   new policies across comparable slots instead of comparing this week with
   last week.

6. **Keep an archive and build on it.**
   *Question:* do proposals ever start from a non-main variant, or revisit a
   parked one? How many proposals are near-duplicates of earlier ones?
   *Change:* let a proposal start from a parked or lower-scoring variant now
   and then, credit lineages (HGM), reject near-duplicates before evaluating
   them, and show the proposer what was already tried and how it did.

7. **Aim changes at what can move the ceiling.**
   *Question:* what share of merged changes are wording edits, and what share
   add a check, a tool, more attempts with selection, or a memory of specific
   failures? *Change:* weight the proposer toward the second kind, which is
   what moved DGM, Reflexion and the self-consistency baselines.

8. **Credit real-world outcomes by policy over weeks, not by change over
   days.**
   *Question:* how much of the day-to-day variance in the world score is
   explained by the calendar, news, one viral post, or token price, rather
   than by brain changes? *Change:* attribute outcomes to batches of changes
   against a control kept back by design (some slots on the old policy).
   Use always-valid statistics if watched hourly. Build a short-term proxy
   only once it has been checked against the long-term outcome.

9. **Audit the judge as a moving part.**
   *Question:* does judge win rate rise while live outcomes stay flat? Do the
   winners share length, a phrase or a format? Is the judge the same model
   family as the proposer? *Change:* control for length, swap positions, use
   a judge from another family or several judges, and rotate held-out tasks
   so the loop can't overfit a fixed set (Dwork et al.).

10. **Know when the loop has hit the model's ceiling.**
    *Question:* do gains stop after a few rounds on a given behaviour, as Song
    et al. found? *Change:* when a behaviour stops improving, stop proposing
    edits to it and spend the budget elsewhere. Treat a base-model upgrade as
    the event most likely to move it.

## Claims I could not verify

- Lee and Shen's (KDD 2018) own numbers on Airbnb's winner's curse. The paper
  and the Airbnb post returned 403. Only Deng et al.'s description is used.
- The "100 times fewer users" figure for interleaving at Netflix. That post
  returned 403. Only Chapelle et al.'s "over an order of magnitude" is used.
- How well LLM judges predict real engagement on X. I found no direct study.
- The size of each AlphaEvolve ablation, which is shown only in a figure.
- Whether GEPA's gain comes mostly from reflective mutation or from Pareto
  selection. No ablation that removes the feedback text was found.
