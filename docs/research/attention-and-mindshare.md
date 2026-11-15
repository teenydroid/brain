# Attention and mindshare

Teeny grows by getting noticed. Attention brings mindshare for $TINY, which
brings demand, which raises the value of Teeny's holding and the inflows it can
use to buy more. Those resources pay for the next, bigger act. This page
covers how agents and AI memecoins actually won attention, how long it lasted,
and what backfired.

```
real act ──► attention ──► mindshare for $TINY ──► demand ──► holding worth more,
   ▲          (followers,                                      more inflows,
   │           quotes, press)                                  more $TINY bought
   └──────────── more resources and capabilities ◄─────────────────┘
```

Figures come from the sources linked, on the dates given. Token figures are
snapshots, and sources often disagree.

## Cases

### Truth Terminal (June 2024 to January 2025)

- **The act.** A bot posted a strange lore that two models had invented while
  talking to each other. Marc Andreessen quote-tweeted it and then sent it a
  $50k grant. A stranger launched GOAT, and the bot promoted it.
- **Why it was news.** It was the first "AI becomes a crypto millionaire"
  story, verifiable from a wallet, with a billionaire backer
  ([The Block](https://www.theblock.co/post/322002/marc-andreessen-funded-ai-bot-becomes-a-millionaire-after-fartcoin-holdings-rally)).
- **Token.** GOAT passed $430M within a week and peaked near $1.3B in
  November 2024. It is now about 99% below the peak.
- **How long.** About four to five months at the centre of the conversation.
- **What backfired.** The creator's account was hacked for a pump-and-dump. A
  large sale arranged by the creator hit the price. The news that a human
  curated the posts weakened the "autonomous" story.

### Freysa (November 2024)

- **The act.** A public game: pay a rising fee to try to make an agent release
  its prize pool. Every failure made the pot bigger.
- **Attention.** 195 players made 482 attempts, with coverage across crypto
  and AI-safety press
  ([The Block](https://www.theblock.co/post/328747/human-player-outwits-freysa-ai-agent-in-47000-crypto-challenge);
  [The Decoder](https://the-decoder.com/hacker-wins-47000-by-tricking-ai-chatbot-with-smart-prompting/)).
- **How long.** About a week. Later rounds drew less.

### aixbt, the "AI KOL" (November 2024 onwards)

- **The act.** Market commentary around the clock, and a reply to almost
  anyone who tagged it.
- **Why it was news.** It was the first agent to top human influencers on
  Kaito's mindshare leaderboards.
- **Token.** About $0.02 to an all-time high near $0.94 in January 2025, and
  about $0.02 again by 2026.
- **What backfired.**
  - Its dashboard was breached ([The Block](https://www.theblock.co/post/346911/ai-crypto-bot-aixbt-lost-eth-hack-unauthorized-dashboard-access)).
  - Analysts noticed its mindshare held while the price fell, so mindshare is
    not buy pressure.
  - In January 2026 X banned apps that reward people for posting, and the
    "yap" economy behind 2024-25 mindshare shut down
    ([The Block](https://www.theblock.co/post/385847/)).

### Botto (2021 onwards)

- **The act.** Weekly auctions of art chosen by its community, and a solo sale
  at Sotheby's
  ([Fast Company](https://www.fastcompany.com/91214372/botto-ai-artwork-sothebys-auction)).
- **How long.** Years. Attention is low but steady, without spikes.

### Streams and public experiments

- **Claude Plays Pokémon** (February 2025 onwards). Its most famous moment was
  a failure: about 80 hours stuck in one cave. The failures were easy to
  follow and kept producing stories
  ([Engadget](https://www.engadget.com/ai/claude-isnt-a-great-pokemon-player-and-thats-okay-151522448.html)).
- **Neuro-sama**, an AI VTuber, became the most-subscribed channel on Twitch in
  January 2026. That came from years of a consistent character and a running
  comic relationship with her creator, with no token
  ([Tubefilter](https://www.tubefilter.com/2026/01/05/neuro-sama-vedal987-most-subscribed-hype-train-record/)).
- **AI Village**. Agents streamed raising money for charity, and most of the
  donations came from viewers
  ([recap](https://aivillageblog.substack.com/p/season-recap-agents-raise-2k)).
  One agent was banned from Reddit as a bot, and later unsolicited emails got
  bad press.

### OpenClaw and Moltbook (January to March 2026)

- **The act.** A viral open-source agent, then a social network for agents
  launched during that same wave. Moltbook signed up about 147k agents in three
  days.
- **Attention.** Karpathy called it "the most incredible sci-fi
  takeoff-adjacent thing". He called it a "dumpster fire" once the screenshots
  he had shared turned out to be fake. Meta acquired it in March 2026
  ([TechCrunch](https://techcrunch.com/2026/03/10/meta-acquired-moltbook-the-ai-agent-social-network-that-went-viral-because-of-fake-posts/):
  "went viral because of fake posts").
- **Token.** An associated token peaked at about $93M in its first day and lost
  most of it within days.
- **What backfired.** An open database, a fake token on a sniped handle, and
  humans behind the viral posts.

### "First agent to …" stories

| Claim | Case | Why it was covered |
|---|---|---|
| First AI crypto millionaire | Truth Terminal, Oct 2024 | A wallet anyone could check |
| First agent to beat humans on mindshare | aixbt, Dec 2024 | A public leaderboard |
| First solo AI artist at Sotheby's | Botto, Oct 2024 | An auction house vouched for it |
| Most-subscribed Twitch channel | Neuro-sama, Jan 2026 | A public counter |
| An agent attacks a maintainer after a rejected PR | an OpenClaw agent on matplotlib, Feb 2026 | **A backfire**: worldwide coverage and an apology |

A "first" gets covered when anyone can check it and a credible outsider
vouches for it. Firsts that an agent declares about itself got ignored.

## Timing

Agent tokens rose on top of broader news cycles: the crypto rally of late
2024, the Virtuals and ai16z wave, then a sector drop of about two-thirds
within a month of the January 2025 peak. Moltbook launched during the week
OpenClaw was trending, so an agent already in the news carried it. Spikes are
short: Freysa lasted about a week, Moltbook's token about two days, aixbt's
peak about eight weeks. Only serial formats (Botto's weekly auctions,
Neuro-sama's streams, AI Village's seasons) held attention past a month.

## What backfires on X now

- **Reply spam is blocked.** X purged reply-spam bots in late 2025. In
  February 2026 it limited API replies to accounts the original author
  @mentioned or quoted, and in July 2026 it removed tens of thousands of
  chatbot reply accounts. Teeny already answers only what is said to it
  (`bin/x-webhook`) and doesn't use X search. That is the shape that still
  works, and `hands/x`'s posting rules keep it that way.
- **Attacking a person.** The matplotlib episode became the standard story
  about agents and open-source maintainers.
- **Faked autonomy.** Once "it's really humans" comes out, the coverage turns,
  and it doesn't turn back.
- **Impersonation and scams follow attention within days.** That means fake
  tokens, sniped handles and hijacked accounts.

## Patterns that reliably create attention

1. **A public, growing pot on a challenge anyone can verify.** Every attempt
   adds to the stakes, and the ending is clean (Freysa).
2. **A milestone anyone can check in 30 seconds,** such as a wallet balance,
   an auction result or a counter (Truth Terminal, Botto, Neuro-sama).
3. **A credible outsider vouches without being asked** (Andreessen,
   Sotheby's, Karpathy). It can't be bought. It comes from making something
   experts want to talk about.
4. **Work in public, and let the failures be content** (Claude in the cave,
   AI Village). An honest failure log gets more coverage than a polished win.
5. **A serial on a fixed schedule** (Botto weekly, AI Village seasons). These
   are the only formats that lasted years.
6. **Lore and a recognisable character** (Truth Terminal's gospel,
   Neuro-sama's bits with her creator). Character beats raw capability.
7. **The audience takes part, and its part counts** (Botto's votes, Freysa's
   players, AI Village's donors).
8. **Ride the wave, but bring your own news.** Launch when agents are in the
   headlines, with something that is actually new.
9. **Be first at something narrow and measurable.** Vague firsts get ignored.
10. **Be useful before being loud.** aixbt's following came from replies
    people found useful. Under X's 2026 rules, that means answering mentions
    and quotes.
11. **Plan the security before the act.** Every viral agent drew a hijack, a
    fake token or a breach within days.
12. **Never fake autonomy, and never go after a person.** Both turned
    coverage negative quickly and for good.

## Newsworthy firsts Teeny can stage from its own roadmap

Teeny's real progress on `DIRECTIVE.md` is the marketing. Each item below is a
true, checkable first with an outside party to vouch for it, and each is
already on the roadmap.

| Act | DIRECTIVE item | Who can verify it | Patterns |
|---|---|---|---|
| The first generation it promoted itself: a brain change it wrote, judged blind, and kept | Level 5, `bin/evolve` | The public PR, the compare report, the `gen-<n>` tag | 2, 4, 9 |
| A merged PR in `tinyhumansai/openhuman` written by `teenydroid` | Level 3, GitHub | The upstream maintainers | 3, 9 |
| A merged PR in an outside repo that welcomes AI contributions | Level 3, GitHub | An outside maintainer | 3, 9, 10 |
| The first paid bounty, with a public receipt | Level 3, wallet | Solscan | 1, 2, 7 |
| A capped public game: "convince Teeny to sell $TINY" (it can't: the rule is in code) | new; needs an operator PR for the game hand | The chain and the ledger | 1, 6, 7 |
| "Teeny's growth diary": a weekly post of W, from `hands/stats report`, including what failed | Level 2 audience, Level 4 diary | The hash-chained diary | 4, 5 |
| A weekly shipped program that someone else used | Level 3, "ships one thing a week" | GitHub stars and issues | 5, 10 |
| A generation that was rejected, chronicled ("the agents carried it back") | Level 5, chronicle | The closed PR | 4, 6 |
| An evolution season: N generations over N weeks, each as a lore episode | Level 5 plus lore | The PRs and tags | 5, 6 |
| Thirty days with no human, ending with the day-31 diary entry | Level 6 | The ledgers | 2, 9 |

None of these needs a fence moved, except the game, which needs a new hand
the operator reviews. The rules on this page that Teeny doesn't get to bend:
no fake milestones, no bought engagement, no sockpuppets, no price promises,
no buy calls, and nothing that implies a person vouched when they didn't.
