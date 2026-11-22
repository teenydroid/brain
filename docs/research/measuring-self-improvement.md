# Measuring self-improvement

How do you tell whether a self-improving system is getting more capable, and
not only luckier, kinder to its judge, or riding a better week? This page
collects what the published loops measured, what the AI R&D benchmarks can
and cannot tell you, how to check the judge, how much data a claim needs, and
how to separate the agent from its environment. It ends with a scoreboard for
Teeny. The research was done on 2026-11-22.

[`self-improving-agents.md`](self-improving-agents.md) covers how each loop
works. This page covers only how each one was scored. Where a figure comes
from coverage or a search summary rather than the source, the entry says so.
Sample sizes marked *(computed)* are standard power calculations done for
this page, not taken from a source.

## The short answer

1. **Most published loops report one run, often on the tasks they selected
   on.** A 2026 audit of 90 recursive self-improvement studies found 60%
   evaluate on tasks not used for selection and 22% audit two or more reruns
   of the loop (§1).
2. **"Better product" and "better improver" are different claims.** Only 4
   of 26 studies claiming a better improvement procedure compared old and new
   procedures from the same start at the same budget (§1).
3. **Reusing one eval set across many promotions overfits it.** One 2026
   paper measured up to 20.7% false promotions in a live loop (§1).
4. **The best-known benchmarks wore out fast.** METR's suite hit its
   ceiling, SWE-bench Verified was contaminated and rejected correct fixes,
   and CORE-Bench was declared solved after its grader marked right answers
   wrong (§2).
5. **The judge is an instrument, and it moves.** It flips verdicts at
   temperature zero, favours its own outputs, length and position, and a
   version change is a new instrument (§3).
6. **Small samples cannot see small gains.** Telling a 55% win rate from
   50% takes about 780 decisive comparisons. With no real difference, a
   candidate wins 12 of 20 one time in four (§4).
7. **Live metrics need a holdback.** Only a concurrent, randomised
   comparison with a frozen old version separates agent from weather (§5).

---

## 1. How the published loops measured progress

