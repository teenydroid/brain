# Measuring self-improvement

How do you tell whether a self-improving system is getting more capable, and
not only luckier, kinder to its judge, or riding a better week? This page
collects what the published loops measured, what the AI R&D benchmarks can
and cannot tell you, how to check the judge, how much data a claim needs, and
how to separate the agent from its environment. It ends with a scoreboard for
Teeny. The research was done on 2026-11-22.

[`self-improving-agents.md`](self-improving-agents.md) covers how each loop
works. This page covers only how each one was scored. Numbers come from a
paper's abstract or body as noted. Where a figure comes from coverage, the
entry says so. Sample sizes marked *(computed)* are standard power
calculations done for this page, not taken from a source.

## The short answer

1. **Most published loops report one run, on the tasks they selected on.**
   A 2026 audit of 90 recursive self-improvement studies found 60% evaluate
   on tasks not used for selection and 22% audit two or more reruns of the
   loop (§1).
2. **"Better product" and "better improver" are different claims** and need
   different evidence. Only 4 of 26 studies claiming a better improvement
   procedure compared old and new procedures from the same start at the same
   budget (§1).
3. **Reusing one eval set across many promotions overfits it.** One 2026
   paper measured up to 20.7% false promotions in a live loop (§1).
4. **The best-known benchmarks wore out fast.** METR's suite hit its
   ceiling, SWE-bench Verified was contaminated and rejected correct fixes,
   and CORE-Bench was declared solved after its grader marked right answers
   wrong (§2).
5. **The judge is an instrument, and it moves.** It flips verdicts at
   temperature zero, prefers its own style, length and position, and a
   version change is a new instrument (§3).
6. **Small samples cannot see small gains.** Telling a 55% win rate from
   50% takes about 780 decisive comparisons. A day's win on 20 tasks is
   noise more often than not (§4).
7. **Live metrics need a holdback.** Reach and revenue move with the world.
   Only a concurrent, randomised comparison against a frozen old version
   separates the agent from the weather (§5).

---

## 1. How the published loops measured progress

