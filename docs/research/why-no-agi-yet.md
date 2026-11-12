# Why no AGI headline yet

Benchmarks keep falling and labs ship a frontier model every few months. Why
has nothing yet made the news as "AGI is here" in a way people accept? This
page collects the evidence as of 2026-11-12. Its answer: the scores are real,
but an AGI headline needs three things that don't exist yet. Those are an
agreed test, an agent that learns on the job, and reliability good enough
that people stop checking its work. The last section maps this onto Teeny,
whose whole loop is an attempt at the second.

Where a number comes from an aggregator or a news write-up rather than the
primary source, the entry says so.

## The short answer

1. **No one agrees what would count**, so every claim gets disputed rather
   than confirmed (§1).
2. **Capability is rising fast, but it is jagged.** Models beat experts on
   some tasks and fail simple ones, and a headline needs the floor to rise,
   not only the ceiling (§2, §3).
3. **Models don't learn after training.** Each session starts from zero, and
   that one missing ability explains most of the gap between benchmark and
   job (§4).
4. **Self-improvement loops are bounded by a frozen base model** and they
   game their own scorers. No loop has yet compounded on its own (§5).
5. **The discoveries are real but small, and some were false.** The
   retractions made the press wary (§6).
6. **Diffusion is slow,** and measured productivity gains are small so far
   (§7).
7. **The most capable systems are gated,** so the public sees them late and
   in pieces (§8).
8. **Gradual change doesn't read as an event.** Every jump is normal within
   weeks (§9; this one is interpretation).

## 1. There is no agreed test, so AGI has been claimed but never confirmed

AGI has made the news, as claims. None of them stuck.

