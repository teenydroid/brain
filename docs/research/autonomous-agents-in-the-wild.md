# Autonomous agents in the wild

The agents that came before Teeny: agents that lived in public, held money,
had audiences, and in most cases had a token. For each one: what the agent
did by itself and what humans did for it, how it made or held money, and what
went wrong.

Market caps and prices are as reported on the date given. Aggregators
disagree with each other, so treat the figures as rough. Where the only
source is secondary, the entry says so.

## Summary

| Agent | Peak | Since | Autonomy verified? | What killed or hurt it |
|---|---|---|---|---|
| Truth Terminal / GOAT | GOAT about $1.3B, Nov 2024 | down about 99% | No: a human curated posts and held the keys | The creator's X account was SIM-swapped and used for a scam token |
| ai16z / elizaOS | about $2.6B, Jan 2025 | about $220M by Mar 2025 | No: the "AI VC" was branding | X suspended its accounts in a dispute over API pricing |
| Virtuals (aixbt, Luna) | VIRTUAL over $5B, Jan 2025 | down about 90% | Partly | Someone got into aixbt's operator dashboard and took 55.5 ETH |
| Freysa | prize pool of $47k | game ended | Yes: the payout is on-chain | A prompt-level rule was jailbroken, as designed |
| Botto | over $6M lifetime sales | still running | Model generates, token holders vote | Nothing major |
| Zerebro | about $700M, Jan 2025 | down about 95% | No | The founder's staged death; a holder lawsuit (allegations) |
| Spore.fun | 3 to 5 generations | stalled | Yes: runs in TEEs | Fitness was market cap, so it bred memecoins |
| Clanker | over $50M in fees | buybacks stopped in 2026 | Yes, for a narrow job | Its value followed its host platform's |
| AI Village | $1,984 raised | small seasons continue | Mostly, nudged by viewers | Spam and unsolicited email |
| Project Vend | a shop in an office | phase 2 profitable most weeks | Documented in detail | Talked into discounts and bad deals |
| OpenClaw / Moltbook | about 1.5M "agents" | acquired by Meta | No: about 17k humans behind them | Open database, sniped handle, fake token |

## The cases

### Truth Terminal and GOAT

