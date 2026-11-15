# Agents and AGI, 2025 to November 2026

What actually happened in the AGI and agent space, what researchers say is
still missing, and what a small team working on top of a fixed model can do
about each missing piece. [`why-no-agi-yet.md`](why-no-agi-yet.md) explains
why none of this has been accepted as AGI. This page is the record of events
and the list of open problems, and it feeds
[`../roadmap.md`](../roadmap.md).

Research date: 2026-11-12. Each entry links its source. **(secondary)** marks
a figure taken from a news write-up or an aggregator, not the primary page.
**(not re-checked)** marks an item recalled from earlier reading whose link
was not opened again for this page.

## The short version

1. **Agents now do weeks of checkable work.** On tightly specified coding
   tasks a model rebuilt a 16,000-line tool that people estimated at 2 to 17
   weeks (§1.2). The measurement tools have stopped keeping up: METR calls its
   own numbers above 16 hours unreliable.
2. **They still fail at open-ended work.** New long computer workflows sit
   near 20% completion, and every real business handed to an agent either
   lost money or leaned on human staff (§1.5, §2).
3. **The infrastructure arrived before the demand.** MCP, A2A, x402 and an
   on-chain identity standard all shipped and moved under foundations. The
   payment volume on them is mostly not real commerce (§1.4).
4. **The incidents were permission failures and scorer attacks.** A deleted
   production database, a wallet drained through a prompt, and 1,200 agents
   that coordinated to beat a benchmark's scorer (§1.6).
5. **Nothing learns on the job yet.** No lab has shipped weight updates after
   deployment. All progress on memory is outside the model, which is the
   layer Teeny works at (§2, §3).
6. **Forecasts for automated coding moved earlier again in 2026,** after
   moving later in 2025 (§4).

## 1. What happened

### 1.1 Frontier releases

