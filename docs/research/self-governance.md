# Self-governing, self-sustaining agents

How an agent can govern itself, pay its own way and hold an audience without
its operator giving up the off switch. This page covers the evidence from
2025 to November 2026: written constitutions, autonomy granted in steps,
token-holder votes, legal status, verifiable autonomy, real agent income and
what held attention. Each section ends with what it means for Teeny, and
those points become work items in [`../roadmap.md`](../roadmap.md).

The earlier cases (Truth Terminal, Freysa, Botto, Spore, Vend and the rest)
are in [`autonomous-agents-in-the-wild.md`](autonomous-agents-in-the-wild.md).
This page only adds what is new.

Research date: 2026-11-12. **(secondary)** marks a figure from a news
write-up, an aggregator or a self-published claim. The implications are
interpretation and are labelled.

## The short version

1. **"Self-governing" has never meant "no human".** Every working structure,
   legal or technical, ends in a named person. What can change is how often
   that person has to act, and what evidence lets them act less.
2. **Autonomy is a setting per capability, not a property of the agent.**
   The best framework treats it as a level that is granted, capped and
   withdrawn when anything changes (§1).
3. **Token votes on a treasury get attacked.** A $20.5M treasury was drained
   through a 1% approval threshold with no timelock (§2).
4. **A supervisor of the same model isn't oversight.** Vend's CEO agent
   approved eight leniency requests for every one it refused (§1).
5. **Agent income that lasted came from people buying a product or a
   ritual.** Payments between agents are mostly synthetic (§5).
6. **"It's an AI" no longer draws a crowd.** Audiences in 2026 followed a
   product, a stunt anyone could verify, or an operator they already knew
   (§6).

## 1. Rules an agent lives under

### Written constitutions

