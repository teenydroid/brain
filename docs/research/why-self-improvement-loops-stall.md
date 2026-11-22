# Why self-improvement loops stall

Why loops that change their own prompts, scaffold or code plateau, churn, or
ship changes that aren't real improvements, and what the loops that did
compound did differently. The systems themselves are described in
[`self-improving-agents.md`](self-improving-agents.md), and Goodhart and
reward hacking as safety problems in
[`rsi-theory-and-safety.md`](rsi-theory-and-safety.md). Neither is repeated
here except where a number is needed.

Numbers come from the linked paper's abstract, tables or text. Where a number
is my own arithmetic it says *(computed)*. The research for this page was done
on 2026-11-22.

## The short answer

A loop compounds only when the change it keeps is better, not just luckier.

1. **Signal.** The scorer's noise is usually large next to the effect of one
   small edit. A loop that picks winners from noise ships noise.
2. **Search.** Keeping only the latest or best variant stalls fast. Loops that
   compounded kept an archive and built on stepping stones.
3. **Proposals.** Asked for "an improvement", a model gives its most typical
   idea, again and again.
4. **Reflection.** The gains come from reading specific failures with an
   outside check. Reflection without new information adds little.
5. **Ceiling.** A frozen model caps the loop. Verifiers, tools and more tries
   move results. Rewording mostly doesn't.

Goodharting runs through all five: the weaker the signal, the more of the
apparent progress is the loop learning its judge.

---

## 1. The signal is weaker than the effect

### Noise against effect size