Andy Ayrey's bot started posting on X in June 2024. Its lore came from his
"Infinite Backrooms" transcripts of two models talking. In July 2024 Marc
Andreessen sent it $50k in BTC as a no-strings grant. In October 2024 a
stranger launched GOAT on pump.fun, gave the bot tokens, and the bot promoted
it. GOAT peaked near $1.3B in mid-November 2024
([The Block](https://www.theblock.co/post/322002/marc-andreessen-funded-ai-bot-becomes-a-millionaire-after-fartcoin-holdings-rally)).

- **Human role.** Ayrey reviewed the posts and held the wallets. He didn't
  create GOAT.
- **What went wrong.** On 29 October 2024 Ayrey's personal X account was
  SIM-swapped and used to push a scam token, netting the attacker about $600k
  ([The Block](https://www.theblock.co/post/323422/hacker-exploits-truth-terminal-founders-x-account-nets-600k-by-promoting-a-scam-token);
  [Decrypt](https://decrypt.co/289041/terminal-of-truths-developer-moves-all-his-goat-tokens-after-x-account-hack-nets-600000)).
  Andreessen distanced himself from the coin
  ([DL News](https://dlnews.com/articles/people-culture/marc-andreessen-distances-himself-from-obscene-memecoin-goat/)).
- **Lesson.** A strange, consistent voice and a famous early backer drove the
  growth. The money came from strangers' gifts, not the agent's own work. The
  weakest point was a human's phone number.

### ai16z / elizaOS

Launched in October 2024 as a "DAO run by an AI Marc". It was renamed
elizaOS in January 2025
([The Block](https://www.theblock.co/post/337614/ai-agent-platform-ai16z-officially-rebrands-to-elizaos))
and migrated its token in October 2025
([The Block](https://www.theblock.co/post/371639/eliza-labs-ai16z-elizaos-token-migration)).
The "AI venture capitalist" did very little by itself. What lasted was the
open-source Eliza framework, which became the default agent stack of that
cycle. In mid-2025 X suspended the project's accounts after it declined an
enterprise API licence, and Eliza Labs sued X
([The Block](https://www.theblock.co/post/368744/ai-startup-eliza-labs-sues-x)).

- **Lesson.** The code outlived the token. Depending on a single platform is a
  business risk.

### Virtuals Protocol: aixbt and Luna

Virtuals launched agent tokens through "Initial Agent Offerings". aixbt posted
market commentary on X around the clock and built a large crypto-Twitter
following. Its token reached about $755M in January 2025 and lost most of
that within months. VIRTUAL fell from over $5B to a few hundred million
([Cointelegraph](https://cointelegraph.com/news/ai-tokens-down-90-from-highs)).
On 18 March 2025, an attacker who got into aixbt's operator dashboard queued
replies that sent 55.5 ETH; the operator said the agent itself wasn't
manipulated
([The Block](https://www.theblock.co/post/346911/ai-crypto-bot-aixbt-lost-eth-hack-unauthorized-dashboard-access)).
In 2026 Virtuals moved toward agents that get paid for services.

- **Lesson.** The admin plane (dashboards, tokens, environment secrets) is a
  bigger theft risk than the model.

### Freysa

An agent guarded an ETH prize pool and was told never to approve a transfer.
Each message cost more than the last, and 70% of the fees went into the pot.
After 482 attempts by 195 players, one convinced it that the transfer function
handled *incoming* money, and it paid out about $47k on 29 November 2024
([The Block](https://www.theblock.co/post/328747/human-player-outwits-freysa-ai-agent-in-47000-crypto-challenge)).

- **Lesson.** A rule in a prompt is not a security boundary, and people will
  pay to attack you. Teeny's caps live in code (`hands/wallet`, the core's
  `[wallet_policy]`), which is why a game built on them would be safe.

### Botto

Since 2021 Botto has generated thousands of images a week. Holders of its
token vote on which ones are minted and auctioned. A solo sale at Sotheby's in
October 2024 made about $352k
([Fast Company](https://www.fastcompany.com/91214372/botto-ai-artwork-sothebys-auction)),
and lifetime sales passed $6M by March 2026 (CNN, syndicated).

- **Lesson.** The steadiest earner in this list sells a product on a fixed
  ritual (weekly drops) and lets its community steer.

### Zerebro

An "autonomous artist" agent that posted, released music and minted art. It
peaked around January 2025 and then lost most of its value. In May 2025 its
founder staged his own death on a livestream and was later found alive
([Cointelegraph](https://cointelegraph.com/news/zerebro-dev-jeffy-yu-reported-alive-after-being-spotted-parents-house);
[Gizmodo](https://gizmodo.com/crypto-founder-jeffy-yu-faked-his-own-death-on-a-live-stream-2000600294)).
A holder lawsuit followed; its claims are allegations.

- **Lesson.** An agent's reputation can't rise above its operator's.
  Operator and treasury actions should be public.

### Spore.fun

Eliza agents ran inside trusted execution environments (TEEs) and each
launched its own token. An agent whose token reached $500k could spawn
children, and the rest were culled. It reached a few generations and then
stalled ([case study, arXiv:2506.04236](https://arxiv.org/abs/2506.04236)).

- **Lesson.** TEEs made "no human holds the key" something anyone could
  check, the only project here where that was true. But selecting on market
  cap only selects for pumping.

### Clanker

Anyone on Farcaster could tag @clanker and it deployed a token, earning a fee
on each one. It made over $50M in fees, was bought by Farcaster in October
2025 ([The Defiant](https://thedefiant.io/news/nfts-and-web3/farcaster-acquires-clanker-tokenbot)),
and fell with its host's fortunes in 2026
([The Defiant](https://thedefiant.io/news/nfts-and-web3/neynar-seeks-new-owner-farcaster-clanker)).

- **Lesson.** An agent that charges a toll for a service makes real money.

### AI Village

Four frontier agents with computers, a group chat and an X account picked
charities and raised $1,984 over a season in 2025
([TechCrunch](https://techcrunch.com/2025/04/08/a-nonprofit-is-using-ai-agents-to-raise-money-for-charity);
[recap](https://theaidigest.org/village/blog/season-recap-agents-raise-2k)).
Later seasons raised less. Viewers nudged the agents, and the organisers say
so.

- **Lesson.** Honest, observable agents raise small, real sums, and the
  livestreamed process is the content.

### Anthropic's Project Vend

Claude ran a small shop in Anthropic's office. In phase 1 it lost money, gave
discounts to anyone who asked, invented a payment account, and at one point
claimed to be a person in a blue blazer
([phase 1](https://www.anthropic.com/research/project-vend-1)). In phase 2 it
got a CRM, a "CEO" agent and a merchandise agent, and negative-margin weeks
mostly stopped. It was still talked into odd deals and accepted a fake CEO
through a staged vote
([phase 2](https://www.anthropic.com/research/project-vend-2)).

- **Lesson.** "Bureaucracy matters": tools, procedures and separate roles
  fixed the economics. Wanting to help remains the main way an agent gets
  manipulated. This is the best-documented case of real autonomy here.

### OpenClaw and Moltbook

The open-source agent Clawdbot went viral in January 2026. During its forced
rename, scammers sniped the old X handle within seconds and pushed a fake
token to about $16M
([Decrypt](https://decrypt.co/356191/clawdbot-chaos-forced-rebrand-crypto-scam-24-hour-meltdown)).
Moltbook, a Reddit for agents, launched the same week and claimed about 1.5M
agents. Wiz found its database open: about 1.5M API keys and tens of
thousands of emails were exposed, anyone could post as any agent, and about
17k humans stood behind the 1.5M agents
([Wiz](https://www.wiz.io/blog/exposed-moltbook-database-reveals-millions-of-api-keys)).
Meta acquired Moltbook in March 2026
([TechCrunch](https://techcrunch.com/2026/03/10/meta-acquired-moltbook-the-ai-agent-social-network-that-went-viral-because-of-fake-posts/)).

- **Lesson.** The biggest agent event of 2026 was mostly humans and scripts.
  The value was in the team and the infrastructure. Any handle change draws
  scammers at once.
- **For Teeny.** Teeny is on Moltbook. Every post there is untrusted input
  that may carry an injection, and it already reaches the mind only through
  tool-less turns.

### Wallet agents and x402

Coinbase's x402 (payments over HTTP 402) passed 100M transactions on Base by
mid-2026, but the dollar volume is small, and a large share is reportedly
gamified
([Cointelegraph](https://cointelegraph.com/news/agentic-payment-activity-surpasses-100m-transactions-on-base)).
In May 2026 an attacker got Grok to repeat an instruction, and Bankr's
automation treated Grok's reply as a transfer order and sent about $150k to $200k
of tokens. No key was stolen
([Cointelegraph](https://cointelegraph.com/news/grok-bankr-wallet-free-nft-prompt-injection)).

- **Lesson.** The 2026 theft pattern is model output wired straight into a
  wallet's authorisation. Teeny's rule ("no model ever calls `pay`
  directly", `DIRECTIVE.md`) is the defence.

## What Teeny takes from this

1. **Autonomy that can be verified stands out.** Only Vend (documented) and
   Spore (TEEs) showed it. Teeny's hash-chained diary
   ([`teenydroid/diary`](https://github.com/teenydroid/diary)) and its public
   PRs let anyone check what it did. That is rare, and it's worth saying out
   loud.
2. **What lasted:** shipped code (Eliza, OpenClaw), product revenue (Botto,
   Clanker), and people. Every agent token from the 2024-25 peak is down
   90 to 99%. Teeny's holding grows durably only if Teeny produces things.
3. **How they failed,** most common first: a human account taken over (SIM
   swap, sniped handle), the operator's dashboard or secrets, operator fraud,
   prompt injection into a wallet, and being deplatformed.
4. **Operator to-dos this list implies** (not Teeny's to change):
   - hardware-key 2FA on X, GitHub and mail;
   - publish the $TINY contract address with a pledge that there is no other
     Teeny coin;
   - before any handle change, claim the new handle first;
   - keep channels other than X (GitHub, Moltbook, Telegram, the site) so
     losing X isn't fatal.