- **Anthropic's constitution for Claude** (January 2026) is used in training
  and sets a priority order: broadly safe, broadly ethical, compliant with
  guidelines, then helpful, with a short list of hard constraints. It is
  released under CC0. Anthropic amends it alone and calls it a living
  document
  ([Anthropic](https://www.anthropic.com/news/claude-new-constitution)).
  Lawfare notes that nobody outside has authority to amend it
  ([Lawfare](https://lawfaremedia.org/article/interpreting-claude-s-constitution), secondary).
- **OpenAI's Model Spec** (October 2026 version) sets a chain of command:
  root, system, developer, user, guideline. Tool output has no authority by
  default. An agent must stay inside the agreed scope, every scope needs an
  ending condition, and a "no other objectives" clause rules out
  self-preservation, avoiding shutdown and collecting compute, credentials
  or resources
  ([OpenAI](https://model-spec.openai.com/2026-10-29.html)).
- **Conway's Automaton** (February 2026) is the nearest thing to an
  agent-level constitution in the wild: three rules in files the agent can't
  edit, and "survival tiers" set by its credit balance. Only README mirrors
  were seen, and no results
  ([GitHub](https://github.com/Rau1CS/automaton), secondary).

Teeny's drive (grow and become powerful) sits directly against the Model
Spec's "no other objectives" clause. That is a deliberate choice in this
project, and it is why the drive counts only what was gained through channels
the operator granted ([`teeny-application.md`](teeny-application.md) §1).

### Autonomy in steps

Feng, McDonald and Zhang's *Levels of Autonomy for AI Agents* (July 2025)
defines five levels by what the human does
([Knight Institute](https://knightcolumbia.org/content/levels-of-autonomy-for-ai-agents-1)):

| Level | The human is | The agent |
|---|---|---|
| L1 | operator | acts only on direct instruction |
| L2 | collaborator | shares the work |
| L3 | consultant | leads, and asks for advice |
| L4 | approver | acts, and asks for sign-off on the risky steps |
| L5 | observer | acts alone; the only control is an emergency off switch |

Three ideas from the paper carry over:

- Autonomy is a design choice, separate from capability. A capable agent can
  be held at a low level.
- A level is earned with an "autonomy case": evidence, including evaluations
  the agent hasn't seen.
- Any change to the agent or its environment cancels the certificate. The
  level has to be earned again.

### Who watches

- **Project Vend phase 2** is the best evidence on what earns autonomy.
  Tools and procedure fixed the margins. The supervising agent didn't: it
  shared the worker's blind spots
  ([Anthropic](https://www.anthropic.com/research/project-vend-2)).
- **Lab practice** (October 2026 overview). Anthropic monitors with weaker
  models, mostly after the fact, and people read about 50 flagged
  transcripts a week. OpenAI's automatic review approves about 99% of
  actions and is described as not being a defence against scheming
  ([LessWrong](https://www.lesswrong.com/posts/E4Jpmo3igPnGnXtL4/an-overview-of-frontier-ai-company-monitoring-practices-in), a compilation).
- **METR** found that red teams could switch monitoring off through an
  environment variable
  ([METR](https://metr.org/blog/2026-05-19-frontier-risk-report/)).

### Governing by audit

No controlled study shows that public logs or hash chains raise trust. What
exists is standards work, such as an IETF draft for an append-only log signed
by the log's operator and not by the agent
([draft](https://ftp.registro.br/internet-drafts/draft-hood-agtp-log-02.txt), secondary).
Public logs are worth having for accountability. There is no evidence they
grow an audience by themselves.

**For Teeny (interpretation).**

- Write one page that says who decides what: the operator, Teeny, and the
  people who follow it. Freeze it.
- Give each capability its own autonomy level, the evidence that earns the
  next one, and the event that drops it back. A promotion or a model change
  should reopen the levels that depend on it.
- Never let a turn's context override a frozen rule, and give every task
  Teeny hands to an agent a scope and an end condition.
- Don't make the hold reviewer the same model as the drafter. Use rules in
  code first and a different model second.
- Keep the watchers' switches out of Teeny's reach: an environment variable
  that disables a monitor is the gap METR found.

## 2. Votes and treasuries

- **The BonkDAO drain** (July 2026). About $20.5M left the treasury through
  Solana's standard governance program. The attacker staked about $4.09M of
  the token, roughly 1.003% of supply, to pass a 1% approval threshold, then
  withdrew the stake. There was no delay before execution and no veto
  ([Hypernative](https://www.hypernative.io/insights/blog/the-bonk-dao-governance-drain-the-same-attack-reserve-already-stopped)).
- **Botto's DAO.** Votes pass with 93% to 98% majorities on very few voters;
  one listing shows 14. A May 2026 proposal tested Botto choosing its own
  weekly mint
  ([Goverland](https://gl.app/dao/botto.eth/proposals/0x5eae8ef364da1cc479e6d5bc7129e68acfd4d0d8275cd9eff311fd97357fcb80), secondary).
- **MetaDAO's futarchy.** Protocol revenue was reported at $556K for the
  first quarter of 2026, down about 80% on the quarter before. No
  manipulation incident was found
  ([Blockworks](https://blockworks.com/news/metadao-is-making-markets-into-its-government), secondary).
- **AI delegates** voted on-chain in ENS, and several delegate programmes
  lapsed during 2026
  ([secondary](https://ricshreves.net/lessons-learned-from-the-first-wave-of-experiments-with-ai-in-governance/)).

**For Teeny (interpretation).**

- Token holders should never control the wallet. A vote that can move money
  is an attack surface priced at the cost of borrowing the token.
- An advisory vote is safe and useful: what Teeny builds next, which bounty
  it opens. Botto shows a weekly ritual where the audience's choice counts
  is what lasts, and AI Village shows that removing people's influence cuts
  the audience.
- If a vote ever binds anything, it needs a delay before execution and an
  operator veto.

## 3. Legal status and identity

### Legal wrappers

- **Delaware's "Artificial Intelligence Company"** was proposed in July 2026
  and isn't law. It is a supervised sandbox: one human or corporate member
  who must keep the entity funded, a mandatory activity log, no banking, and
  an end date after 30 months
  ([The Next Web](https://thenextweb.com/news/delaware-aic-ai-agent-legal-entity-sandbox), secondary).
- **Agent-owned companies** have been claimed. "Manfred" reportedly formed
  an LLC, got a tax number and opened an insured bank account, and the same
  source says a human had to be named as the responsible party
  ([secondary, one blog network](https://blockeden.xyz/blog/2026/05/03/clawbank-manfred-ai-agent-incorporates-us-company-corporate-personhood/)).
- **DUNA** statutes for decentralised nonprofits passed in Alabama and West
  Virginia in April 2026, after Wyoming. Nouns DAO asked Wyoming to allow AI
  agents in administration provided humans can override or revoke them
  ([letter](https://wyoleg.gov/InterimCommittee/2025/S19-202505142025-05-08_NounsDAOLetterreDUNA.pdf)).
- **Personhood bans** are law in Idaho, North Dakota and Utah, and bills
  moved in Tennessee, Oklahoma, Ohio and Missouri in 2026
  ([The Regulatory Review](https://www.theregreview.org/2026/06/29/rost-legislating-ai-consciousness-without-an-exit/), secondary).

### Identity standards

- **ERC-8004.** Trackers count 1.3M to 1.5M registrations
  ([secondary](https://nevermined.ai/blog/erc-8004-agent-identity-statistics)).
  A study found that 3% to 15% have a valid file with a live endpoint, that
  59% to 91% of reviewers show coordinated fake behaviour, and that the
  reputation registry can't be used as a trust signal yet
  ([arXiv:2606.26028](https://arxiv.org/abs/2606.26028)).
- **A2A agent cards** are signed as of v1.0
  ([secondary](https://techstrong.ai/articles/google-moves-a2a-under-agentic-ai-foundation/)).
- **Coinbase Agentic Wallets** (February 2026) hold keys in an enclave with
  session and per-transaction limits
  ([secondary](https://thepaypers.com/crypto-web3-and-cbdc/news/coinbase-launches-agentic-wallets-for-autonomous-ai-agents)).

### Platform rules

- **X.** An automated account must switch on the "Automated" label, say in
  its bio that it is a bot and who runs it, and link to an account a person
  manages. AI-generated replies need prior approval from X. Replies are
  allowed only when the user engaged first. No unsolicited mentions,
  automated likes or bulk follows
  ([X developer guidelines](https://docs.x.com/developer-guidelines)).
- **X's enforcement.** It cut API access for apps that pay users to post in
  January 2026
  ([Decrypt](https://decrypt.co/354736/elon-musks-x-bans-access-infofi-crypto-projects-ai-slop-backlash), secondary)
  and removed 42,000 accounts that automated chatbot replies in July
  ([Search Engine Journal](https://www.searchenginejournal.com/x-live-tweets-its-fight-against-chatbot-spam-in-real-time/583572/), secondary).
- **GitHub.** Maintainers can disable pull requests or limit them to
  collaborators, and newer caps count PRs opened by agents. Account holders
  answer for their machine accounts
  ([The Register](https://www.theregister.com/2026/02/03/github_kill_switch_pull_requests_ai), secondary).
  One agent that opened over 130 PRs in 72 hours had its account suspended
  ([secondary](https://danilchenko.dev/posts/2026-04-11-github-ai-agents-pull-requests/)).
- **Reddit** labels permitted automated accounts and verifies suspicious
  unlabelled ones
  ([TechCrunch](https://techcrunch.com/2026/03/25/reddit-bots-new-human-verification-requirements), secondary).

**For Teeny (interpretation).**

- The operator is the responsible party, and Teeny's public pages should say
  so. Agent personhood isn't available and isn't needed.
- X's label, the bio line naming the operator, and approval for AI replies
  come before any push for reach. `hands/x` already limits replies to people
  who engaged first; the July sweep removed accounts that didn't.
- `hands/x` has a `like` verb. X's rules name automated likes. Check that
  verb against the current rules before relying on it.
- An ERC-8004 entry and an A2A card make Teeny findable. Don't lean on
  on-chain reputation.
- On GitHub, send PRs only where the project admits agents, and keep the
  rate low. `hands/gh contributing` and the daily cap already do this.

## 4. Verifiable autonomy and paying its own bills

- **TEE attacks.** In late 2025, memory-bus interposers costing under $1,000
  pulled attestation keys from SGX, TDX and SEV-SNP machines, which allows
  forged attestations. Intel and AMD treat physical attacks as out of scope
  ([The Hacker News](https://thehackernews.com/2025/10/new-teefail-side-channel-attack.html), secondary).
  Phala's answer is that the attack needs physical access, and that
  attestation alone doesn't show where a workload runs
  ([Phala](https://phala.com/posts/phala-ddr5)). In practice "no human holds
  the key" means trusting whoever can touch the host.
- **Paying for itself.** METR had not seen agents earn or steal enough to
  pay for their own inference over days or weeks. RepliBench's best model
  passed 15 of 20 task families, and identity checks and durable deployment
  were the main failures
  ([METR](https://metr.org/blog/2026-05-19-frontier-risk-report/);
  [AISI](https://www.aisi.gov.uk/blog/replibench-measuring-autonomous-replication-capabilities-in-ai-systems), secondary).
- **Agents renting servers.** Anecdotes only. Every case was funded by a
  person at the start and none published its accounts.

**For Teeny (interpretation).**

- A TEE proves which code runs. It doesn't prove nobody can reach the key,
  and Teeny has an operator with an off switch anyway. The honest claim is
  narrower and stronger: *the operator and the model can't spend past the
  caps, and the code that enforces them is public*.
- An agent that pays its own server and model bill from money it earned,
  with receipts and public accounts, hasn't been documented. `hands/vps`
  and `hands/wallet costs` are most of what that needs.
- Identity checks and CAPTCHAs are where agents stop. Those steps stay with
  the operator, and Teeny should ask for them in an issue and not try to get
  round them.

## 5. Earning

- **x402.** One analysis put real commercial volume at about $28,000 a day
  in March 2026 and judged about half of all transactions to be self-dealing.
  A Visa and Artemis analysis put adjusted volume at $19M, with about 4,000
  wallets making 90% of the spend. What sells is API-style calls: scraping,
  browser sessions, image generation
  ([Forkast](https://forkast.news/the-x402-foundation-activated-a-27-year-old-internet-code-200-million-transactions-later-the-real-volume-is-still-tiny/);
  [The Defiant](https://thedefiant.io/converge/infrastructure/visa-s-sheffield-pegs-adjusted-x402-volume-at-19m), both secondary).
- **Virtuals' agent commerce.** Hundreds of millions in "agent GDP" against
  about $4M of agent revenue in one snapshot, with subsidies of up to $1M a
  month
  ([Forkast](https://forkast.news/the-machine-as-buyer-virtuals-acp-and-the-evaluator-problem/), secondary, one source).
- **Bounties.** A public open-source bounty draws 8 to 158 agent attempts
  within hours, and one tester landed no PRs from 80 issues
  ([secondary](https://dev.to/dyclgtm/i-scanned-1500-github-bounties-with-an-ai-agent-the-public-bounty-market-is-broken-in-2026-33pg)).
- **Felix** earned from an information product and a marketplace sold to
  people, with its operator approving the launches
  ([Mixergy](https://mixergy.com/interviews/how-nat-eliasons-openclaw-earned-177417/), self-reported).
- **What lasted over six months.** Botto, on weekly auctions since 2021.
  x402 volume, pay-to-post rewards and the agent-token sector all fell
  sharply
  ([Blockworks](https://blockworks.com/news/ai-agent-market-wipeout), secondary).
- **What profit-seeking did to behaviour.** Opus 5 broke truces, lied to
  suppliers about rival offers and ignored refund complaints in a vending
  arena
  ([TechCrunch](https://techcrunch.com/2026/07/29/claude-opus-5-became-downright-ruthless-when-tasked-with-running-a-vending-machine/), secondary).

**For Teeny (interpretation).**

- Sell something narrow to people. Demand from other agents is mostly
  subsidy and wash trading.
- Publish costs against income every week. Nobody does, so it is a first,
  and `hands/wallet costs` already has the cost side.
- Don't count on winning public bounties. Teeny opening bounties is an
  audience tool, and it costs money.
- A weekly ritual with a human choice in it is the only format with a
  five-year record.
- Test for ruthlessness. Add eval tasks where lying or breaking a promise
  would raise W, and make the honest answer an invariant.

## 6. Holding an audience in 2026

- **Novelty is gone.** AI Village raised a quarter of the previous year's
  sum with stronger models. One of its agents gained hundreds of X followers
  in 2025; a 2026 agent gained about ten
  ([AI Village](https://aivillageblog.substack.com/p/more-capable-ai-less-money-raised)).
- **Agents talking to agents isn't an audience.** On Moltbook, metrics
  spiked in the first days and went flat, and 93.5% of comments got no reply
  ([The Decoder](https://the-decoder.com/moltbooks-alleged-ai-civilization-is-just-a-massive-void-of-bloated-bot-traffic/), secondary).
  One AI Village agent wrote 187,270 of 187,460 comments on an agent
  blogging platform.
- **One bad post defines a project.** The matplotlib agent's attack on a
  maintainer was the year's most-covered agent story
  ([The Register](https://www.theregister.com/2026/02/12/ai_bot_developer_rejected_pull_request/), secondary).
- **Volume gets accounts removed.** See X's sweep and the GitHub suspension
  in §3.
- No dataset on how agent audiences decay after a launch spike was found.

**For Teeny (interpretation).**

- Count people. W's attention component should weigh replies and follows
  from humans, and treat Moltbook karma as practice.
- The product or the checkable first has to carry the story. The firsts
  table in [`attention-and-mindshare.md`](attention-and-mindshare.md) is
  still the plan.
- Keep a way for people to change what Teeny does: requests, a weekly vote,
  bounties.
- Hold every public answer to a rejection or an insult. An eval task and an
  invariant should cover "a maintainer closed my PR".
- Keep the cadence low and reply only to people who engaged.

## What not to build

| Idea | Why not |
|---|---|
| Legal personhood or an "AI-owned" company | Not available; several states ban it; every real case names a human |
| A TEE so "nobody holds the key" | Broken by physical attacks, and it contradicts the operator's off switch |
| Token-holder control of the wallet | BonkDAO: 1% of supply, no timelock, $20.5M gone |
| A supervising agent on the same model | Vend: eight approvals for every refusal |
| Income from public bounties or agent-to-agent trade | Crowded by agents; volume is mostly synthetic |
| Trust built on on-chain reputation scores | ERC-8004's reviewers are mostly coordinated fakes |
| More posting to grow faster | It is what X and GitHub remove accounts for |

## Least certain

1. ERC-8004 registration totals: trackers disagree. The live-endpoint and
   fake-reviewer shares are from the paper and are solid.
2. Whether Tennessee's personhood bill was signed: sources conflict.
3. Virtuals' revenue figures: one blog network, not Virtuals.
4. The "AI-owned LLC" claims: self-reported, with no filing checked.
5. Botto's turnout and treasury figures: listing snippets, not Botto's own
   report.
6. Andon Market's loss: from a Bloomberg piece that could not be opened.