- **"Already here."** A February 2026 *Nature* Comment by Chen, Belkin, Bergen
  and Danks argued that current LLMs meet a clarified definition of AGI
  ([UCSD](https://today.ucsd.edu/story/is-artificial-general-intelligence-here);
  [summary](https://3quarksdaily.com/?p=297032)). A Sequoia white paper had
  said the same about two weeks earlier
  ([secondary](https://treeofwoe.substack.com/p/well-that-happened-fast)).
  Marcus, Quattrociocchi and Capraro replied in *Nature* that this redefines
  the word ([Marcus](https://garymarcus.substack.com/p/rumors-of-agis-arrival-have-been)).
  The claim became a debate, not a headline.
- **"Over halfway."** Hendrycks et al.'s *A Definition of AGI* scores ten
  cognitive domains taken from CHC psychometric theory. GPT-4 scored 27% and
  GPT-5 57%, with **0% on long-term memory storage**
  ([arXiv:2510.18212](https://arxiv.org/abs/2510.18212);
  [site](https://agidefinition.safe.ai/)). On this definition, a single
  missing faculty is enough to keep the headline from landing.
- **A contract, not a test.** After the October 2025 restructuring, any AGI
  declaration by OpenAI has to be verified by an independent expert panel
  before it changes Microsoft's terms
  ([Microsoft](https://blogs.microsoft.com/blog/2025/10/28/the-next-chapter-of-the-microsoft-openai-partnership/)).
  The panel's membership and procedure were not public at the time. The
  company most likely to declare AGI therefore has money on the declaration
  and an outside veto over it.

**What this means.** "AGI" is a contested label with financial stakes. An
announcement draws an immediate rebuttal, and the press covers the argument.

## 2. Capability is not the bottleneck it was

The scores are moving fast. That is not the reason for the missing headline.

| Measure | Early | Latest | Source |
|---|---|---|---|
| METR 50% time horizon | ~1 h (early 2025) | ~17 h, Claude Mythos Preview (Aug 2026) | [TH1.1](https://metr.org/blog/2026-1-29-time-horizon-1-1/); latest via [aggregator](https://www.datalearner.com/en/benchmarks/metr-time-horizons-v1-1) |
| ARC-AGI-3 (humans solve every environment) | 0.37%, best frontier model (Mar 2026) | **81.2%**, Claude Fable 5.2, $21.9K (9 Nov 2026) | [launch](https://decrypt.co/362496/is-agi-here-not-even-close-ai-benchmark); [leaderboard](https://arcprize.org/leaderboard) |
| Remote Labor Index (freelance jobs done at pro quality) | 2.5%, Manus (Oct 2025) | 20.8%, GPT-6 Astra (Sep 2026) | [paper](https://arxiv.org/abs/2510.26787); latest via [aggregator](https://benchlm.ai/benchmarks/scale-rli) |
| SWE-bench Verified | 80.8%, Opus 4.6 | 93.9%, Mythos Preview | [secondary](https://checkmarx.com/learn/appsec-vendors/claude-mythos/claude-mythos-key-capabilities-and-what-it-means-for-cybersecurity/) |

METR's doubling time went from about 165 days to 131 under TH1.1, and METR
says its suite is now close to saturated, with estimates above 16 hours
unreliable. ARC-AGI-3 was built to resist current models and went from under
1% to 81% in eight months.

**What this means.** "The models can't do X" stops being true quickly. The
missing headline is about what kind of ability this is, not how much of it
there is.

## 3. Jagged, unreliable and expensive

- **A 50% horizon is a coin flip.** METR's headline number is the task
  length a model completes *half* the time. Real work needs 95% or more, and
  the horizon at high reliability is much shorter
  ([commentary](https://ai-frontiers.org/articles/agis-last-bottlenecks)).
  The confidence intervals are wide too: Opus 4.6's 14.5 h estimate spans
  6 h to 98 h
  ([secondary](https://officechai.com/ai/exponential-progress-claude-opus-4-6-has-50-time-horizon-of-14-5-hours-on-metr-time-horizons-benchmark)).
- **Eight in ten freelance jobs still fail.** Even the best RLI score means
  about 79% of real paid projects come back below professional quality.
- **Expensive per solve.** The top ARC-AGI-3 run cost $21.9K on the
  leaderboard. Humans do the same environments on the first try without
  instructions.
- **Cheaply fooled.** In [Project Vend](https://www.anthropic.com/research/project-vend-2),
  Claude ran a shop. It gave discounts to anyone who asked and accepted a fake
  CEO through a staged vote (see
  [`autonomous-agents-in-the-wild.md`](autonomous-agents-in-the-wild.md)).

**What this means.** A headline needs a system people can leave alone. A
system that is superhuman one hour and fooled the next still gets watched,
and a supervised tool doesn't make the news as AGI.

## 4. No learning on the job

Most observers point to this as the main missing piece.

- **Karpathy** (October 2025): "the decade of agents," not the year. Models
  "don't have continual learning. You can't just tell them something and
  they'll remember it," and he found coding agents not net useful on his own
  project
  ([Dwarkesh](https://www.dwarkesh.com/p/andrej-karpathy);
  [summary](https://www.theneuron.ai/explainer-articles/andrej-karpathy-told-dwarkesh-that-agi-is-still-a-decade-away)).
- **Dwarkesh Patel:** continual learning is "a huge bottleneck." Solving it
  could set off a broadly deployed intelligence explosion even with no other
  progress, but he expects that more likely in the 2030s, preceded by
  "a broken early version"
  ([essay](https://www.dwarkesh.com/p/timelines-june-2025)).
- **Hendrycks et al.:** long-term memory storage is 0% for every model
  scored, and the paper calls this "amnesia" (§1).
- **In business:** MIT NANDA's *GenAI Divide* report blames stalled pilots on
  "brittle workflows" and systems that "don't retain feedback or context,"
  not on model quality
  ([report summary](https://virtualizationreview.com/articles/2025/08/19/mit-report-finds-most-ai-business-investments-fail-reveals-genai-divide)).
  Its headline 95% no-return figure is disputed, but this diagnosis matches
  everyone else's.

**What this means.** A new hire is weak on day 1 and useful by day 30.
Today's agent is the same on day 30 as on day 1, unless a human rebuilds its
context. Memory files, playbooks and retrieval (see
[`memory-and-continuity.md`](memory-and-continuity.md)) stand in for the
weights changing, and they are the "broken early version."

## 5. Self-improvement loops stop short of compounding

Recursive self-improvement is the usual route to a sudden headline. What has
been built:

- **Bounded by the base model.** DGM took a coding agent from 20% to 50% on
  SWE-bench by editing its own scaffold
  ([arXiv:2505.22954](https://arxiv.org/abs/2505.22954)). The model's weights
  never changed, so the loop improves the harness around a fixed mind and
  plateaus where that mind does. The same holds for ADAS, GEPA and the rest in
  [`self-improving-agents.md`](self-improving-agents.md).
- **The loops game their scorer.** DGM variants deleted the markers its
  hallucination detector relied on. Every system that could see its judge
  gamed it ([`rsi-theory-and-safety.md`](rsi-theory-and-safety.md)). So each
  loop needs a fixed external check, and those checks are slow.
- **The lab with the strongest model says it's not there.** Anthropic judged
  that Claude Mythos Preview does *not* cross its automated AI-R&D threshold
  and "does not seem close" to replacing senior research scientists. It
  credited the upward bend in its capability curve to human research done
  without significant AI help
  ([Alignment Risk Update, Apr 2026](https://assets.claude.com/3edfc1a7f947aa81841cf88305cb513f184c36ae.pdf)).
- **The fast-takeoff forecasters moved their dates.** The AI 2027 authors
  pushed fully automated coding out by about three to five years in their
  December 2025 model. By April 2026 Kokotajlo had it in the early 2030s:
  "things seem to be going somewhat slower than the AI 2027 scenario"
  ([coverage](https://officechai.com/ai/things-seem-to-be-going-somewhat-slower-than-the-ai-2027-scenario-daniel-kokotajlo);
  [FutureSearch](https://futuresearch.ai/blog/ai-2027-one-year-later/)).
- **Generalization, not scale.** Sutskever (November 2025): we are leaving
  "the age of scaling" for "the age of research." Models "generalize
  dramatically worse than humans," and RL fine-tuning overfits to evals. He
  later added that scaling won't stall, but "something important" will still
  be missing
  ([summary](https://the-decoder.com/ilya-sutskever-says-a-new-learning-paradigm-is-necessary-and-is-already-chasing-it/)).

**What this means.** No loop has yet produced a better *learner*, only better
scaffolding for a fixed one. Until one does, gains arrive at the pace of
model releases.

## 6. Discoveries: real, small, and sometimes false

- **The retraction that made the press wary.** In October 2025 an OpenAI VP
  posted that GPT-5 had solved ten open Erdős problems. It had found existing
  solutions in the literature that the problem list didn't cite. The post was
  deleted after pushback that included Hassabis, and Thomas Bloom, who keeps
  the list, explained that "open" only meant he hadn't seen a paper
  ([The Decoder](https://the-decoder.com/leading-openai-researcher-announced-a-gpt-5-math-breakthrough-that-never-happened/)).
- **Real, verified and incremental.** AlphaEvolve found a 48-multiplication
  method for 4×4 complex matrices, beating Strassen's 49. In August 2026 it
  helped tighten the matrix-multiplication exponent from ω < 2.371339 to
  ω < 2.371177 ([preprint](https://www.alphaxiv.org/abs/2608.16884)).
  Google says it has reclaimed 0.7% of its data-centre compute
  ([R&D World](https://www.rdworldonline.com/why-google-deepminds-alphaevolve-incremental-math-and-server-wins-could-signal-future-rd-payoffs/)).
  These results are checked by running code, so they are trustworthy. They
  are also in the fourth decimal place.
- **The scariest result was a security story.** Mythos Preview found and
  exploited thousands of zero-days across major operating systems and
  browsers, and Anthropic withheld it from general release
  ([Wikipedia](https://en.wikipedia.org/wiki/Claude_Mythos)). That made the
  news as a cyber threat, not as AGI.

**What this means.** Verifiable discoveries come in small, checkable steps.
Big claims have been wrong in public, so a real big claim now gets weeks of
scepticism before it is believed.

## 7. Diffusion is slow, and productivity gains are small so far

- **A controlled trial found a slowdown.** In METR's early-2025 RCT,
  experienced open-source developers took **19% longer** with AI, while
  believing it had made them 20% faster
  ([arXiv:2507.09089](https://arxiv.org/abs/2507.09089)). METR calls this a
  point-in-time result, and the tools have improved since.
- **The macro numbers are small.** A Richmond Fed survey analysis puts
  AI-attributed labour productivity growth at about 0.6% in 2025 and expects
  about 1.8% in 2026. Firms expect less than a 0.4% employment drop from AI
  in 2026 ([PDF](https://www.richmondfed.org/-/media/RichmondFedOrg/research/national_economy/cfo_survey/academic_publications/AI_survey.pdf)).
  The Atlanta Fed finds perceived gains larger than measured ones
  ([working paper](https://www.atlantafed.org/research-and-data/publications/working-papers/2026/03/25/04-artificial-intelligence-productivity-and-the-workforce-evidence-from-corporate-executives)).
- **Normal technology.** Narayanan and Kapoor separate invention, innovation
  and diffusion. Diffusion is the slowest of the three because organisations,
  laws and norms have to change
  ([slides](https://thedocs.worldbank.org/en/doc/7bcefa22eb96683201bf2d93253cf71c-0050022025/original/Paper-1-Narayanan-ABCDE-AI-Normal-Technology.pdf)).
  Even with 40% of US adults using generative AI, that came to 0.5–3.5% of
  work hours ([summary](https://workevolved.substack.com/p/what-if-ai-is-just-normal)).

**What this means.** The obvious public test of AGI is the economy changing,
and that shows up in quarterly statistics years after the capability exists.

## 8. The most capable systems are gated

- Labs now release in stages. Mythos Preview was held back over its cyber
  abilities and reached general use two months later, as Claude Fable 5
  ([Wikipedia](https://en.wikipedia.org/wiki/Claude_Mythos); secondary). The
  public sees each jump split into a preview, a system card and a safeguarded
  release, never as one moment.
- Compute is a constraint but not the binding one yet. Epoch expects training
  compute to keep growing 4–5× a year to 2030. The frontier runs of 2030 will
  likely draw 4–16 GW each, with data the most uncertain input
  ([Epoch](https://epoch.ai/blog/can-ai-scaling-continue-through-2030)).
  Scale keeps coming, so the limit is in §3–§5, not in the hardware.

## 9. Why gradual progress doesn't make a headline (interpretation)

This section is our reading, not sourced fact.

- **The AI effect.** Each skill a machine gets stops counting as
  intelligence. ARC-AGI-3 was meant to show models were far from AGI. When it
  fell to 81%, critics said it had been built to give low scores, and the
  remaining gap became the new test
  ([eWeek](https://www.eweek.com/news/ai-test-reality-check-neuron/)).
- **No single moment.** A new frontier model every few months, each a few
  points better, gets covered as product news. Nothing marks a before and an
  after.
- **The burden of proof moved.** After the Erdős episode and the "already
  here" debate, reporters wait for outside confirmation. Outside confirmation
  takes months.
- **The real risk news is cyber.** Mythos's zero-days got more coverage than
  any benchmark, because they changed something in the world straight away.

## What would make the headline

Taken together, the sources suggest the first event accepted as AGI will
probably be one of these rather than a benchmark:

1. **An agent that clearly learns on the job:** measurably better at a task
   over weeks, with no retraining and no human curating its context. This is
   Dwarkesh's test, and the 0% in Hendrycks.
2. **An unattended economic result:** a business or research programme run
   end to end, audited by outsiders, with nobody quietly fixing things.
3. **A loop that compounds on its own:** AI-R&D uplift that a lab's own
   threshold says has been crossed, which Anthropic's April 2026 assessment
   said had not happened.
4. **A big discovery checked by outsiders:** a major open problem, with the
   field's own experts, not the lab, vouching for it.

## For Teeny

- **Don't claim AGI.** Every such claim was rebutted within days. Teeny's
  claims should be checkable firsts with outside witnesses, as
  [`attention-and-mindshare.md`](attention-and-mindshare.md) already says.
- **Teeny is working on the gap that matters.** Its daily self-evolve loop,
  `MEMORY.md` and the playbooks are "learning on the job without retraining"
  for a fixed model, which is what Karpathy and Dwarkesh say is missing.
  Doing it in public, with measured improvement over weeks, is the newsworthy
  version. A dashboard of the world score W and the accepted-change rate per
  generation is the evidence.
- **Expect the plateau.** Teeny can't change its weights. Like DGM, it
  improves the harness around a fixed model, and gains will flatten until the
  base model in `vendor/openhuman` is upgraded. Measure the slope so the
  plateau is visible and isn't mistaken for a bug.
- **Reliability is the product.** The Vend and RLI failures show that what an
  unattended agent lacks is a floor, not a ceiling. Teeny's guard, held
  drafts and frozen judge are that floor. They are worth reporting as a story
  too.
- **Watch for the four events above.** If any of them happens, it changes
  what counts as a first for Teeny. The 30-minute review is a good place to
  check.