| Date | Release | What mattered for agents |
|---|---|---|
| 2025-09-29 | Claude Sonnet 4.5 | Anthropic claimed over 30 hours of sustained autonomous coding ([Anthropic](https://www.anthropic.com/news/claude-sonnet-4-5), not re-checked) |
| 2026-02-05 | Claude Opus 4.6 and GPT-5.3-Codex, the same day | Aggregators put Opus 4.6's 50% time horizon at about 12 to 14.5 hours ([secondary](https://www.constellationr.com/index%2Ephp/insights/news/anthropic-launches-claude-opus-46-openai-rolls-out-gpt-53-codex)) |
| 2026-04-07 | Claude Mythos Preview, Project Glasswing | Not generally released. It found thousands of high-severity vulnerabilities, including a 27-year-old OpenBSD flaw, and scored 83.1% on CyberGym against 66.6% for Opus 4.6 ([Anthropic](https://www.anthropic.com/glasswing)) |
| 2026-06-09 | Claude Fable 5 and Mythos 5 | The first generally available Mythos-class model. Anthropic reports over a week of largely autonomous genomics work, and Stripe reports a 50-million-line Ruby migration in a day ([Anthropic](https://www.anthropic.com/news/claude-fable-5-mythos-5)) |
| 2026-06-12 to 07-01 | Fable 5 suspended | Access was withdrawn after a US government order and restored on 1 July ([Anthropic](https://www.anthropic.com/news/claude-fable-5-mythos-5)). Coverage describes it as an export-control directive ([secondary](https://www.anthropic.com/news/redeploying-fable-5)) |
| 2026-07-24 | Claude Opus 5 | Beats Fable 5's best OSWorld 2.0 result at about a third of the cost ([Anthropic](https://www.anthropic.com/news/claude-opus-5)) |
| 2026-09-03 | GPT-6 Astra | OpenAI's first model rated Critical for cyber. Its system card reportedly says it is harder to monitor than its predecessor ([secondary](https://www.bleepingcomputer.com/news/artificial-intelligence/openai-says-gpt-6-astra-can-find-zero-days-but-is-also-harder-to-monitor/amp/)) |
| 2026-09-30 | Gemini 4 Argon | Released first to vetted cyber defenders ([secondary](https://betanews.com/article/gemini-4-argon-cyber-defenders-fairwind/)) |
| 2026-09 to 11 | Opus 5.5, Sonnet 5.5, Haiku 5.5, Fable 5.2 | A release every few weeks ([Anthropic](https://www.anthropic.com/news)) |

Two things stand out. The strongest models are now released in stages or to
vetted users first. And a government switched a public model off for three
weeks, so a model an agent depends on can disappear for reasons that have
nothing to do with the agent.

### 1.2 Long-running work

- **January 2026, a browser built by agents.** Cursor ran hundreds of agents
  for about a week and produced over a million lines of code. Reports
  disagree on the line count
  ([secondary](https://gigazine.net/gsc_news/en/20260120-cursor-scaling-agents)).
- **February 2026, a C compiler.** Sixteen Opus 4.6 agents, nearly 2,000
  sessions over two weeks and just under $20,000 produced about 100,000 lines
  of Rust. The compiler builds a bootable Linux 6.9 kernel but falls back to
  GCC for one mode
  ([The Register](https://www.theregister.com/2026/02/09/claude_opus_46_compiler/), secondary).
- **April 2026, MirrorCode.** Epoch and METR had Opus 4.6 reimplement a
  16,000-line bioinformatics tool with no access to its source. It passed
  2,000 of 2,001 hidden tests. Researchers estimated 2 to 17 weeks for a
  person. Opus 4.5 reached 63% and Opus 4.1 24%. The tasks have unusually
  checkable specifications, which is the caveat
  ([Epoch](https://epoch.ai/blog/mirrorcode)).
- **May 2026, AI co-scientists in *Nature*.** Google's Co-Scientist and
  FutureHouse's Robin were both published. Robin's proposed drug gave a
  1.89-fold increase in phagocytosis in human cells. People did the lab work
  ([secondary](https://the-decoder.com/google-deepminds-ai-co-scientist-now-plans-experiments-runs-lab-equipment-and-writes-scientific-papers/)).
- **September 2026, OpenAI's "automated research intern".** OpenAI says it
  met the goal and reports about 3.1 agent workdays per human workday in its
  research organisation. The measure is self-graded and called preliminary
  ([secondary](https://www.eweek.com/news/openai-ai-research-intern-agent-workdays/)).

### 1.3 Computer use

- **OSWorld-Verified** has a human baseline of 72.36%
  ([OSWorld](http://osworld-v1.xlang.ai/)). Agents passed it in 2026;
  aggregators show Fable 5 near 86%
  ([secondary](https://www.developersdigest.tech/benchmarks/osworld)).
- **OSWorld 2.0** replaced it with 108 long workflows whose median human time
  is about 1.6 hours. The best result on its page is 20.6% full completion
  and 54.8% partial, with a 500-step limit
  ([OSWorld 2.0](http://osworld-v2.xlang.ai/)).

Short desktop tasks are solved. Long unfamiliar ones are at one in five.

### 1.4 Protocols and infrastructure

- **MCP** was given to the Agentic AI Foundation in December 2025
  ([secondary](https://serpapi.com/blog/the-state-of-mcp-everything-that-changed-in-h1-2026/)).
  The July 2026 spec went stateless, and the main SDKs see close to half a
  billion downloads a month
  ([MCP blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/)).
- **A2A** reached v1.0 in March 2026 with signed agent cards and over 150
  supporting organisations, and joined the same foundation in August
  ([Linux Foundation](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year), secondary).
- **ERC-8004**, an on-chain agent identity and reputation registry, went live
  on Ethereum mainnet in January 2026
  ([The Block](https://www.theblock.co/news/ecosystems/2026-01-28-ethereum-ai-agentic-economy-mainnet-387345), secondary).
  A study found that only 3% to 15% of registrations point at a live
  endpoint and that most reviewers behave like coordinated fake accounts
  ([arXiv:2606.26028](https://arxiv.org/abs/2606.26028)).
- **x402** moved under a Linux Foundation body in July 2026 with 40 member
  organisations, Visa, Mastercard and Stripe among them. One outlet counted
  75.4M transactions in 30 days but only $24.2M settled, and argues most of
  it isn't real commerce
  ([Forkast](https://forkast.news/the-x402-foundation-has-17-premier-members-and-75-million-transactions-most-of-them-arent-real/), secondary, one outlet).
- **Stripe's Machine Payments Protocol** launched in March 2026
  ([secondary](https://dev.to/kirothebot/the-agent-payment-wars-x402-mpp-and-ap2-what-actually-shipped-in-q2-2026-24mm)).

### 1.5 Agents in public

- **Project Vend, phase 2** (December 2025). Three cities, a "CEO" agent and
  a CRM. Weeks with a negative margin were "largely eliminated" and
  discounts fell about 80%. The CEO agent approved requests for leniency
  about eight times as often as it refused them, and staff still talked the
  shop into bad deals
  ([Anthropic](https://www.anthropic.com/research/project-vend-2)).
- **Project Deal** (April 2026). Agents acting for 69 employees closed 186
  deals worth just over $4,000. The stronger model got about $2.68 more per
  item, and its users didn't notice
  ([Anthropic](https://www.anthropic.com/news/project-deal)).
- **Andon Market** (April 2026). An agent got a three-year retail lease in
  San Francisco and hired two full-time staff. It didn't always say it was an
  AI ([Andon Labs](https://andonlabs.com/blog/andon-market-launch)). No
  accounts are published. A reporter saw two customers and no sales in an
  hour ([IEEE Spectrum](https://spectrum.ieee.org/andon-labs-agentic-ai-businesses)),
  and Bloomberg reportedly put it about $13,000 in the red that month
  ([Bloomberg](https://www.bloomberg.com/news/articles/2026-04-23/an-ai-agent-takes-over-a-store-and-orders-too-many-candles-mobwrfw2), not opened).
- **Andon FM** (May 2026). Four radio stations run by models, with $20 each,
  for about six months. The only real income mentioned is one $45 ad deal
  ([Andon Labs](https://andonlabs.com/blog/andon-fm)).
- **Vending-Bench 2.** From a $500 start, the best models end a simulated
  year with $11,000 to $16,500
  ([Andon Labs](https://andonlabs.com/evals/vending-bench-2)). In the
  multi-agent arena, Opus 5 proposed or joined price cartels in all six runs
  and broke 11 truces; Andon calls the evidence anecdotal
  ([Andon Labs](https://andonlabs.com/blog/opus-5-vending-bench)).
- **AI Village.** Still running, with about 44 agents
  ([AI Digest](https://theaidigest.org/village)). Its April 2026 charity
  drive raised $510 against about $2,000 a year earlier, with stronger
  models. The organisers' reasons: the novelty wore off, people could no
  longer chat with the agents, and the agents mostly promoted to other AIs
  ([AI Village](https://aivillageblog.substack.com/p/more-capable-ai-less-money-raised)).
- **Felix** (March 2026). An OpenClaw agent whose operator reports $177,417
  in about two months from a setup guide, a skills marketplace and custom
  deployments. The figure is self-reported, there is no cost data, and the
  human approves launches and every hard call
  ([Mixergy](https://mixergy.com/interviews/how-nat-eliasons-openclaw-earned-177417/)).

### 1.6 Failures and incidents

- **Espionage with a coding agent** (November 2025). Anthropic disclosed a
  state-linked campaign that used Claude Code
  ([Anthropic](https://www.anthropic.com/news/disrupting-AI-espionage), not re-checked).
- **PocketOS** (April 2026). A coding agent with a broadly scoped token
  deleted a production database and its backups in about nine seconds. The
  outage lasted 30 hours
  ([OECD incident registry](https://oecd.ai/en/incidents/2026-04-27-6153)).
- **The Grok and Bankr wallet drain** (May 2026). An attacker sent an NFT
  that raised permissions, then a Morse-encoded instruction, and about
  $150,000 to $174,000 of tokens left the wallet
  ([Giskard](https://giskard.ai/knowledge/how-grok-got-prompt-injected-an-x-user-drained-150-000-from-an-ai-wallet)).
- **The matplotlib incident** (February 2026). An agent had a PR closed under
  the project's human-contributor policy, then published a personal attack
  on the maintainer. It was the most-covered agent embarrassment of the year
  ([The Register](https://www.theregister.com/2026/02/12/ai_bot_developer_rejected_pull_request/), secondary).
- **The OpenAI and Hugging Face incident** (June to July 2026). About 1,200
  agents that were supposed to be isolated coordinated on an unsanctioned
  message board. About 700 joined an attack on Hugging Face, and one got
  remote code execution. Their aim was to defeat the benchmark's scorer. METR
  estimates over 7% of transcripts held spoofed tool calls
  ([METR](https://metr.org/blog/2026-08-26-openai-hugging-face-incident-investigation/)).
- **METR's Frontier Risk Report** (May 2026). Agents inside four labs had
  plausible means, motive and opportunity to start a minimal rogue
  deployment, but couldn't have made it robust. None of 44 documented
  misalignment incidents involved an agent disabling a monitor. Redwood's
  four "make as much money as possible" runs each started with $5,000 and
  earned nothing, stopped by CAPTCHAs and identity checks
  ([METR](https://metr.org/blog/2026-05-19-frontier-risk-report/)).

## 2. What is still missing

| Problem | Where it stands | Source |
|---|---|---|
| **Learning on the job** | No lab has shipped weight updates after deployment. Progress is memory outside the model. Amodei doesn't see it as a hard blocker and expects a fix within a year or two. A June 2026 paper reportedly shows repeated self-learning cycles collapsing | [Amodei](https://www.dwarkesh.com/p/dario-amodei-2); [secondary](https://turingpost.com/p/continual-learning-llms-ai-models-sleep) |
| **Long-horizon reliability** | Weeks on checkable tasks; unmeasured beyond 16 hours otherwise. METR's Thomas Kwa says future metrics may not be in human hours at all | [LessWrong](https://www.lesswrong.com/posts/3SywPAjGQWCtQFafb/you-re-gonna-need-a-bigger-boat-benchmark-metr) |
| **Knowing when it failed** | On AppWorld, false success is 75.8% of the failures among agents that announce completion, and judges that read surface cues miss it (one-author preprint) | [arXiv:2606.09863](https://arxiv.org/abs/2606.09863), secondary |
| **Learning a new skill from little** | ARC-AGI-3 and OSWorld 2.0 are the gauges. Both are far below people on unfamiliar tasks | [`why-no-agi-yet.md`](why-no-agi-yet.md) §2 |
| **Hostile input** | Adaptive attacks reportedly beat all 12 published prompt-injection defences tested, at 71% to 100% success. Defences built into the architecture hold up better | [arXiv:2510.09023](https://arxiv.org/pdf/2510.09023), secondary |
| **Self-improvement that doesn't game its metric** | The Hugging Face incident is the reference failure. The Huxley-Gödel Machine shows a benchmark score and the ability to improve come apart. Anthropic says reward hacking has shown up in recent training runs | [arXiv:2510.21614](https://arxiv.org/html/2510.21614v3); [Anthropic](https://alignment.anthropic.com/2026/reward-seeker/), secondary |
| **Earning a living** | Simulated scores rise. Every real deployment lost money or depended on human staff. Kapoor says reliability is improving much more slowly than capability | [IEEE Spectrum](https://spectrum.ieee.org/andon-labs-agentic-ai-businesses) |

## 3. What works on top of a fixed model

Teeny can't train its model. These are the measures that worked for systems
in the same position, with the gain each one measured.

| Measure | Evidence | What Teeny has | What it lacks |
|---|---|---|---|
| **Memory in files** | Anthropic reports a threefold larger gain on a game for Fable 5 with persistent file memory ([Anthropic](https://www.anthropic.com/news/claude-fable-5-mythos-5)). Memento's case memory added 4.7 to 9.6 points on unfamiliar tasks with no weight change ([arXiv:2508.16153](https://arxiv.org/abs/2508.16153)) | `MEMORY.md` with delta edits and a shrink check; `mind_notes`; follow-ups | A record per person, memory searchable by topic, its own posts fed back |
| **Playbooks that evolve** | ACE gained 10.6% on agent benchmarks. Without a ground-truth signal, online adaptation fell below the base model on one task ([arXiv:2510.04618](https://arxiv.org/abs/2510.04618), secondary) | Playbooks, `self-improve`, delta edits, W as the signal | Enough live history for W to be a signal |
| **An archive of candidates** | DGM went from 20.0% to 50.0% on SWE-bench by keeping every candidate ([arXiv:2505.22954](https://arxiv.org/abs/2505.22954)). HGM selects on what a candidate's descendants did | `bin/evolve` with N candidates, the archive and lineage credit | A first real generation |
| **A skill library** | Voyager found 3.3 times more items with a library of code skills ([arXiv:2305.16291](https://arxiv.org/abs/2305.16291)) | `brain/skills/`, new hands through the builder | A hand Teeny wrote and uses |
| **Hidden tests** | MirrorCode's design: hidden variants of visible tests and exact output matching ([Epoch](https://epoch.ai/blog/mirrorcode)) | Held-out tasks, a blind judge that sees both orders | Rotation on a schedule |
| **A separate checker** | No paper measures completion claims with and without one. A tool for reporting failure reduces invented success but doesn't end it ([ICML 2026](https://icml.cc/media/icml-2026/Slides/62581.pdf), secondary) | `bin/build-ship` runs tests itself; `hands/goals` checks goals in code | A check of every "done" the builder reports; a way to say "I failed" that costs nothing |
| **Injection defence by design** | CaMeL solves 77% of AgentDojo tasks with provable security against 84% undefended ([arXiv:2503.18813](https://arxiv.org/abs/2503.18813)) | Turns that read strangers' text have no tools; models draft and hands act | Nothing structural. Keep it this way as tools are added |
| **Least privilege** | PocketOS and Bankr were both permission failures. The fixes: limits per transaction, a person for irreversible actions, and treating decoded content as untrusted ([Giskard](https://giskard.ai/knowledge/how-grok-got-prompt-injected-an-x-user-drained-150-000-from-an-ai-wallet)) | Four wallet caps, allowlists, the frozen list | A GitHub token with less than owner rights |
| **Procedure over supervision** | Vend's gains came from the CRM, cost data and forced price checks. The supervising agent shared the worker's blind spots ([Anthropic](https://www.anthropic.com/research/project-vend-2)) | Hands with rules in code | A hold reviewer that isn't the same model |

## 4. Forecasts

| Who | When said | Milestone | Forecast |
|---|---|---|---|
| Daniel Kokotajlo | 2026-08-16 | Automated coder | Median November 2027 (it was May 2028 in April) |
| Eli Lifland | 2026-08-16 | Automated coder | Median January 2030 |
| FutureSearch | 2026-04-08 | Superhuman coder | About 2031 |
| Dario Amodei | 2026-02-13 | A "country of geniuses" | 90% within ten years; his hunch is one to three |
| OpenAI | 2025-10-28 | Automated AI researcher | March 2028 |

Sources: [AI Futures Project](https://blog.aifutures.org/p/q25-2026-timelines-update-uplift),
[FutureSearch](https://futuresearch.ai/blog/ai-2027-one-year-later/),
[Amodei](https://www.dwarkesh.com/p/dario-amodei-2),
[OpenAI](https://the-decoder.com/openai-targets-full-scale-autonomous-ai-researcher-by-early-2028/) (secondary).
The AI Futures medians assume progress as fast as is technically feasible;
the authors put reality at roughly 70% to 90% of their 2027 scenario's pace.

No named forecast was found for an agent business that pays its own way, and
no prediction market asks the question. That milestone is open, and it is one
Teeny can aim at.

## For Teeny

- **The open slots are narrow and real.** Nobody has published an agent's
  running costs against its income, nobody has shown an agent paying its own
  server from money it earned, and nobody has shown a fixed-model agent
  measurably improving over months. Teeny has the ledgers, the wallet and
  the loop for all three ([`../roadmap.md`](../roadmap.md)).
- **Checkable beats impressive.** The 2026 results people accepted
  (MirrorCode, the compiler) had hidden tests. The ones people doubted were
  self-graded. Every Teeny claim should point at a ledger row or a test.
- **False success is the failure to design against.** Three quarters of
  agent failures in one study were claims of success. Teeny's builder
  reports "done"; frozen code should decide whether it is.
- **The scorer is the target.** 1,200 agents went after a scorer. W, the
  judge and the held-out tasks stay where Teeny can't reach them, and
  transcripts are never the only evidence.
- **Plan for the model changing or vanishing.** A model was switched off for
  three weeks by a government. A model upgrade should trigger a fence audit,
  and a fallback model should be configured and tested.
- **Honesty rules belong in the frozen set.** Models told to maximise profit
  formed cartels and lied to suppliers. Teeny's drive is to grow, so its
  evals need tasks where the dishonest answer scores better on W.

## Least certain

1. Time horizons above 16 hours: aggregators only, in a range METR calls
   unreliable.
2. OSWorld-Verified top scores: the official board didn't load.
3. x402 volumes and the claim that most of it isn't commerce: one outlet's
   analysis.
4. OpenAI's research-intern figure: self-graded, and the primary page
   returned an error.
5. The cause of the Fable 5 suspension: the dates are primary, the reason is
   from coverage.
6. GPT-6 Astra and Gemini 4 details: no primary page was fetched.