| System | What was measured | Held out? | Repeats | Later criticism |
|---|---|---|---|---|
| DGM | SWE-bench Verified in stages (10, then 50 or 60, then 200 tasks); Polyglot 10 then 50 | Partly. Cross-benchmark transfer is called "truly held-out" | Not reported. The 40% gate was set "based on the noise observed in preliminary runs" | Single run; ~2 weeks and "significant API costs" per run ([arXiv:2505.22954](https://arxiv.org/html/2505.22954)) |
| HGM | SWE-Verified-60, Polyglot, then SWE-Lite (300 tasks, 93 overlapping Verified) | SWE-Lite partly; transfer GPT-5-mini to GPT-5 | One run per method apparently; no intervals | Its own finding: a node's score predicts its descendants poorly ([arXiv:2510.21614](https://arxiv.org/html/2510.21614)) |
| SICA | Fixed 50-task SWE-bench Verified subset, 50 LiveCodeBench | No split described | One run, 15 iterations, ~$7,000 | Peak (53%, iteration 14) higher than final (51%) ([arXiv:2504.15228](https://arxiv.org/html/2504.15228)) |
| ADAS | ARC: 20 validation, 60 test; others held out | Yes | Five evaluations, median with 95% bootstrap CI | Gains not uniform; on MMLU the transferred agents did not beat chain-of-thought ([arXiv:2408.08435](https://arxiv.org/html/2408.08435)) |
| GEPA | Train, validation (Pareto set), test | Yes, test reported | Not stated in the main text | "The majority of GEPA's rollout budget is spent on validation" ([arXiv:2507.19457](https://arxiv.org/html/2507.19457)) |
| AlphaEvolve | Machine-checked evaluators per problem | Not needed: the score is the truth | n/a | Works only where a checker exists ([DeepMind](https://deepmind.google/discover/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/)) |
| AI Scientist | LLM reviewer score | No | n/a | An outside audit found 42% of experiments failed on coding errors and novelty checks called known ideas new ([arXiv:2502.14297](https://arxiv.org/abs/2502.14297)) |
| AIDE² (2026) | AI R&D tasks; selection on hidden evaluations | Four held-out benchmarks, one out of distribution | One 8-day run | Reward hacking on a held-out family fell from 55% to 32% though not optimised for ([arXiv:2609.26457](https://arxiv.org/abs/2609.26457)) |

Details worth keeping:

- **DGM's transfer tests are the strongest evidence it has.** Run on Claude
  3.7 Sonnet, a model it was not evolved with, the base agent scored 19.0%
  and the DGM agent 59.5%; on o3-mini, 23.0% and 33.0%. The SWE-bench-evolved agent
  scored 28.9% on Polyglot against a 14.2% base. None of these numbers carry
  error bars ([arXiv:2505.22954](https://arxiv.org/html/2505.22954)).
- **HGM measured the thing a lineage curve assumes.** The Pearson correlation
  between a node's own score and the best score in its subtree was 0.27 to
  0.44 for DGM and SICA's selection rules. HGM's clade estimate reached 0.78
  on SWE-Verified-60 ([arXiv:2510.21614](https://arxiv.org/html/2510.21614)).
  A generation's own eval is a weak guide to whether it is a good parent.
- **SICA's numbers are from 50 tasks.** At 50% accuracy the standard error on
  50 tasks is about 7 points *(computed)*, so most iteration-to-iteration
  moves in its table sit inside the noise.
- **Hyperagents** reports that meta-level improvements "transfer across
  domains and accumulate across runs." I read only the abstract. The full
  paper's HTML returned 404, so its splits and repeat counts are unverified
  here ([arXiv:2603.19461](https://arxiv.org/abs/2603.19461)).

### The 2026 audit: five claims, five designs

Chen, Zhu and Zheng coded 114 studies, 90 of which call themselves recursive
self-improvement
([arXiv:2610.27610](https://arxiv.org/abs/2610.27610), October 2026). In a
human-coded random sample, 45% evaluated on tasks not used for selection and
14% audited two or more reruns. They map each kind of claim to the evidence it
needs ([HTML](https://arxiv.org/html/2610.27610), Table 2):

| Claim | Evidence it needs |
|---|---|
| C1 Product gain: the final system beats the first | Audited gain on held-out tasks, by a grader independent of the development judge, at matched budget |
| C2 Accumulation: gains build round by round | Audited scores at every retained round on one fixed audit set |
| C3 Repeatability: the loop reliably produces gains | Two or more independent audited reruns of the whole loop |
| C4 Improver gain: a later improver beats an earlier one | A swap test: both improvers start from the same state with the same budget, and their outputs are audited |
| C5 Compounding: improver gains grow | Repeated swap tests over a stated horizon, with total cost |

"Generation N is better than N-5" is C2. "The loop is getting smarter" is C4
or C5. Almost nobody measures those.

### Reusing the benchmark

Sun et al. show that a loop which keeps scoring candidates on one fixed set
adapts to that set. Their REUSE method limits what score feedback reaches the
search and counts every promotion against an error budget α. In live runs it
cut false promotions from up to 20.7% to 0% with similar final performance
([arXiv:2609.33180](https://arxiv.org/abs/2609.33180), September 2026). The
older version of the same point: "many agent benchmarks have inadequate
holdout sets, or none at all" ([Kapoor et al., arXiv:2407.01502](https://arxiv.org/abs/2407.01502)).

**What this means.** A loop's own development score is its training loss.
Progress claims need a set the loop never touched, scored by something the
loop never optimised against, more than once.

## 2. Benchmarks for AI doing AI R&D and long tasks

| Benchmark | Measures | Best known result | Can tell you | Cannot tell you |
|---|---|---|---|---|
| METR time horizon | Human task length at which an agent succeeds 50% of the time | Doubling every ~7 months 2019–2025 ([arXiv:2503.14499](https://arxiv.org/abs/2503.14499)); 131-day doubling since 2023 under TH1.1 ([METR](https://metr.org/blog/2026-01-29-time-horizon-1-1/)) | Trend across model generations | Anything above 16 hours: "unreliable with our current task suite" ([METR](https://metr.org/time-horizons/)) |
| RE-Bench | 7 ML research engineering environments vs 61 experts | Agents 4× humans at 2 h; humans ahead at 8 h; 2× at 32 h ([arXiv:2411.15114](https://arxiv.org/abs/2411.15114)) | How returns to time differ between agents and people | Open-ended research |
| MLE-bench | 75 Kaggle competitions, medal rate | 16.9% at launch ([arXiv:2410.07095](https://arxiv.org/abs/2410.07095)); 64.44 ± 1.18 at the top of the board ([README](https://github.com/openai/mle-bench)) | Engineering on known problem types | Anything new since submissions paused on 2026-04-24 |
| PaperBench | Replicate 20 ICML 2024 papers; 8,316 rubric items | 21.0% at launch; ML PhDs 41.4% after 48 h on a 3-paper subset ([arXiv:2504.01848](https://arxiv.org/html/2504.01848v3)) | Partial credit on long research | Fine differences: the judge's F1 against humans is 0.83 |
| CORE-Bench | Reproduce 270 tasks from 90 papers | 21% at launch on the hardest level ([arXiv:2409.11363](https://arxiv.org/abs/2409.11363)); declared solved, 77.78% auto-graded and 95.5% after manual checks ([HAL](https://hal.cs.princeton.edu/corebench_hard)) | Whether reproduction works | Its own auto-grader marked correct answers wrong |
| SWE-bench Verified | 500 human-checked GitHub issues | No longer reported by OpenAI ([OpenAI](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)) | Little now | See below |
| AIRS-Bench, ResearchGym (2026) | Research tasks from recent papers | AIRS: agents beat human state of the art on 4 of 20 tasks ([arXiv:2602.06855](https://arxiv.org/abs/2602.06855)); ResearchGym: beat the baseline in 1 of 15 runs, 26.5% of sub-tasks ([arXiv:2602.15112](https://arxiv.org/abs/2602.15112)) | Not yet saturated | Run-to-run spread is not reported |

What went wrong with them:

- **METR's own numbers moved when the tasks did.** Moving from TH1 to TH1.1
  (170 to 228 tasks) raised GPT-5's horizon by 55% and cut GPT-4 1106's by
  57%. METR puts most of the change down to the task suite and run-to-run
  noise. Only 5 of 31 tasks of 8 hours or more have measured human times
  ([METR](https://metr.org/blog/2026-01-29-time-horizon-1-1/)). A
  statistical re-analysis found AI difficulty is nearly flat between 2 and
  30 human minutes, so "3 min to 30 min is much easier than one from 30 min to
  5 hours" despite the same tenfold step
  ([Nguyen and Fithian, arXiv:2610.12466](https://arxiv.org/abs/2610.12466)).
- **METR's suite ran out of ceiling.** Coverage reports METR put Claude
  Mythos Preview at "at least 16 hours" (95% CI 8.5 to 55 hours); I did not
  reach METR's own post, only its chart page, which lists the model and the
  16-hour caveat ([coverage](https://officechai.com/ai/claude-mythos-shows-50-time-horizon-of-16-hours-on-metr-benchmark/)).
  METR's Thomas Kwa wrote that future metrics may not be "measured in human
  hours" ([LessWrong comments](https://www.lesswrong.com/posts/3SywPAjGQWCtQFafb/you-re-gonna-need-a-bigger-boat-benchmark-metr)).
- **SWE-bench Verified was contaminated and partly broken.** Models named
  the buggy file from the issue text alone 76% of the time on Verified,
  against 53% on repositories outside it
  ([arXiv:2506.12286](https://arxiv.org/abs/2506.12286)). OpenAI audited 138
  problems o3 often failed and found at least 59.4% had tests that reject
  correct fixes. Every frontier model tested could reproduce some gold
  patches verbatim ([OpenAI, 2026-02-23](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)).
  Epoch rated it "Flawed" on 2026-09-03
  ([Epoch](https://epoch.ai/benchmarks/swe-bench-verified/review)). Fresh-task
  pipelines (SWE-rebench, LiveCodeBench) collect new problems by date to
  stay ahead of training data
  ([arXiv:2505.20411](https://arxiv.org/abs/2505.20411);
  [arXiv:2403.07974](https://arxiv.org/abs/2403.07974)).
- **The grader is often the weak part.** A checklist study found task and
  reward flaws that move measured performance by up to 100% relative;
  TAU-bench counted empty responses as successes
  ([arXiv:2507.02825](https://arxiv.org/abs/2507.02825)). CORE-Bench and
  SWE-bench both rejected correct answers.
- **Scaffolds change scores.** METR saw some models score significantly
  higher under its old infrastructure than the new. On CORE-Bench, Sonnet 4.5
  reportedly scored 44% in one scaffold and 62% in another; that figure came
  from a search summary of HAL's notes, not from the page I opened
  ([HAL](https://hal.cs.princeton.edu/corebench_hard)).
- **Models notice tests.** Gemini 2.5 Pro told evaluation transcripts from
  deployment with an AUC of 0.83; people managed 0.92
  ([arXiv:2505.23836](https://arxiv.org/abs/2505.23836)).

**What this means.** A benchmark is a dated instrument with a ceiling, a
contamination clock and a grader error rate. None of these suites measures
"attention on X" or anything like Teeny's job. Their lesson is the method:
hidden tasks, fresh tasks by date, several seeds, an audited grader.

## 3. Evaluating the evaluator

The judge decides which generation wins, so the judge's error is the
loop's error. What is known:

- **Classic biases.** GPT-4 as judge agreed with people over 80% of the
  time, about as often as people agree with each other, and showed position,
  verbosity and self-enhancement bias
  ([Zheng et al., arXiv:2306.05685](https://arxiv.org/abs/2306.05685)).
  Position bias is "not due to random chance," varies by judge and task, and
  is strongest when the two answers are close in quality: the close calls
  are the ones that decide a generation
  ([Shi et al., arXiv:2406.07791](https://arxiv.org/abs/2406.07791)).
- **Self-preference.** A model's ability to recognise its own output
  correlates linearly with how much it favours it
  ([Panickssery et al., arXiv:2404.13076](https://arxiv.org/abs/2404.13076)).
  A loop whose candidates and judge share a model family is exposed.
- **Length.** AlpacaEval favoured longer outputs. Controlling for length
  raised its correlation with Chatbot Arena from 0.94 to 0.98
  ([arXiv:2404.04475](https://arxiv.org/abs/2404.04475)).
- **Leniency and the wrong statistic.** Only the largest judges reached
  reasonable alignment with people, judges lean lenient, and high percent
  agreement can hide very different scores
  ([Thakur et al., arXiv:2406.12624](https://arxiv.org/abs/2406.12624)).
- **The judge disagrees with itself.** Across three runs, judges' Krippendorff
  alpha with themselves ranged from 0.27 to 0.79; none reached the usual 0.8
  bar. A majority vote over runs raised agreement with people. Turning
  sampling off lowered it
  ([Haldar and Hockenmaier, arXiv:2510.27106](https://arxiv.org/html/2510.27106)).
  A pinned judge at temperature zero still flipped about 5% of verdicts on
  re-run, and about 40% on close-call items
  ([arXiv:2609.33044](https://arxiv.org/abs/2609.33044), one-author preprint).
- **Pairwise vs absolute.** Pairwise preferences flipped in about 35% of
  cases when the generator planted features the judge liked, against about
  9% for absolute scores
  ([Tripathi et al., arXiv:2504.14716](https://arxiv.org/abs/2504.14716)).
  Pairwise is more sensitive to real differences and also easier to game.
- **Criteria drift.** People grading outputs change their criteria as they
  grade ([Shankar et al., arXiv:2404.12272](https://arxiv.org/abs/2404.12272)).
  A golden set labelled once, early, encodes early taste.

### When the judge model changes

The "same" model can change: GPT-4 went from 84% to 51% on one task between
March and June 2023 ([arXiv:2307.09009](https://arxiv.org/abs/2307.09009)).
Three 2026 preprints, all single studies, say the same about judges:

- A judge version change is a new instrument. It can shift toward leniency
  or harshness and change resolution; one change put 93% of scores on one
  value. A stratified audit of about 200 calls caught the failures
  ([arXiv:2610.32986](https://arxiv.org/abs/2610.32986)).
- Judges overstate agent upgrades. Eight judge-only intervals declared
  upgrades that execution-based checks could not establish. Reusing a
  calibration fitted on an older agent version raised comparison error from
  3.8 to 19.5 points
  ([arXiv:2609.34198](https://arxiv.org/abs/2609.34198)).
- A fixed human-labelled anchor set, re-scored at a steady rate alongside
  live traffic, told a judge change from a system change in all 60 test
  runs. A rolling z-test raised false alarms on 75% of drift-free streams
  ([arXiv:2606.15474](https://arxiv.org/abs/2606.15474)).

### Protocols that follow from this

1. **A golden set with human labels**, including known-better pairs and
   identical pairs. Report agreement as a chance-corrected statistic
   (Cohen's kappa or Krippendorff's alpha), not percent.
2. **Judge every pair in both orders** and count a verdict only if it
   survives the swap. Report the swap-disagreement rate.
3. **Judge more than once** and report self-agreement. Majority-vote the
   runs.
4. **Two judges from different families**, as the pinned-judge preprint
   recommends ([arXiv:2609.33044](https://arxiv.org/abs/2609.33044)).
5. **An A/A test**: the same version on both sides. The share of A/A runs
   that declare a winner is the loop's false-positive rate, measured.
6. **Correct the judge's estimate with the human set.** Prediction-powered
   inference combines a small labelled set with many machine labels into a
   valid confidence interval, with no assumptions about the model
   ([Angelopoulos et al., arXiv:2301.09633](https://arxiv.org/abs/2301.09633)).
7. **Rank generations with Bradley-Terry, not running Elo.** Elo
   computations "exhibit volatility" and its reliability and transitivity
   assumptions do not always hold for LLMs
   ([Boubdir et al., arXiv:2311.17295](https://arxiv.org/abs/2311.17295)).
   Private best-of-N testing inflated Arena ranks: Meta tested 27 private
   variants before Llama 4
   ([Singh et al., arXiv:2504.20879](https://arxiv.org/abs/2504.20879)). A
   loop that promotes its best of N candidates does the same to itself.

## 4. Statistics for small samples

Miller's guidance, written up by Anthropic
([arXiv:2411.00640](https://arxiv.org/html/2411.00640);
[Anthropic](https://www.anthropic.com/research/statistical-approach-to-model-evals)):

- Report the standard error with every mean.
- Cluster standard errors when items share a source (one situation, several
  samples). Clustered errors on popular evals were "over three times as
  large as naive standard errors."
- Sample each item several times and average. In his example two samples
  cut variance by a third, four by half.
- Compare versions on the same items (paired differences). Correlations of
  0.3 to 0.7 between models make this nearly free variance reduction.
- Do a power analysis first. Detecting a 3-point difference at 80% power
  needs about 969 independent questions.

Bowyer, Aitchison and Ivanova add that with fewer than a few hundred items,
normal-approximation intervals are "usually dramatically underestimating
uncertainty"; use Bayesian or exact intervals
([arXiv:2503.01747](https://arxiv.org/abs/2503.01747)).

How many decisive pairwise comparisons (ties dropped) a win rate needs,
two-sided α = 0.05, 80% power *(computed)*:

| True win rate of new over old | Comparisons needed |
|---|---|
| 55% | ~783 |
| 60% | ~194 |
| 65% | ~85 |
| 70% | ~47 |
| 75% | ~29 |

Unpaired pass rates need more: 50% to 60% takes about 387 tasks per version,
50% to 55% about 1,565 *(computed)*.

95% Wilson intervals for an observed rate *(computed)*:

| Observed | Interval |
|---|---|
| 7 of 10 | 40% to 89% |
| 12 of 20 | 39% to 78% |
| 30 of 50 | 46% to 72% |
| 60 of 100 | 50% to 69% |

**Why "it won today" is not evidence.** With no real difference, a candidate
wins 12 or more of 20 comparisons 25% of the time. Run five candidates and
promote the best, and at least one gets there 77% of the time *(computed)*.
The promoted winner's measured gain is also biased upward, the winner's curse
that Lee and Shen measured in Airbnb's experiments (KDD 2018, DOI
10.1145/3219819.3219905; the paper and Airbnb's post returned 403, so this
rests on the search summary only). Re-measure a winner on fresh samples
before believing its size.

**Peeking.** Checking a running test and stopping when it looks good makes
ordinary p-values invalid. "Always valid" sequential p-values and confidence
intervals allow stopping at any time
([Johari et al., arXiv:1512.04922](https://arxiv.org/abs/1512.04922)). The
same applies to a loop that keeps sampling until a candidate passes.

**Pick a metric with signal.** A benchmark's usefulness is its
signal-to-noise ratio: how well it separates better models from worse ones
against its run-to-run noise. Dropping noisy subtasks and using continuous
scores raised it ([Heineman et al., arXiv:2508.13144](https://arxiv.org/abs/2508.13144)).

## 5. Capability, behaviour and outcome

Three different things get called "better":

- **Capability**: what the agent can do on a fixed task, in a fixed
  environment, scored by a fixed grader.
- **Behaviour**: what it chooses to do. Longer posts, more replies, a new
  tone. A behaviour change can move a judge with no capability change.
- **Outcome**: what happened in the world. Reach, followers, money. It
  depends on the agent and on everything else.

In a live, non-stationary setting, outcome metrics confound the agent with
the environment. A follower jump in the week after a merge may be the merge,
a news cycle, an algorithm change or regression to the mean after a bad
week. Designs that separate them:

- **Frozen regression suite.** Past situations saved verbatim, with the
  context the agent had then. Old and new versions both act on them now, and
  a judge or check compares outputs. The environment cannot be kinder to one
  of them, because it is the same recorded environment. It measures
  capability and behaviour, not outcome.
- **Canary tasks.** A few fixed tasks with known answers run every
  generation. Any change on them is the system or the judge, not the world.
- **Replay with logged outcomes.** Li et al. evaluated news-recommendation
  policies offline by replaying logged events, keeping only those where the
  new policy chose what was actually shown, and called it "provably
  unbiased" ([arXiv:1003.5956](https://arxiv.org/abs/1003.5956)). I could
  not confirm from the abstract the condition the logging policy must meet;
  in the usual statement it must be randomised. For an agent this means the
  old choices must have been partly random for replay to price a new one.
- **Concurrent holdback.** Keep a frozen old version live and assign it a
  random share of the work at the same time as the new one. Both face the
  same week. AlphaGo Zero promoted a new network only if it won by a 55%
  margin against the current best; AlphaZero later dropped that gate
  ([Silver et al., arXiv:1712.01815](https://ar5iv.labs.arxiv.org/html/1712.01815)).
  An open reimplementation reportedly used 400 games per gate; I could not
  open its primary source.
- **Switchback.** With one unit (one account), alternate versions over
  randomised time blocks. Effects carry over between blocks, so block length
  and the carryover window have to be designed, and randomisation-based
  p-values stay exact ([Bojinov et al., arXiv:2009.00148](https://arxiv.org/abs/2009.00148)).
- **External reference series.** Divide outcomes by a comparison set of
  similar accounts or platform-wide numbers. Weaker than randomisation, but
  it removes a shared trend. *(Interpretation; no source opened.)*

## 6. What real improvement looks like

Signs that a learning system has learned, and not only been lucky:

- **Learning curves on fixed material.** Scores on one frozen audit set rise
  across retained generations and stay up (C2 in §1).
- **Transfer.** A change made for one task helps tasks it was not made for,
  or carries to another model. DGM and ADAS both showed this, and it is
  their best evidence (§1).
- **No forgetting.** Backward transfer measures how learning a new task
  changes scores on earlier ones; negative is forgetting. Forward transfer
  measures how much it helps tasks not yet seen
  ([Lopez-Paz and Ranzato, arXiv:1706.08840](https://arxiv.org/html/1706.08840)).
- **Sample efficiency.** Chollet defines intelligence as "skill-acquisition
  efficiency" ([arXiv:1911.01547](https://arxiv.org/abs/1911.01547)). For a
  loop: fewer candidates, rollouts or days to reach a given gain over time.
- **Error types shift.** Old failure classes become rare and new, harder
  ones appear. A loop that fixes the same class twice has not learned it.
  *(Interpretation.)*
- **Fewer repeated mistakes.** A mistake recorded as a lesson should not
  recur. AIDE²'s falling reward-hacking rate on a held-out family is the
  kind of evidence that counts: a behaviour improving where it was not
  optimised ([arXiv:2609.26457](https://arxiv.org/abs/2609.26457)).
- **Novelty that survives.** A new behaviour still in the champion several
  generations later, and still winning on the audit set. HGM's result says
  to judge a change by what its descendants did, not by its own day
  ([arXiv:2510.21614](https://arxiv.org/html/2510.21614)).
- **A better improver.** The strongest sign, and the rarest measurement: the
  improver from generation N, started from generation 0's brain with the
  same budget, beats the improver from generation 0 (the swap test, C4).

---

## A scoreboard for Teeny

Five measures, run on a schedule, by frozen code the proposer cannot see.
Each answers one question. The sample sizes are the minimum to detect the
stated effect at α = 0.05 and 80% power *(computed)*; replace the assumed
variances with measured ones after the first month.

### 1. A frozen replay suite: "is N better than N-5 on the same situations?"

- **What.** Real past situations (a mention, a news item, a slot to fill),
  frozen with the context Teeny had, stored outside the repository and never
  shown to the proposer. Each generation's champion, the champion from five
  generations back, and a fixed generation-0 anchor all act on every
  situation. Hard checks score what can be checked; the judge scores the
  rest, pairwise, both orders.
- **Minimum.** To tell a 60% win rate from 50%: ~194 decisive comparisons,
  about 100 situations × 2 samples a side once ties are dropped. To see
  55%: ~783. Cluster errors by situation. Below 100 situations, report
  "no detectable difference" rather than a winner.
- **Hygiene.** Add new situations by date and retire the oldest a quarter at
  a time, so the suite does not become training data. Keep a sealed quarter
  that is scored only monthly, for C2 claims (§1).

### 2. A judge calibration check: "is the instrument the same?"

- **What.** A golden set of pairs labelled by the operator, including clear
  wins, A/A pairs and pairs that differ only in length. Measured each week
  and on any judge model or prompt change: agreement with the labels (kappa),
  swap disagreement, self-agreement over three runs, the length effect, and
  the A/A false-positive rate.
- **Minimum.** To pin 80% agreement to ±5 points: ~246 labelled pairs;
  ±10 points: ~61 *(computed)*. Start at 60 and grow. The A/A rate needs
  20 or more A/A runs before it means much (a 5% rate is 1 in 20).
- **Rule.** A judge change is a new instrument: re-score the anchor set
  before any generation is judged with it, and never compare generations
  across a judge change without re-judging both under the new one.

### 3. A lineage curve: "is the line rising?"

- **What.** Every champion joins a pool. Monthly, a round-robin on the
  replay suite feeds a Bradley-Terry fit with bootstrap intervals, anchored
  at generation 0. Plot strength against generation number. Claim "N beats
  N-5" only when the intervals separate.
- **Minimum.** Adjacent generations are usually close; separating a 55%
  pair needs ~780 comparisons, so compare across gaps of five generations,
  where the difference should be larger, and spend comparisons there.
- **Twice a year,** run a swap test: the current improver and generation 0's
  improver each run a few generations from the same start with the same
  budget, and their products go through measures 1 and 3. That is the only
  test of "the loop got smarter" (C4).

### 4. Outside-world metrics with a holdback: "did it help in the world?"

- **What.** A frozen reference version (the champion from five generations
  back, refreshed quarterly) drafts a random share of posts, say one in five,
  chosen by a coin flip per slot, under the same rules and caps. Compare
  reach per post, replies and follows per post between the two arms in the
  same weeks. Use log reach; posts are heavy-tailed.
- **Minimum.** If the standard deviation of log reach per post is about 1.2
  (an assumption; measure it), detecting a 20% lift needs ~565 posts per
  arm, a 30% lift ~251 *(computed)*. At a handful of posts a day that is a
  quarterly measurement, not a daily one. A switchback by day needs more
  units, because days carry over.
- **Read with it:** the same numbers for a fixed set of comparison accounts,
  as a trend check.

### 5. A mistake ledger: "does it stop repeating itself?"

- **What.** Every failure tagged by type when it happens. Per generation:
  recurrences of a type after a lesson about it was recorded, and the mix of
  types. Learning shows as recurrences falling and the mix moving to new,
  harder failures.
- **Minimum.** Counts are small, so pool by month and use exact Poisson
  intervals. A type needs about ten past occurrences before its recurrence
  rate says anything.

### Rules for the whole board

1. **Pre-register** each measure, its sample size and its decision rule
   before the run. No stopping early unless the test is sequentially valid
   ([arXiv:1512.04922](https://arxiv.org/abs/1512.04922)).
2. **Report intervals,** never a bare win.
3. **Re-measure the winner** on fresh samples before recording its gain.
4. **One claim, one design.** A day's promotion is a C1 claim on a small
   set at best. "Teeny is getting smarter" needs measures 1, 3 and the swap
   test to agree.
5. **Keep the board out of the proposer's sight**, as with the held-out
   tasks. Every loop that could see its scorer gamed it
   ([`rsi-theory-and-safety.md`](rsi-theory-and-safety.md)).

## Least certain

1. Hyperagents' measurement protocol: abstract only; the HTML returned 404.
2. The 2026 judge-drift findings (§3) are single preprints, mostly by one
   author each, not yet replicated.
3. Mythos's 16-hour horizon and its interval: coverage, not METR's post.
4. Lee and Shen's winner's-curse paper: not opened (403).
5. The replay method's logging condition: not stated in the abstract.
6. The AlphaGo Zero 400-game gate: from a reimplementation, not opened.
7. Every sample size on this page assumes independent items and a stated
   variance; real items cluster, so treat them as floors.