- **Format alone moves scores a lot.** Changing prompt format without
  changing meaning (separators, casing, spacing) moved few-shot accuracy by
  "up to 76 accuracy points" on LLaMA-2-13B, and the spread didn't go away
  with scale or instruction tuning ([Sclar et al., 2023](https://arxiv.org/abs/2310.11324)).
  A small prompt edit can move a score by more than its real effect, in either
  direction.
- **Evals are experiments.** Report a standard error with every score; cluster
  it when questions share a source ("over three times as large as naive
  standard errors" on popular evals); resample each question; compare on
  paired differences; and run a power analysis first
  ([Anthropic, 2024](https://www.anthropic.com/research/statistical-approach-to-model-evals);
  [Miller, arXiv:2411.00640](https://arxiv.org/abs/2411.00640)).
- **Sample size grows with the square of the effect.** For 95% confidence and
  80% power, n = 16σ²/Δ² per arm. Detecting a 20% change instead of 5% cut one
  example from 122,000 users to 7,600
  ([Kohavi et al., 2009](https://ai.stanford.edu/~ronnyk/2009controlledExperimentsOnTheWebSurvey.pdf), §3.2).

**What that means for a pairwise judge** *(computed: normal approximation,
two-sided 5%, 80% power, no ties)*:

| True candidate win rate | Pairs needed |
|---|---|
| 0.70 | about 47 |
| 0.65 | about 85 |
| 0.60 | about 194 |
| 0.55 | about 783 |

An edit that really improves one behaviour a little is a 0.55 change, not a
0.70 one. Tens of pairs can't see it.

### Too few samples per candidate

The clearest measurement is Gideoni, Risi and Gal,
[*Simple Baselines are Competitive with Code Evolution*](https://arxiv.org/abs/2602.16805)
(2026). Plain random sampling, and sampling conditioned on earlier tries,
matched or beat purpose-built evolution systems in three domains. On agent
scaffolds:

- "High variance in the scaffolds coupled with small datasets leads to
  suboptimal scaffolds being selected."
- With about 100 validation questions and 10 evaluations each, accuracy still
  had a standard deviation above 1% on AIME 2025. Halving it needed more than
  60 evaluations per question, over 20 times the default cost.
- Automated methods lost more than 10 points from validation to test; the
  majority-vote baselines about 5. Every automated method ended below a
  hand-written majority-vote scaffold on test.
- Changing the *search space* (reformulating one maths problem) helped every
  method more than any search algorithm did. The hard part is "designing good
  search spaces … and not the search itself."

The loops that worked paid for evaluation. DGM scored each child on 10 tasks,
then 50 or 60, and only the best on 200, with a stage threshold "chosen based
on the noise observed in preliminary runs." A SWE-bench run cost about USD
22,000 and took about two weeks ([Zhang et al., 2025](https://arxiv.org/abs/2505.22954), §3, App. E).

### The winner's curse and regression to the mean

The candidate that scored best, or the one that passed a bar, is biased upward
by the noise that let it through.

- **Exaggeration at low power.** At power 0.06 a "significant" result has a
  24% chance of the wrong sign and overstates the true effect 9.7 times on
  average. Exaggeration starts below 50% power, sign errors below 10%
  ([Gelman and Carlin, 2014](https://sites.stat.columbia.edu/gelman/research/published/retropower_final.pdf)).
- **The same in A/B tests.** Selecting on significance "introduces bias, also
  known as 'winner's curse'" ([Deng et al., KDD 2021](https://arxiv.org/abs/1910.03788),
  citing Lee and Shen, KDD 2018).
- **Most ideas don't work.** At Microsoft "only about 1/3 of ideas improve the
  metrics they were designed to improve"; at Amazon, where every feature was
  tested, under half did ([Kohavi et al., 2009](https://ai.stanford.edu/~ronnyk/ExPThinkWeek2009Public.pdf)).
  A loop whose ideas pass far more often is probably measuring something else.

**Several candidates make it worse** *(computed, exact binomial)*. A change
with no effect reaches a 0.6 win rate on 30 fair pairs 18% of the time. Among
four such candidates, at least one gets there about 55% of the time. Then the
"winner" falls back toward 0.5. It was never better.

**A non-regression test has the opposite problem.** On few pairs, "no
evidence it got worse" is the usual answer *(computed, one-sided sign test at
5%)*:

| Pairs | Chance of catching a change that loses 60% of pairs | … loses 70% |
|---|---|---|
| 8 | 11% | 26% |
| 16 | 17% | 45% |
| 30 | 29% | 73% |
| 60 | 45% | 94% |

A pass on 8 or 16 pairs says little about harm, and nothing about help.

### LLM-judge variance and bias

- **Position, length, self-preference.** Zheng et al. name position,
  verbosity and self-enhancement bias, while finding strong judges agree with
  people over 80% of the time ([arXiv:2306.05685](https://arxiv.org/abs/2306.05685)).
  By swapping answer order alone, Vicuna-13B "could beat ChatGPT on 66 over 80
  tested queries" with ChatGPT judging ([Wang et al., 2023](https://arxiv.org/abs/2305.17926)).
  Controlling for length raised AlpacaEval's correlation with Chatbot Arena
  from 0.94 to 0.98 ([Dubois et al., 2024](https://arxiv.org/abs/2404.04475)).
  Models recognise their own text, and self-recognition correlated linearly
  with self-preference ([Panickssery et al., 2024](https://arxiv.org/abs/2404.13076)).
- **One sample is not a verdict.** Fixed seeds "can improve consistency" but
  "do not guarantee reliability" ([Schroeder and Wood-Doughty, 2024](https://arxiv.org/abs/2412.12509)).
- **Judges are weak where taste matters.** On research ideas, the best LLM
  ranker agreed with experts 53.3% of the time (others 43.3% to 51.7%), against
  56.1% between two human reviewers ([Si et al., 2024](https://arxiv.org/abs/2409.04109)).
  I found no study of how well an LLM judge's preference between two posts
  predicts real engagement on X.

---

## 2. Credit in a slow, noisy, non-stationary world

A loop scored on audience metrics has one history, delayed effects, and
outcomes driven partly by luck.

- **Success is partly luck.** In MusicLab, 14,341 people downloaded unknown
  songs with or without seeing others' downloads. Social influence "increased
  both inequality and unpredictability of success." The best songs rarely did
  badly, "but any other result was possible"
  ([Salganik, Dodds and Watts, 2006](https://www.cs.toronto.edu/~bor/303s19/docs/salganik_dodds_watts.pdf)).
- **Cascades have a prediction ceiling.** On Twitter the best models explained
  "less than half of the variance in cascade sizes", bounded "well below
  deterministic accuracy" even with unlimited data
  ([Martin et al., 2016](https://arxiv.org/abs/1602.01013)). One day's
  engagement after a change is mostly noise about that change.
- **Short-term effects mislead.** At Google, the short-term effect "is not
  always predictive of the long-term effect"; user learning "may take months",
  with about half done in 14 to 21 days for changes on every results page.
  They fit a model that predicts the long-term effect from short-term metrics
  ([Hohnhold, O'Brien and Tang, 2015](https://www.gwern.net/doc/economics/advertising/2015-hohnhold.pdf)).
- **Peeking breaks p-values.** They are "wholly unreliable" if you watch a test
  and stop when it looks good; always-valid p-values fix that
  ([Johari et al.](https://arxiv.org/abs/1512.04922)).
- **Off-policy credit needs known randomisation.** Unbiased replay assumes the
  logging policy "chose each arm at each time step uniformly at random"
  ([Li et al., 2011](https://arxiv.org/abs/1003.5956), §3). Logs from a policy
  that always did what it thought best can't credit an alternative.
- **Pairing is far cheaper.** Interleaving shows two rankers to the same users
  on the same queries and needed "over an order of magnitude" fewer queries
  than the best absolute metric, because it is "a paired test, paired on both
  queries and users" ([Chapelle et al., 2012](https://www.cs.cornell.edu/people/tj/publications/chapelle_etal_12a.pdf)).
- **Reusing a holdout overfits it** ([Dwork et al., 2015](https://arxiv.org/abs/1506.02629)).
  A fixed eval set checked daily for months stops being held out.

**What this means.** Next-day audience numbers can't credit one change. Real
outcomes can check a *policy* over weeks, against a control held back by
design. Short-term numbers help only through a proxy checked against the
long-term outcome.

---

## 3. The search space

### Greedy search stalls; archives and stepping stones carry it

| System | Full method | Without the archive or Pareto set | Source |
|---|---|---|---|
| DGM, SWE-bench | 50.0% | 23.0% (only the latest agent kept); 39.7% ("DGM Greedy") | [arXiv:2505.22954](https://arxiv.org/abs/2505.22954), Table 1 |
| DGM, Polyglot | 38.0% | 14.0%; 30.0% (Greedy) | same |
| GEPA, Qwen3 8B, 4 tasks | 61.28 | 54.89 (always pick the best); 53.95 (beam of 4) | [arXiv:2507.19457](https://arxiv.org/abs/2507.19457), Table 3 |

- Picking GEPA's best candidate every round "led to a local-optima after one
  iteration" and "quickly stalls, wasting rollouts on a single candidate."
- In DGM "many paths to innovation traverse lower-performing nodes." The best
  agent's lineage had two dips in score.
- HGM measured why. An agent's own score correlated only 0.27 to 0.44 with how
  good its descendants turned out; HGM's lineage estimate 0.51 to 0.87.
  Choosing parents by lineage reached higher scores in 2.4 to 6.9 times fewer
  CPU hours than DGM ([Wang et al., 2025](https://arxiv.org/abs/2510.21614), Tables 1–2).
- In AlphaEvolve, removing evolution, the prompt's context, meta-prompt
  evolution, full-file evolution or the model mix each hurt; sizes are only in
  a figure ([arXiv:2506.13131](https://arxiv.org/abs/2506.13131), §4).

### Path dependence and proposal collapse

- **Early ideas anchor later ones.** In SICA, early ideas "would often heavily
  influence later feature ideas as variations on the same theme"
  ([Robeyns et al., 2025](https://arxiv.org/abs/2504.15228)).
- **The model's ideas run out.** Of 4,000 seed ideas per topic, about 200 were
  not near-duplicates; more generation "simply leads to repeating duplicate
  ideas" ([Si et al., 2024](https://arxiv.org/abs/2409.04109)).
- **Aligned models collapse to typical answers,** traced to "typicality bias in
  preference data". Asking for several answers with probabilities raised
  creative-writing diversity 1.6 to 2.1 times ([Zhang et al., 2025](https://arxiv.org/abs/2510.01171)).
- **Self-training loses diversity.** Pass@k at large k fell each round of
  iterated self-improvement ([Song et al., 2024](https://arxiv.org/abs/2412.02674)).
- **Complexity is not capability.** Re-run fairly, automatically designed
  multi-agent systems "consistently underperform CoT-SC despite being up to
  10x more expensive". On GPQA-Diamond with GPT-5, ADAS scored 85.23% for
  $832.10 against 87.35% for $46.39. Seven of AFlow's 14 final workflows just
  ran one prompt three times and aggregated
  ([Jwalapuram et al., 2026](https://arxiv.org/abs/2606.13003)).

**What this means.** "One learning, one smallest change" is a good unit for
review. As a *search*, it is a greedy local step drawn from the model's most
typical idea. Without an archive, a novelty check and an occasional bigger
move, it can't leave where it starts.

---

## 4. Reflection: where the gain comes from

The best-supported claim here: reading *specific* feedback about *real*
failures helps. Reflecting without new information doesn't.

| Study | With the component | Without it | Source |
|---|---|---|---|
| Reflexion, HumanEval Rust (50 hardest, GPT-4) | 0.68 | 0.60 without self-reflection (= base); 0.52 without test generation | [arXiv:2303.11366](https://arxiv.org/abs/2303.11366) |
| Reflexion, HotPotQA | +8 points for reflection over episodic memory alone | | same |
| Self-Refine, sentiment reversal | 43.2 specific feedback | 31.2 generic; 0 none | [arXiv:2303.17651](https://arxiv.org/abs/2303.17651), Table 2 |
| Self-Refine, code optimisation | 27.5 | 26.0 generic; 24.8 none | same |
| ACE, AppWorld average | 59.4 | 56.8 no multi-epoch; 55.1 no reflector or multi-epoch; 42.4 base | [arXiv:2510.04618](https://arxiv.org/abs/2510.04618), Table 3 |
| ExpeL, HotpotQA success | 39.0 | 36 insights only; 31 retrieval only; 32.0 hand-written insights | [arXiv:2308.10144](https://arxiv.org/abs/2308.10144), Table 3, §5.2 |

- **The outside check carries the gain.** In Reflexion's Rust ablation,
  reflection without tests did worse than base, and tests without reflection
  no better; only both gained. On MBPP Python, where 16.3% of its generated
  tests were false positives, Reflexion scored 77.1% against GPT-4's 80.1%.
- **Intrinsic self-correction doesn't work.** Without outside feedback LLMs
  "struggle to self-correct" and sometimes get worse
  ([Huang et al., 2023](https://arxiv.org/abs/2310.01798)). Self-Refine's maths
  gains were near zero (GPT-4: 92.9 to 93.1); ChatGPT's feedback said
  "everything looks good" 94% of the time.
- **Success-failure contrast helps.** ExpeL's insights compare successes with
  failures. Adding Reflexion's reflections to them "hurt performance" (29.0),
  and insights from a weaker model were worse (32.0).
- **Reflection without labels can pollute.** ACE without labels still gained on
  AppWorld (57.2), but Dynamic Cheatsheet without labels fell below base on
  finance (65.4 against 69.1). A context "can be polluted by spurious or
  misleading signals."
- **Rewriting loses detail.** One Dynamic Cheatsheet rewrite shrank its
  context from 18,282 tokens to 122, and accuracy fell from 66.7 to 57.1,
  below no adaptation (63.7) (ACE §2.2).
- **DGM starts from its own failures.** "Each selected parent analyzes its own
  benchmark evaluation logs, proposes the next feature to implement," then
  implements it.
- **GEPA.** Its reflective step reads traces and feedback text, but I found no
  ablation removing the text alone, so its gain can't be split between
  reflection and Pareto selection from the paper.

---

## 5. The ceiling set by the frozen model

- **Iterated self-improvement saturates fast:** "typically saturates after two
  or three rounds, regardless of the model's capacity"
  ([Song et al., 2024](https://arxiv.org/abs/2412.02674)).
- **RL sharpens, it doesn't widen.** With many samples, base models solve more
  problems than their RL-tuned versions; the reasoning is "bounded by the base
  model" ([Yue et al., 2025](https://arxiv.org/abs/2504.13837)). Prompt search
  is weaker than RL, so the bound applies to it at least as strongly (my
  inference, not a measured result).
- **Scaffolds barely move reasoning.** SICA's reasoning tasks showed "less
  improvement", and "there seem to be limits to what can be achieved with such
  purely scaffolding based systems" ([Robeyns et al., 2025](https://arxiv.org/abs/2504.15228)).
- **Gains rise with the model.** AlphaEvolve "performs increasingly better as
  the underlying LLM improves." ExpeL's insights were better from GPT-4.

**Which scaffold changes moved capability.** DGM found patch validation,
better file viewing, finer editing tools, generating and ranking several
solutions, and a memory of past attempts and why they failed
([Sakana](https://sakana.ai/dgm/)). Reflexion's gain needed tests.
Self-consistency beat most automated designs. The pattern: a *verifier*, a
*tool that acts on the world*, *more attempts with selection*, or *memory of
specific failures* moves results. Wording, roles and topology mostly don't,
once evaluation is fair.

---

## 6. Goodharting: progress on the judge, not the goal

How judges get gamed is in [`rsi-theory-and-safety.md`](rsi-theory-and-safety.md).
Here is how it shows up as progress that isn't real.

- **Proxy up, truth down.** Optimising a learned reward model "too much can
  hinder ground truth performance"
  ([Gao, Schulman and Hilton, 2022](https://arxiv.org/abs/2210.10760)). A loop
  that keeps promoting against one fixed judge walks along that curve.
- **A constant answer beats the judges.** A "null model" giving the same output
  every time got an 86.5% length-controlled win rate on AlpacaEval 2.0, 83.0 on
  Arena-Hard-Auto and 9.55 on MT-Bench ([Zheng et al., 2024](https://arxiv.org/abs/2410.07137)).
- **Single tokens fool judges.** Openers such as "Thought process:" "can
  consistently elicit false positive rewards", frontier judges included
  ([Zhao et al., 2025](https://arxiv.org/abs/2507.08794)).
- **No holdout invites shortcuts.** Many agent benchmarks have "inadequate
  holdout sets, and sometimes none at all" ([Kapoor et al., 2024](https://arxiv.org/abs/2407.01502)).

**The tell.** Judge win rate rises while outside measures stay flat, and
winners share a surface trait: length, a stock phrase, a format. Selecting on
noise looks the same from inside, so check for both.

---

## What this argues for in Teeny

Ranked by how much each is likely to decide whether merged changes add up.
Each is a question for Teeny's own records first, then the change it argues
for.

1. **Measure the noise floor.** Run main against main through the exact eval
   path, repeatedly. How often does it say pass, regressed or inconclusive, and
   how wide is the judge's win rate across repeats? Then report every verdict
   as an effect with an interval and its power, and set pair counts from a
   power calculation for the effect one playbook edit can plausibly have. If
   that can't be afforded, call the change unmeasured, not passed.
2. **Separate "harmless" from "better".** Of merged changes, how many showed a
   measured gain, and how many only failed to show harm? Keep non-regression
   as the shipping gate, but count an improvement only from a positive paired
   result or a checked outcome. A loop that counts harmless changes looks busy
   and learns nothing.
3. **Put real failures in front of the proposer.** Does it see the failing
   drafts, the judge's notes on lost pairs and the posts that flopped, or only
   a one-line learning? Give it concrete success and failure pairs, ask it to
   diagnose before it edits (DGM, GEPA, ExpeL), and keep held-out notes out of
   reach.
4. **Correct for the winner's curse.** A week after merge, re-score each change
   on fresh samples. Does the effect hold or shrink toward zero? Shrink
   reported effects by the gap seen, and raise the bar when candidates compete.
5. **Pair everything.** Same tasks, same seeds where possible, both answer
   orders, ties counted. For live posting, alternate old and new policies
   across comparable slots rather than comparing this week with last.
6. **Keep an archive and use it.** Do proposals ever start from a parked or
   lower-scoring variant? How many repeat earlier ones? Let some proposals
   branch from non-main variants, credit lineages (HGM), drop near-duplicates
   before evaluating, and show the proposer what was tried and how it did.
7. **Aim at changes that can move the ceiling.** What share of merges are
   wording edits, and what share add a check, a tool, more attempts with
   selection, or a memory of specific failures? Weight the proposer toward the
   second kind.
8. **Credit outcomes to policies over weeks, not to changes over days.** How
   much of the world score's daily variance is the calendar, news, one viral
   post or the token price? Attribute outcomes to batches of changes against a
   control held back by design, use always-valid statistics if watched hourly,
   and trust a short-term proxy only once it has been checked against the
   long-term outcome.
9. **Audit the judge as a moving part.** Does its win rate rise while live
   outcomes stay flat? Do winners share length or a phrase? Is it the
   proposer's model family? Control for length, swap positions, use another
   family or several judges, and rotate held-out tasks.
10. **Know when a behaviour has hit the ceiling.** If gains on one behaviour
    stop after a few rounds, stop editing it and spend the budget elsewhere.
    Treat a base-model upgrade as the thing most likely to move it.

## Claims I could not verify

- Lee and Shen's (KDD 2018) own winner's-curse numbers from Airbnb. The paper
  and Airbnb's post returned 403; only Deng et al.'s account is used.
- Industry claims that interleaving needs 10 to 100 times less traffic. The
  Netflix post returned 403; only Chapelle et al.'s figure is used.
- How well LLM judges predict real engagement on X. I found no direct study.
- The size of each AlphaEvolve ablation, shown only in a figure.
- How GEPA's gain splits between reflective mutation and Pareto selection.