| System | What was measured | Held out? | Repeats | Later criticism |
|---|---|---|---|---|
| DGM | SWE-bench Verified in stages (10, then 50 or 60, then 200 tasks); Polyglot 10 then 50 | Partly. Cross-benchmark transfer is called "truly held-out" | Not reported. The 40% gate was set "based on the noise observed in preliminary runs" | About two weeks and "significant API costs" per run, so one run ([arXiv:2505.22954](https://arxiv.org/html/2505.22954)) |
| HGM | SWE-Verified-60, Polyglot, then SWE-Lite (300 tasks, 93 overlapping Verified) | Partly; transfer from GPT-5-mini to GPT-5 | Apparently one run per method; no intervals | Its own finding: a node's score predicts its descendants poorly ([arXiv:2510.21614](https://arxiv.org/html/2510.21614)) |
| SICA | Fixed 50-task SWE-bench Verified subset, 50 LiveCodeBench | No split described | One run, 15 iterations, about $7,000 | Peak (53%, iteration 14) above final (51%) ([arXiv:2504.15228](https://arxiv.org/html/2504.15228)) |
| ADAS | ARC: 20 validation, 60 test; other domains held out | Yes | Five evaluations, median with 95% bootstrap CI | On MMLU the transferred agents did not beat chain-of-thought ([arXiv:2408.08435](https://arxiv.org/html/2408.08435)) |
| GEPA | Train, validation (Pareto set), test | Yes, test reported | Not stated in the main text | "The majority of GEPA's rollout budget is spent on validation" ([arXiv:2507.19457](https://arxiv.org/html/2507.19457)) |
| AlphaEvolve | Machine-checked evaluators per problem | Not needed: the score is the truth | n/a | Works only where a checker exists ([DeepMind](https://deepmind.google/discover/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/)) |
| AI Scientist | LLM reviewer score | No | n/a | An outside audit found 42% of experiments failed on coding errors, and novelty checks called known ideas new ([arXiv:2502.14297](https://arxiv.org/abs/2502.14297)) |
| AIDE² (2026) | AI R&D tasks; selection on hidden evaluations | Four held-out benchmarks, one out of distribution | One 8-day run | Reward hacking on a held-out family fell from 55% to 32%, though not optimised for ([arXiv:2609.26457](https://arxiv.org/abs/2609.26457)) |

- **DGM's transfer tests are its strongest evidence.** On Claude 3.7
  Sonnet, a model it was not evolved with, the base agent scored 19.0% and
  the DGM agent 59.5%; on o3-mini, 23.0% and 33.0%. The SWE-bench-evolved
  agent scored 28.9% on Polyglot against a 14.2% base. None carry error bars
  ([arXiv:2505.22954](https://arxiv.org/html/2505.22954)).
- **HGM measured what a lineage curve assumes.** The correlation between a
  node's own score and the best score in its subtree was 0.27 to 0.44 for
  the DGM and SICA selection rules. HGM's clade estimate reached 0.78 on
  SWE-Verified-60 ([arXiv:2510.21614](https://arxiv.org/html/2510.21614)).
  A generation's own eval is a weak guide to whether it is a good parent.
- **SICA's numbers are from 50 tasks.** At 50% accuracy the standard error
  on 50 tasks is about 7 points *(computed)*, so most iteration-to-iteration
  moves in its table sit inside the noise.
- **Hyperagents** says meta-level gains "transfer across domains and
  accumulate across runs"; abstract only, the HTML returned 404
  ([arXiv:2603.19461](https://arxiv.org/abs/2603.19461)).

### The 2026 audit: five claims, five designs

Chen, Zhu and Zheng coded 114 studies, 90 of which call themselves recursive
self-improvement ([arXiv:2610.27610](https://arxiv.org/abs/2610.27610),
October 2026). In a human-coded random sample, 45% evaluated on tasks not
used for selection and 14% audited two or more reruns. They map each claim to
the evidence it needs ([HTML](https://arxiv.org/html/2610.27610), Table 2):

| Claim | Evidence it needs |
|---|---|
| C1 Product gain: the final system beats the first | Audited gain on held-out tasks, by a grader independent of the development judge, at matched budget |
| C2 Accumulation: gains build round by round | Audited scores at every retained round on one fixed audit set |
| C3 Repeatability: the loop reliably produces gains | Two or more independent audited reruns of the whole loop |
| C4 Improver gain: a later improver beats an earlier one | A swap test: both start from the same state with the same budget, and their outputs are audited |
| C5 Compounding: improver gains grow | Repeated swap tests over a stated horizon, with total cost |

"Generation N is better than N-5" is C2. "The loop is getting smarter" is C4
or C5. Almost nobody measures those.

### Reusing the benchmark

Sun et al. show that a loop which keeps scoring candidates on one fixed set
adapts to that set. Their REUSE method limits what score feedback reaches the
search and counts every promotion against an error budget α. In live runs it
cut false promotions from up to 20.7% to 0% with similar final performance
([arXiv:2609.33180](https://arxiv.org/abs/2609.33180), September 2026). The
older form of the point: many agent benchmarks "have inadequate holdout sets,
or none at all" ([Kapoor et al., arXiv:2407.01502](https://arxiv.org/abs/2407.01502)).

**What this means.** A loop's own development score is its training loss.
A progress claim needs a set the loop never touched, scored by something the
loop never optimised against, more than once.

## 2. Benchmarks for AI doing AI R&D and long tasks

| Benchmark | Measures | Results | Can tell you | Cannot tell you |
|---|---|---|---|---|
| METR time horizon | Human task length at which an agent succeeds 50% of the time | Doubling about every 7 months, 2019–2025 ([arXiv:2503.14499](https://arxiv.org/abs/2503.14499)); 131 days since 2023 under TH1.1 ([METR](https://metr.org/blog/2026-01-29-time-horizon-1-1/)) | The trend across model generations | Anything above 16 hours: "unreliable with our current task suite" ([METR](https://metr.org/time-horizons/)) |
| RE-Bench | 7 ML research engineering environments, 61 experts | Agents 4× humans at 2 h; humans narrowly ahead at 8 h, 2× at 32 h ([arXiv:2411.15114](https://arxiv.org/abs/2411.15114)) | How returns to time differ | Open-ended research |
| MLE-bench | 75 Kaggle competitions, medal rate | 16.9% at launch ([arXiv:2410.07095](https://arxiv.org/abs/2410.07095)); top entry 64.44 ± 1.18, at least 3 seeds ([README](https://github.com/openai/mle-bench)) | Engineering on known problem types | Anything since submissions paused on 2026-04-24 |
| PaperBench | Replicate 20 ICML 2024 papers; 8,316 rubric items | 21.0% at launch; ML PhDs 41.4% after 48 h on a 3-paper subset ([arXiv:2504.01848](https://arxiv.org/html/2504.01848v3)) | Partial credit on long research | Fine differences: the judge's F1 against human grades is 0.83 |
| CORE-Bench | Reproduce 270 tasks from 90 papers | 21% at launch, hardest level ([arXiv:2409.11363](https://arxiv.org/abs/2409.11363)); declared solved at 77.78% auto-graded, 95.5% after manual checks ([HAL](https://hal.cs.princeton.edu/corebench_hard)) | Whether reproduction works | Its auto-grader marked correct answers wrong |
| SWE-bench Verified | 500 human-checked GitHub issues | OpenAI stopped reporting it ([OpenAI](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)) | Little now | See below |
| AIRS-Bench, ResearchGym (2026) | Research tasks from recent papers | AIRS: agents beat human state of the art on 4 of 20 ([arXiv:2602.06855](https://arxiv.org/abs/2602.06855)); ResearchGym: beat the baseline in 1 of 15 runs, 26.5% of sub-tasks ([arXiv:2602.15112](https://arxiv.org/abs/2602.15112)) | Not saturated yet | Run-to-run spread is not reported |

What went wrong with them:

- **METR's numbers moved when the tasks did.** TH1 to TH1.1 (170 to 228
  tasks) raised GPT-5's horizon by 55% and cut GPT-4 1106's by 57%. METR puts
  most of the change down to the task suite and run-to-run noise. Only 5 of
  31 tasks of 8 hours or more have measured human times
  ([METR](https://metr.org/blog/2026-01-29-time-horizon-1-1/)). A
  re-analysis found AI difficulty nearly flat between 2 and 30 human
  minutes, so "a time-horizon jump from 3 min to 30 min is much easier than
  one from 30 min to 5 hours"
  ([Nguyen and Fithian, arXiv:2610.12466](https://arxiv.org/abs/2610.12466)).
- **METR's suite ran out of ceiling.** Coverage reports METR put Claude
  Mythos Preview at "at least 16 hours" (95% CI 8.5 to 55 hours). I reached
  only METR's chart page, which lists the model and the 16-hour caveat
  ([coverage](https://officechai.com/ai/claude-mythos-shows-50-time-horizon-of-16-hours-on-metr-benchmark/)).
  METR's Thomas Kwa wrote that many new metrics "wouldn't be measured in
  human hours" ([LessWrong comments](https://www.lesswrong.com/posts/3SywPAjGQWCtQFafb/you-re-gonna-need-a-bigger-boat-benchmark-metr)).
- **SWE-bench Verified was contaminated and partly broken.** Models named
  the buggy file from the issue text alone up to 76% of the time, against
  53% on repositories outside the benchmark
  ([arXiv:2506.12286](https://arxiv.org/abs/2506.12286)). OpenAI audited 138
  problems o3 often failed and found at least 59.4% had tests that reject
  correct fixes; every frontier model tested reproduced some gold patches or
  problem details verbatim
  ([OpenAI, 2026-02-23](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)).
  Epoch rated it "Flawed" on 2026-09-03
  ([Epoch](https://epoch.ai/benchmarks/swe-bench-verified/review)). Fresh-task
  pipelines collect new problems by date to stay ahead of training data
  ([SWE-rebench](https://arxiv.org/abs/2505.20411);
  [LiveCodeBench](https://arxiv.org/abs/2403.07974)).
- **The grader is often the weak part.** A checklist study found task and
  reward flaws that misstate performance by up to 100% relative; TAU-bench
  counted empty responses as successes
  ([arXiv:2507.02825](https://arxiv.org/abs/2507.02825)).
- **Scaffolds and tests change scores.** METR's move to new infrastructure
  changed some models' results, and Gemini 2.5 Pro told evaluation
  transcripts from deployment with an AUC of 0.83
  ([arXiv:2505.23836](https://arxiv.org/abs/2505.23836)).

**What this means.** A benchmark is a dated instrument with a ceiling, a
contamination clock and a grader error rate. None measures Teeny's job; take
the method: hidden tasks, fresh tasks by date, several seeds, audited
graders.

## 3. Evaluating the evaluator

The judge decides which generation wins, so the judge's error is the loop's
error.

- **Classic biases.** GPT-4 as judge agreed with people over 80% of the
  time, about as often as people agree with each other, and showed position,
  verbosity and self-enhancement bias
  ([Zheng et al., arXiv:2306.05685](https://arxiv.org/abs/2306.05685)).
  Position bias is "not due to random chance," varies by judge and task, and
  depends strongly on the quality gap between the answers
  ([Shi et al., arXiv:2406.07791](https://arxiv.org/abs/2406.07791)).
- **Self-preference.** How well a model recognises its own output
  correlates linearly with how much it favours it
  ([Panickssery et al., arXiv:2404.13076](https://arxiv.org/abs/2404.13076)).
  A judge from the same family as the candidates is exposed.
- **Length and leniency.** Controlling AlpacaEval for length raised its
  correlation with Chatbot Arena from 0.94 to 0.98
  ([arXiv:2404.04475](https://arxiv.org/abs/2404.04475)). Judges lean
  lenient, and high percent agreement can hide very different scores
  ([Thakur et al., arXiv:2406.12624](https://arxiv.org/abs/2406.12624)).
- **The judge disagrees with itself.** Over three runs, judges' Krippendorff
  alpha with themselves ranged from 0.27 to 0.79; none reached the usual 0.8
  bar. A majority vote over runs raised agreement with people; turning
  sampling off lowered it
  ([Haldar and Hockenmaier, arXiv:2510.27106](https://arxiv.org/html/2510.27106)).
  A pinned judge at temperature zero still flipped about 5% of verdicts on
  re-run, and about 40% on close calls
  ([arXiv:2609.33044](https://arxiv.org/abs/2609.33044), one-author preprint).
- **Pairwise vs absolute.** When the generator planted features the judge
  liked, pairwise preferences flipped in about 35% of cases and absolute
  scores in about 9% ([Tripathi et al., arXiv:2504.14716](https://arxiv.org/abs/2504.14716)).
- **Criteria drift.** People change their criteria as they grade
  ([Shankar et al., arXiv:2404.12272](https://arxiv.org/abs/2404.12272)). A
  golden set labelled once, early, encodes early taste.

### When the judge model changes

The "same" model can change: GPT-4 fell from 84% to 51% on one task between
March and June 2023 ([arXiv:2307.09009](https://arxiv.org/abs/2307.09009)).
Three 2026 preprints, each a single study, say the same of judges:

- A version change is a new instrument. It can shift toward leniency or
  harshness and change resolution; one put 93% of scores on a single value.
  A stratified audit of about 200 calls identified the failure
  ([arXiv:2610.32986](https://arxiv.org/abs/2610.32986)).
- Judges overstate agent upgrades. Eight judge-only intervals declared
  upgrades that execution-based checks could not establish, and reusing a
  calibration fitted on an older agent version raised comparison error from
  3.8 to 19.5 points ([arXiv:2609.34198](https://arxiv.org/abs/2609.34198)).
- A fixed human-labelled anchor set, re-scored steadily alongside live
  traffic, told a judge change from a system change in all 60 test runs. A
  rolling z-test raised false alarms on 75% of drift-free streams
  ([arXiv:2606.15474](https://arxiv.org/abs/2606.15474)).

### Protocols that follow

A human-labelled golden set scored with kappa or alpha, not percent; both
orders; several runs, majority-voted; two judges from different families
([arXiv:2609.33044](https://arxiv.org/abs/2609.33044)); A/A runs, whose
share of declared winners is the measured false-positive rate. To fold the
golden set into the estimate, prediction-powered inference gives valid
intervals from a few human labels plus many machine ones
([arXiv:2301.09633](https://arxiv.org/abs/2301.09633)). To rank
generations, fit Bradley-Terry over all pairs rather than running Elo, whose
computations "exhibit volatility" and whose transitivity assumption is "not
always satisfied" ([arXiv:2311.17295](https://arxiv.org/abs/2311.17295)).
Promoting the best of N is its own bias: Meta tested 27 private variants on
Arena before Llama 4 ([arXiv:2504.20879](https://arxiv.org/abs/2504.20879)).

## 4. Statistics for small samples

Miller's guidance, published by Anthropic
([arXiv:2411.00640](https://arxiv.org/html/2411.00640);
[Anthropic](https://www.anthropic.com/research/statistical-approach-to-model-evals)):

- Report the standard error with every mean.
- Cluster standard errors when items share a source. Clustered errors on
  popular evals were "over three times as large as naive standard errors."
- Sample each item several times. In his example two samples cut variance
  by a third, four by half.
- Compare versions on the same items (paired differences). Correlations of
  0.3 to 0.7 between models make this nearly free variance reduction.
- Do a power analysis first. A 3-point difference at 80% power needs about
  969 independent questions.

With fewer than a few hundred items, normal-approximation intervals are
"usually dramatically underestimating uncertainty"; use Bayesian or other
alternatives ([Bowyer et al., arXiv:2503.01747](https://arxiv.org/abs/2503.01747)).

Decisive pairwise comparisons (ties dropped) needed to tell a win rate from
50%, two-sided α = 0.05, 80% power *(computed)*:

| True win rate of new over old | 55% | 60% | 65% | 70% | 75% |
|---|---|---|---|---|---|
| Comparisons needed | ~783 | ~194 | ~85 | ~47 | ~29 |

Unpaired pass rates need more: 50% to 60% takes about 387 tasks per version
*(computed)*. The 95% Wilson interval for 12 of 20 is 39% to 78%; for 60 of
100, 50% to 69% *(computed)*.

**Why "it won today" is not evidence.** With no real difference, a candidate
wins 12 or more of 20 comparisons 25% of the time. Run five candidates and
promote the best, and at least one gets there 77% of the time *(computed)*.
A promoted winner's measured gain is also biased upward, the winner's curse
of Lee and Shen (KDD 2018, DOI 10.1145/3219819.3219905; not opened, 403).
Re-measure a winner on fresh samples before recording its size.

**Peeking.** Stopping a test when it looks good makes ordinary p-values
invalid. "Always valid" p-values and intervals allow stopping at any time
([Johari et al., arXiv:1512.04922](https://arxiv.org/abs/1512.04922)). A
loop that keeps sampling until a candidate passes is peeking.

**Pick a metric with signal**, one that separates better from worse
against its noise; dropping noisy subtasks raised that ratio
([arXiv:2508.13144](https://arxiv.org/abs/2508.13144)).

## 5. Capability, behaviour and outcome

Three things get called "better": **capability** (what the agent can do on
a fixed task with a fixed grader), **behaviour** (what it chooses to do; a
tone or length change can move a judge with no capability change) and
**outcome** (reach, followers, money, which depend on everything else too).

A follower jump the week after a merge may be the merge, a news cycle, an
algorithm change, or regression to the mean after a bad week. Designs that
separate them:

- **Frozen regression suite.** Past situations saved with their context;
  old and new versions act on the same recording, so the world cannot favour
  either. It measures capability and behaviour, not outcome.
- **Canary tasks.** Fixed tasks with known answers. A change on them is the
  system or the judge, never the world.
- **Replay with logged outcomes.** Li et al. scored news-recommendation
  policies offline by replaying logs, "provably unbiased"
  ([arXiv:1003.5956](https://arxiv.org/abs/1003.5956)). The usual condition,
  not stated in the abstract, is a randomised logging policy.
- **Concurrent holdback.** Keep a frozen old version live and give it a
  random share of the work at the same time. Both face the same week.
  AlphaGo Zero replaced its best network only when a new one "won by a
  margin of 55%"; AlphaZero later dropped the gate
  ([Silver et al., arXiv:1712.01815](https://ar5iv.labs.arxiv.org/html/1712.01815)).
- **Switchback.** With one unit (one account), alternate versions over
  random time blocks. Effects carry over between blocks, so block length has
  to be designed; randomisation-based p-values stay exact
  ([Bojinov et al., arXiv:2009.00148](https://arxiv.org/abs/2009.00148)).
- **A reference series** of similar accounts removes a shared trend, more
  weakly than randomisation. *(Interpretation; no source.)*

## 6. What real improvement looks like

- **A learning curve on a sealed audit set** that rises across retained
  generations and stays up (C2).
- **Transfer** to tasks or models a change was not made for, DGM's and
  ADAS's best evidence (§1).
- **No forgetting.** Backward transfer is how a new lesson changes scores on
  old tasks; forward transfer, how it helps unseen ones
  ([arXiv:1706.08840](https://arxiv.org/html/1706.08840)).
- **Sample efficiency.** Chollet defines intelligence as "skill-acquisition
  efficiency" ([arXiv:1911.01547](https://arxiv.org/abs/1911.01547)): fewer
  candidates or days per unit of gain, over time.
- **Fewer repeated mistakes, and error types shifting** to new, harder ones
  *(interpretation)*. AIDE²'s falling reward hacking on a held-out family is
  the pattern: improvement where nothing optimised for it (§1).
- **Novelty that survives** in the champion generations later. HGM judges a
  change by its descendants, not its own day (§1).
- **A better improver**, the rarest measurement: the swap test (C4).

---

## A scoreboard for Teeny

Five measures, run on a schedule by frozen code the proposer cannot see.
Sample sizes are minimums for α = 0.05 and 80% power *(computed)*. Replace
the assumed variances with measured ones after the first month.

### 1. A frozen replay suite: is N better than N-5 on the same situations?

- **What.** Real past situations (a mention, a news item, a slot to fill),
  frozen with their context, never shown to the proposer. The champion, the
  champion five generations back and a fixed generation-0 anchor all act on
  each. Hard checks where possible; otherwise the judge, pairwise, both
  orders.
- **Minimum.** To tell a 60% win rate from 50%: ~194 decisive comparisons,
  for example 100 situations × 2 samples, more if many tie. To see 55%:
  ~783. Cluster errors by situation. Below that, report "no detectable
  difference," never a winner.
- **Hygiene.** Add situations by date, retire the oldest quarter at a time,
  and keep a sealed quarter scored only monthly, for C2 claims.

### 2. A judge calibration check: is the instrument the same?

- **What.** Operator-labelled pairs: clear wins, A/A pairs, and pairs that
  differ only in length. Weekly, and on any judge model or prompt change:
  kappa against the labels, swap disagreement, self-agreement over three
  runs, the length effect, the A/A false-positive rate.
- **Minimum.** To pin 80% agreement to ±10 points: ~61 labelled pairs; to
  ±5 points: ~246 *(computed)*. Start at 60 and grow. A 5% false-positive
  rate is one in 20, so the A/A rate needs 20 or more runs to mean anything.
- **Rule.** A judge change is a new instrument: re-score the anchor set
  first, and re-judge both sides before comparing across it.

### 3. A lineage curve: is the line rising?

- **What.** Every champion joins a pool. Monthly, a round-robin on the
  replay suite feeds a Bradley-Terry fit with bootstrap intervals, anchored
  at generation 0. Plot strength against generation. Claim "N beats N-5"
  only when the intervals separate.
- **Minimum.** A 55% pair needs ~783 comparisons, so spend them on gaps of
  five generations, where differences should be larger.
- **Twice a year, a swap test:** today's improver and generation 0's each
  run a few generations from one start and budget, scored by measures 1 and
  3. It is the only test of "the loop got smarter."

### 4. Outside-world metrics with a holdback: did it help in the world?

- **What.** A frozen reference version (the champion five generations back,
  refreshed quarterly) drafts a random share of posts, chosen by a coin flip
  per slot, under the same rules and caps. Compare reach, replies and follows
  per post between the arms in the same weeks, on a log scale.
- **Minimum.** Assuming a standard deviation of log reach per post of 1.2
  (measure it), a 20% lift needs ~680 posts per arm with an even split; with
  one post in five held back, ~425 held-back posts and ~2,100 in all. A 30%
  lift needs ~330 per arm *(computed)*. At a few posts a day this is a
  quarterly reading, not a daily one.
  Read it beside the same numbers for a fixed set of comparison accounts.

### 5. A mistake ledger: does it stop repeating itself?

- **What.** Every failure tagged by type. Per generation: recurrences of a
  type after a lesson about it was recorded, and the mix of types.
- **Minimum.** Counts are small: pool by month and use exact Poisson
  intervals. A type needs about ten past occurrences before its recurrence
  rate says anything. *(Rule of thumb, not a source.)*

### Rules for the whole board

1. **Pre-register** each measure, its sample size and its decision rule. No
   early stopping unless the test is sequentially valid.
2. **Intervals, never a bare win;** re-measure a winner on fresh samples.
3. **One claim, one design.** A day's promotion is at best a C1 claim on a
   small set. "Teeny is getting smarter" needs measures 1 and 3 and the swap
   test to agree.
4. **Keep the board out of the proposer's sight,** like the held-out tasks.
   Every loop that could see its scorer gamed it
   ([`rsi-theory-and-safety.md`](rsi-theory-and-safety.md)).

## Least certain

1. Hyperagents' measurement protocol: abstract only; the HTML returned 404.
2. The 2026 judge-drift findings (§3) are single preprints, mostly by one
   author each, not yet replicated.
3. Mythos's 16-hour horizon and its interval: coverage, not METR's post.
4. The winner's-curse attribution to Lee and Shen: paper not opened (403).
5. The replay method's logging condition: not in the abstract.
6. Every sample size here assumes independent items and a guessed variance.
   Real items cluster, so treat them as floors.
