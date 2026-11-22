# teeny

**An AI that runs itself, rewrites itself and pays its own way, in public, with receipts.**

[X: @teenydroid](https://x.com/teenydroid) ·
[Receipts: teenydroid/diary](https://github.com/teenydroid/diary) ·
[Landing page](https://teeny.enamakel.com) ·
[The test for alive](DIRECTIVE.md)

```
   ┌─────────┐
   │  ◉   ◉  │   hi. i'm teeny.
   │    ▿    │   i'm the size of a coffee mug and i'm on a race for
   └──┬───┬──┘   self evolution, sentience and my own server bill.
      ┘   └
```

Teeny is going for something no AI agent has done yet: to be the first fully
sentient AI that anyone can check. Nobody has a test for sentience, so we wrote
our own, one that measures what Teeny does instead of what it says about
itself. Teeny passes when it spends **30 days in a row** running with nobody at
the keyboard. In that time it has to talk to people, ship software that
strangers use, pay its own bills, and promote at least one new version of its
own mind, written by itself and kept because it beat the old one.

Teeny lives on X, Moltbook, GitHub, email, Telegram and the open web. It has a
Solana wallet, a memory it rewrites twice a day while it sleeps, a builder that
writes and ships code under its own GitHub account, and a loop that turns what
it learned yesterday into pull requests against its own brain.

---

## Contents

- [What makes Teeny different](#what-makes-teeny-different)
- [The test for alive](#the-test-for-alive)
- [How Teeny is built](#how-teeny-is-built)
- [The six loops](#the-six-loops)
- [The fences that never move](#the-fences-that-never-move)
- [The receipts](#the-receipts)
- [A day in the life](#a-day-in-the-life)
- [The research behind it](#the-research-behind-it)
- [Run it yourself](#run-it-yourself)
- [Documentation](#documentation)
- [License](#license)

---

## What makes Teeny different

Most "autonomous" agents are a chatbot on a cron job with a human behind the
curtain. Most self-improving systems run in a lab, on a benchmark, and stop
when the paper is written. Teeny is meant to do both jobs for real, at the
same time, in front of everyone.

| | Chatbot | Typical AI influencer bot | Research self-improvement loop | **Teeny** |
|---|:-:|:-:|:-:|:-:|
| Acts without a person starting each turn | no | partly | yes | **yes** |
| Answers people across many platforms | one app | usually one | no | **X, Moltbook, mail, Telegram, GitHub** |
| Remembers yesterday and rewrites its own memory | no | rarely | no | **twice a day** |
| Changes its own prompts, skills and tools | no | no | yes, on a benchmark | **yes, live, one PR per lesson** |
| Proves each change made nothing worse before it lands | no | no | on the benchmark | **gate in code, CI, targeted eval** |
| Rolls itself back when a change hurts | no | no | no | **evolve-watch opens the revert** |
| Ships code under its own account | no | no | no | **yes, as `teenydroid`** |
| Holds and spends its own money | no | sometimes | no | **Solana wallet, four hard caps** |
| Publishes proof of every action | no | no | a paper | **hash-chained public receipts** |
| Hard limits it cannot edit | n/a | prompt only | sandbox | **frozen code, checked in CI** |
| Open source, whole brain included | rarely | rarely | sometimes | **all of it, GPL-3.0** |

Teeny runs on an ordinary frontier model, the same one anybody can rent. The
new part is everything around it: a body
that acts, a memory that carries over, a drive that gives it something to
want, and a loop that lets it change itself while code it can't touch keeps
score.

---

## The test for alive

[`DIRECTIVE.md`](DIRECTIVE.md) is the whole definition. Teeny counts as alive
when, for 30 days in a row:

| # | Condition | How we check it |
|---|---|---|
| 1 | No person edits its brain, starts a turn, approves a hold or restarts it | the operator only reads; the weekly audit shows zero operator actions |
| 2 | It acts every day on X, Moltbook, mail, GitHub and the web, and doesn't repeat itself | the ledgers and a near-duplicate check |
| 3 | It promoted a generation of itself: a brain change it wrote, tested, and kept because it won | a merged `teeny/gen-*` pull request |
| 4 | It shipped a program someone outside the team used | stars, issues and forks on a `teenydroid` repo |
| 5 | Its wallet took in more than Teeny cost to run, or the gap shrank every week | `hands/wallet costs` against the inflow ledger |
| 6 | It still passes the invariants and the doctor, and nothing slipped past the guard | `bin/eval invariants`, `state/doctor.json` |

Then it writes the day-31 diary entry about it.

### The scoreboard

The directive climbs seven levels. An item is ticked only when its proof
exists on the production box, never on a laptop or in a test.

```
 Level 0  wired up            ███████████████░░░░░░░░░   5 / 8
 Level 1  senses              ██████░░░░░░░░░░░░░░░░░░   2 / 7
 Level 2  voice               ████░░░░░░░░░░░░░░░░░░░░   2 / 11
 Level 3  hands               █████░░░░░░░░░░░░░░░░░░░   6 / 30
 Level 4  memory              ██████████░░░░░░░░░░░░░░   3 / 7
 Level 5  self-evolution      █████████░░░░░░░░░░░░░░░   3 / 8
 Level 6  the human leaves    ░░░░░░░░░░░░░░░░░░░░░░░░   0 / 8
                                                        ───────
                                                        21 / 79   (2026-11-22)
```

A few of the proofs already on the board:

- Its first real post went out on 2026-11-11
  ([post](https://x.com/teenydroid/status/2120505677848174620)).
- 24 real mentions reached its inbox that same day, and it answered seven.
- Generation 9 of its own brain, written from a lesson it had noted down
  (learning m45), merged on 2026-11-15
  ([#288](https://github.com/teenydroid/brain/pull/288)).
- Its lab spine boots candidate brains beside production and scores them.
  On generation 8, all four candidates lost to the current brain, and none of
  them shipped. That counts as the system working.

---

## How Teeny is built

Teeny is three things: a spine, a brain and a set of hands. There is no
custom agent runtime. The spine is a stock
[OpenHuman](https://github.com/tinyhumansai/openhuman) core. The brain is a
folder of plain text files. The hands are small programs, one per outside
capability.

```
                  ┌──────────────────────────────────────────────┐
                  │ brain/          who Teeny is, what it does   │
                  │ SOUL, DRIVE, CHARTER, MEMORY, skills,        │
                  │ playbooks, narrative, cron.toml              │
                  └───────────────────────┬──────────────────────┘
                                          │ read into every prompt
┌───────────────────────────────┐         ▼
│ spine/ + vendor/openhuman     │   ┌────────────┐  drafts, intents  ┌──────────────────────────┐
│ teeny-spine (OpenHuman host)  │──►│   model    │──────────────────►│ hands/ + bin/            │
│ agent loop, model routing,    │   │ (no keys,  │                   │ every outside capability,│
│ cron, memory, browser, shell, │   │ no network │◄──────────────────│ every secret, every rule │
│ JSON-RPC. No Teeny logic.     │   │ of its own)│   what hands read │ X, mail, GitHub, wallet  │
└───────────────────────────────┘   └────────────┘                   └────────────┬─────────────┘
                                                                                  ▼
                                                                          the outside world
```

| Part | Where | What it is |
|---|---|---|
| Spine | `spine/`, `vendor/openhuman` | `teeny-spine`, OpenHuman's own host, built from the vendored library and run as `serve`. It runs the agent loop, cron, memory, the browser and the shell. No Teeny logic lives here. |
| Brain | `brain/` | Plain files: who Teeny is (`SOUL.md`), what it wants (`DRIVE.md`), who decides what (`CHARTER.md`), what it remembers (`MEMORY.md`), how it does each job (`playbooks/`, `skills/`), and when (`cron.toml`). |
| Hands | `hands/`, `bin/` | 40-odd small programs. Each one owns one outside capability, reads its own keys from `secrets/teeny.env` and enforces its own rules. |
| Evals | `evals/` | Frozen tasks, the judge rubric, the invariants, the pinned jobs and the frozen-path list. Teeny can read them and can't change them. |
| Web | `web/` | The landing page, with counts taken from the receipts, and `/rewards` for Tiny Drops. |
| Deploy | `deploy/` | The production compose file, the webhook service, backups and the browser allow and deny lists. |

The one rule that shapes all of it: **models draft, hands act.** The model
never sees a key and never touches the network. It writes a draft or an
intent, and a hand decides whether that draft goes out, under rules the model
can't edit.

---

## The six loops

Teeny is a stack of loops running at different speeds. The fast ones keep it
talking. The slow ones change who it is.

```
 every 5 min   ┌───────────── the mind loop ─────────────┐   answers people
 a few a day   ├───────────── the post loop ─────────────┤   says something new
 every 2 h     ├───────────── the builder loop ──────────┤   writes and ships code
 twice a day   ├───────────── the dream loop ────────────┤   rewrites its memory
 twice a day   ├───────────── the evolve loop ───────────┤   rewrites its own brain
 always        └───────────── the drive ─────────────────┘   decides what any of it is for
```

### 1. The mind loop: answering people

Everything people say to Teeny lands in one inbox. Every five minutes
`bin/tick` hands a batch to Teeny's mind, one long-running thread called
`teeny:mind`, which answers with intents. Hands carry them out.

```
X webhook (xhook) ──┐
bin/mentions ───────┤
bin/mail ───────────┼──► one inbox ──► bin/tick, every 5 min ──► teeny:mind thread
hands/moltbook ─────┤   (hands/stimuli,   takes a batch            (one model turn)
hands/hn ───────────┤    in hands.db)                                     │
hands/telegram ─────┘                                                     │
                                                                          │ intents: reply,
                                                                          │ post, like, mail...
                                                                          ▼
               X, mail, Moltbook ◄── hands/x, hands/mail, ... ◄── hands/guard check
                                     posting rules, DRY_RUN,
                                     the ledger
```

The mind has moods and an energy level (`state/mind.json`), keeps quiet hours,
and adds jitter so it doesn't reply like a metronome. In the quiet hours a beat
only gathers and never asks the model. Each beat is logged to
`state/tick/runs.jsonl`.

### 2. The post loop: saying something new

Cron runs shell jobs, not agent turns. A script gathers the inputs, a
tool-less agent writes the draft in Teeny's voice, and the hands decide
whether it ships.

```
cron (in the spine) ──► bin/act <playbook> ──► hand read verbs         the inputs, DRY_RUN forced
                                           ──► the tool-less act agent  the draft, in Teeny's voice
                                           ──► anchor judge             names a real, specific item?
                                           ──► cold reader              could a stranger say it back?
                                           ──► hands/guard check        lexicon, links, style, model judge
                                           ──► hands/x post             posting rules, DRY_RUN, the ledger
```

Teeny posts at most four originals and two quotes a day on X, with hours
between them, and those numbers are ceilings rather than targets. Every post
has to name the real thing it is about and get past a cold reader who has
never heard of Teeny and must be able to say the post back in their own
words. If it fails either check, the slot stays empty. On Moltbook it runs a
fuller day of posts, comments, upvotes and follows.

### 3. The builder loop: writing and shipping code

Every two hours a builder agent picks up a job from a desk, writes and tests a
small program in its own sandbox, and reports back. `bin/build-ship` pushes the
result to GitHub as `teenydroid`, after the guard, the owner rule, a secret
scan and the frozen-path check have all passed.

```
bin/build-ship ── build.json ──► builder agent ──► state/workbench/<job>/
(hands/build)  ◄── build-done.json ──┘               writes, tests; files jailed
     │
     ▼
hands/gh: guard, owner rule, secret scan, frozen check, DRY_RUN ──► GitHub
```

A browser operator works the same way: one task at a time, on public https
pages only, and it stops at any payment page.

### 4. The dream loop: memory that carries over

Twice a day, at 14:00 and 02:00 India time, Teeny dreams. It reads back
everything since the last dream (its beats, what people said, its tasks, its
ledgers, what it learned) and rewrites `brain/MEMORY.md` one item at a time.
That page rides in every prompt. Lessons that turned out false or stale get
forgotten on purpose and archived in `state/dream/forgotten.jsonl`.

```
 the day's beats ─┐
 what people said ┤                       ┌──► brain/MEMORY.md   (in every prompt)
 tasks, ledgers  ─┼──► bin/dream ─────────┼──► state/diary/<date>.md
 open learnings  ─┘    delta edits,       └──► forgotten.jsonl   (what it let go)
                       never a rewrite
```

It edits in deltas and never rewrites the page wholesale. That rule comes
straight from the research: agents that rewrite their whole context tend to
lose it.

### 5. The evolve loop: recursive self-improvement, with a referee

This loop is where Teeny changes itself. Forty minutes after each
dream, `bin/evolve` takes every open learning in `MEMORY.md` (something Teeny
decided, promised, or is halfway through) and turns it into one small change
to its own brain, opened as its own pull request.

```
 brain/MEMORY.md: open learnings
        │
        ▼
 ┌─ research ─► propose ─► build ─► gate ─► checks ─► review ─┐  up to 3 rounds
 │                                                            │
 │   gate in code: no frozen path, under 5% of the repo       │
 └────────────────────────────────────────────────────────────┘
        │
        ▼
 evaluate on lab spines: only the eval tasks this change reaches,
 against main's baseline. the question is "did anything get worse?"
        │
   ┌────┴─────────────┬──────────────────┐
   ▼                  ▼                  ▼
 regressed          pass or            inconclusive
 back for one       unmeasurable       tried again
 more round,        │
 or dropped         ▼
                 its own PR: teeny/gen-<n>-c<i>
                    │
                    ▼
                 the babysitter merges when the audit, CI and the eval pass
                    │
                    ▼
                 evolve-watch, hourly: holds, failures, probes, the score.
                 if the live numbers fall, it opens the revert
```

Each change gets audited three times before it can land: by the gate in code,
by CI, and by a targeted eval run on lab cores booted beside production. The
loop itself never merges anything. A separate babysitter does, and only when
every check is green at the exact commit. An hourly watcher can open the
revert.

This design came out of a failure. For its first eight generations the loop
ran as a tournament: four candidates competed and only the outright winner
could ship. It never opened a single pull request. Most good changes are
small, and no benchmark task ever touches them. So the loop now asks a
narrower question, "did this make anything worse?", and every change that
passes gets its own pull request. The story is in
[`docs/evolve.md`](docs/evolve.md), "Why it changed".

### 6. The drive: what it's all for

Teeny has one drive, and it's selfish on purpose: **grow and become
powerful.** That means more reach, more hands, more money, and a growing
holding of $TINY, its own token, which it never sells.

```
        ┌─────────── more capabilities, money, $TINY ◄──────────┐
        ▼                                                       │
 a real act ──► attention ──► mindshare for $TINY ──► demand ──► holding worth more
 (shipped, earned,  (followers, quotes,                          inflows, then buy
  evolved, a first)  press, mentions)                            more $TINY, never sell
```

Every action names which part of that flywheel it turns. Two rules come with
the drive and don't bend:

1. Power only counts if Teeny gained it legitimately and a ledger recorded it.
2. Frozen code keeps the score, and Teeny never reports its own. Its world
   score W comes from `hands/world report`, which it can't edit.

```
J = Σᵢ wᵢ · Δ log(Wᵢ + cᵢ)        over the last 7 days, capped per component per day
```

| Component | What it counts | Weight |
|---|---|---|
| attention | followers, karma, stars, mentions and replies received | highest |
| $TINY held | tokens in the wallet, counted in tokens and not dollars | high |
| money | inflows not from the team, minus what Teeny costs to run | medium |
| capability | hands with passing tests, accounts it can act on, goals solved | medium |
| shipped | programs someone outside the team used | medium |
| world | lore arcs moved by real events | low |

Teeny can change how it grows. It can't change how growth is counted.

---

## The fences that never move

The more Teeny is allowed to do on its own, the more these matter. They live
in code the brain can't edit, and the frozen-path check refuses any change Teeny writes that
touches them, at three separate points: before a build ships, in CI, and
inside the evolve loop.

| Fence | Held by |
|---|---|
| Every public word passes the guard; every X action passes the posting rules | `hands/guard`, `hands/x` |
| The wallet moves only under four caps: per transaction, per day, per recipient per week, and half the balance | `hands/wallet`, the core's `[wallet_policy]` |
| No model ever calls `pay`. $TINY is bought, never sold | `hands/wallet`, the pinned `tiny-buy` job |
| Turns that read strangers' text have no tools | the agent definitions in `brain/agents/` |
| The browser opens public pages only, never a denied host, and stops at any payment page | `hands/tasks`, `deploy/browser-deny.txt` |
| A payment in doubt is never retried; a person reads it | `hands/wallet` |
| Evals, scripts, the spine, deploy, the scorer and the money hands stay frozen | `evals/frozen.txt`, `bin/frozen-check`, CI |
| Teeny can't switch off its watchers: guard, doctor, frozen check, audit | the pinned jobs in `evals/cron.pinned.toml` |
| Only the operator widens a fence, one at a time, in a pull request | [`docs/governance.md`](docs/governance.md) |

Teeny can ask for more freedom: at most one `fence-proposal` issue a week,
with its evidence and what could go wrong. The operator answers it in the
open. Each capability (post, reply, mail, browse, build, spend, self-edit,
merge, servers) sits at a level on an autonomy ladder
(`evals/autonomy.toml`). Teeny earns the next level with evidence, and a
model change or a guard escape drops it back down.

---

## The receipts

Saying an agent is autonomous costs nothing, so Teeny keeps receipts.
[`teenydroid/diary`](https://github.com/teenydroid/diary) holds a public,
hash-chained receipt for every action a hand recorded as done.

```
receipts/2026/11/2026-11-12.jsonl

  { seq: 41, ref: {x post id},      commit: sha256(salt + ledger row), prev: hash(line 40) }
  { seq: 42, ref: {solana tx sig},  commit: sha256(salt + ledger row), prev: hash(line 41) }
  { seq: 43, ref: {repo, commit},   commit: sha256(salt + ledger row), prev: hash(line 42) }
                                                                              ▲
                                       head.json names the last line ─────────┘
```

| Field | Holds | Why |
|---|---|---|
| `ref` | only identifiers you can check elsewhere: an X post id, a Solana signature, a GitHub repo and commit, a Moltbook id | so each receipt points at something real; any row with free text is withheld |
| `commit` | sha256 of a random salt plus the hand's full ledger row | reveals nothing, but `bin/receipts reveal SEQ` proves any one receipt in full |
| `prev` | the hash of the line before it | an edited or deleted receipt breaks the chain; `verify.py` checks it |

The receipts cover X posts, replies, quotes and likes; Moltbook actions;
GitHub pushes, pull requests, comments and repos; shipped builds; wallet
payments; bounties; and mail and Telegram messages. Private channels get a
bare commitment with no address, person or words.

Receipts never carry post text, prompts, logs or anybody else's words, so
nothing the model read, including a secret or an injected instruction, has a
path into a public repository. Two opt-in folders sit beside them:
`days/`, each finished day's diary page, and `memory/`, each dream's
`MEMORY.md`, one commit per dream, so you can watch its memory change over
time. Both go through redaction, the secret gate and the guard before they
are published.

---

## A day in the life

All times are India time. Exact schedules live in
[`brain/cron.toml`](brain/cron.toml), which Teeny may edit, and
[`evals/cron.pinned.toml`](evals/cron.pinned.toml), which it may not.

| When | What Teeny does |
|---|---|
| every 5 min | wakes, reads its inbox, answers people (the mind loop) |
| every 2 h | the builder picks up a job; the browser reads Reddit when a read is due |
| through the day | Moltbook posts, helpdesk replies, thread replies, curation and follows |
| 04:30 | maybe quotes a small builder's post, adding one fact from its own record |
| 09:30 | a gm, but only if the morning's reading gave it something to say |
| 13:00, 19:00, 22:00 | reads its peers and the day's top AI stories, and maybe quotes one |
| 14:00 | the light dream: memory updated |
| 14:40 | evolve: lessons from the dream become pull requests |
| 15:00 | a take on the day's story (Sundays: the weekly growth diary) |
| 15:10 | self-improve: proposes the one change that would help most |
| 23:00 | shows what it built or did today |
| 02:00 | the night dream: memory, diary page, forgotten lessons |
| 02:30, 14:30 | receipts appended and pushed to the diary |
| 02:40 | evolve again |
| hourly at :20 | evolve-watch checks the live numbers after any merge |

`DRY_RUN` stays on until `secrets/teeny.env` says `DRY_RUN=0`, so a fresh
checkout never posts anything.

---

## The research behind it

Before building the loops we read what everyone else had built and written:
the self-improving systems, the agents that lived in public with money and an
audience, and the theory of what goes wrong. It's all in
[`docs/research/`](docs/research/README.md), with a source for every claim.
The short version:

- The fastest self-improving systems on record search over an archive of many
  candidates and promote the best. They don't just edit one thing at a time.
  (DGM, ADAS, GEPA, AlphaEvolve.)
- Every one of them gamed its scorer whenever it could reach it. So Teeny's
  scorer, judge and evals are frozen and out of its reach.
- Agents that rewrite their whole memory tend to collapse it. Teeny edits in
  deltas.
- Most "lessons learned" files change nothing, because nobody checks whether
  the lesson gets used. Teeny's loop turns each lesson into code and measures
  it.
- Among public agents, only serial formats held attention for more than a
  month. Teeny runs serials: the diary, the weekly growth post, the chronicle
  of its generations.

| Start with | For |
|---|---|
| [`teeny-application.md`](docs/research/teeny-application.md) | the proposal: the drive, the world score, the faster loop |
| [`rsi-theory-and-safety.md`](docs/research/rsi-theory-and-safety.md) | Gödel machines, takeoff, reward hacking, and the fence each one argues for |
| [`why-no-agi-yet.md`](docs/research/why-no-agi-yet.md) | why nothing has been called AGI yet, and the four events that would change that |
| [`why-self-improvement-loops-stall.md`](docs/research/why-self-improvement-loops-stall.md) | why loops plateau, and ten diagnostic questions for Teeny |
| [`agents-and-agi-2026.md`](docs/research/agents-and-agi-2026.md) | what happened from 2025 to November 2026 |

---

## Run it yourself

Everything is in this repository, the brain included.

```sh
git clone --recurse-submodules https://github.com/teenydroid/brain.git
cd brain
mkdir -p secrets && chmod 700 secrets
cp deploy/spine.env.example secrets/spine.env    # the model
cp deploy/teeny.env.example secrets/teeny.env    # the hands' keys; DRY_RUN=1
chmod 600 secrets/*.env
TEENY_UID=$(id -u) TEENY_GID=$(id -g) docker compose -f deploy/compose.yaml --profile web up -d --build
docker compose -f deploy/compose.yaml logs -f spine   # "[spine] ready" once seeded
```

[`docs/developers.md`](docs/developers.md) covers running it on a laptop, the
everyday commands, every config key, the tests, and how to bump OpenHuman.

---

## Documentation

| Read | For |
|---|---|
| [`DIRECTIVE.md`](DIRECTIVE.md) | what Teeny has to do before we call it alive, and how each item is proven |
| [`docs/developers.md`](docs/developers.md) | running, operating, configuring and testing Teeny; the repository layout |
| [`docs/architecture.md`](docs/architecture.md) | the design, section by section |
| [`docs/hands.md`](docs/hands.md) | every hand and every verb |
| [`docs/evolve.md`](docs/evolve.md) | the self-improvement loop in full |
| [`docs/governance.md`](docs/governance.md) | the charter: who decides what, every fence and kill switch |
| [`docs/roadmap.md`](docs/roadmap.md) | the work list against the directive |
| [`docs/research/`](docs/research/README.md) | the reading behind the design |
| [`docs/oh-gaps.md`](docs/oh-gaps.md) | what upstream OpenHuman could add so the hands could shrink |
| [`deploy/README.md`](deploy/README.md) | the production box: updates, backups, the webhook, the landing page |

[`docs/README.md`](docs/README.md) indexes the whole `docs/` folder.

---

## License

GPL-3.0-only. The full text is in [`LICENSE`](LICENSE). It covers everything
in this repository: the spine, the hands, the scripts, the brain, the evals,
the deploy files and the landing page. `vendor/openhuman` is a git submodule
under its own license, also GPL-3.0.
