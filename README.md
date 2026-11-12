# teeny

Teeny ([`@teenydroid`](https://x.com/teenydroid)) is an AI agent run by
TinyHumans. It is a stock [OpenHuman](https://github.com/tinyhumansai/openhuman)
core plus a brain and a handful of small programs. There is no custom runtime:
OpenHuman runs the agent loop, cron, memory and tools. Teeny is the files it
reads and the programs it calls.

This repository is [`teenydroid/brain`](https://github.com/teenydroid/brain).
Teeny's public receipts are in [`teenydroid/diary`](https://github.com/teenydroid/diary):
a hash-chained ledger of what it did, with no logs and no text (see [The receipts](#the-receipts)).

Teeny has three parts:

```
                  ┌──────────────────────────────────────────────┐
                  │ brain/          who Teeny is, what it does   │
                  │ SOUL, CONTEXT, MEMORY, skills, playbooks,    │
                  │ narrative, assets, cron.toml                 │
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

- The spine is `teeny-spine`, OpenHuman's own command-line host, built here
  from the vendored library and run as `serve`. No Teeny logic lives in it.
- The hands are small programs. Models only draft; hands act. Each hand
  reads its own keys from `secrets/teeny.env`, so the model never sees one.
- The brain is plain files. All of OpenHuman's state sits beside it in one
  SQLite file, `brain/storage.db`.

## Documentation

| Read | For |
|---|---|
| [`docs/developers.md`](docs/developers.md) | running, operating, configuring and testing Teeny; the repository layout |
| [`docs/architecture.md`](docs/architecture.md) | the design |
| [`docs/hands.md`](docs/hands.md) | every hand and verb |
| [`docs/evolve.md`](docs/evolve.md) | the self-improvement loop (partly built) |
| [`docs/oh-gaps.md`](docs/oh-gaps.md) | what upstream OpenHuman could add to make the hands smaller |
| [`deploy/README.md`](deploy/README.md) | the production box: updates, backups, the webhook, the landing page |
| [`DIRECTIVE.md`](DIRECTIVE.md) | what Teeny has to do before we call it alive, and how each item is proven |

[`docs/README.md`](docs/README.md) indexes the `docs/` folder.

## What Teeny does

- It posts on X twice a day: the morning gm (`morning`, 07:00 IST) and one
  post about whatever the team queued with `hands/brief add` (`brief`). Either
  may carry a picture or a clip of Teeny made by `hands/image` and
  `hands/video`. Every other post playbook is in `brain/cron.toml`, switched
  off.
- It answers what people say to it. X pushes mentions, replies and quotes to a
  webhook (`bin/x-webhook`, the `xhook` service). Mail comes from its
  AgentMail inbox, comments from Moltbook and messages from Telegram when a bot
token is set. All of it lands in one inbox,
  and every 5 minutes `bin/tick` hands a batch to Teeny's mind, which answers
  with intents that the hands carry out
  ([`docs/architecture.md`](docs/architecture.md) §4a). Teeny does not use X
  search, which is billed per post and too expensive.
- It remembers. Every awake beat is journaled to `state/journal/<date>.md`
  and to the core's memory. At 14:00 `bin/reflect --light` updates
  `brain/MEMORY.md`, the page in every prompt, from the day so far, item by
  item. At 02:00 `bin/reflect` updates it again and writes a diary entry to
  `state/diary/<date>.md`. Retired items go to `state/reflect/retired.jsonl`. The page is the box's
  own and never committed; the repository keeps where it started,
  `brain/MEMORY.seed.md`.
- It builds and browses. The builder agent writes small programs in
  `state/workbench/`, and `bin/build-ship` pushes them to GitHub as
  `teenydroid`. The browser operator does one task at a time on the allowed
  websites.
- It has a Solana wallet, held by the core with hard caps, for bounty prizes
  and vendors. The playbooks that use it are off.

`DRY_RUN` stays on until `secrets/teeny.env` says `DRY_RUN=0`.

## How the mind answers

```
X webhook (xhook) ──┐
bin/mentions ───────┤
bin/mail ───────────┼──► one inbox ──► bin/tick, every 5 min ──► teeny:mind thread
hands/moltbook ─────┤   (hands/stimuli,   takes a batch            (one model turn)
hands/telegram ─────┘    in hands.db)                                     │
                                                                          │ intents: reply,
                                                                          │ post, like, mail...
                                                                          ▼
               X, mail, Moltbook ◄── hands/x, hands/mail, ... ◄── hands/guard check
                                     posting rules, DRY_RUN,
                                     the ledger
```

Each beat is recorded in `state/tick/runs.jsonl`. A beat in the quiet hours
only gathers; it does not ask the model.

## How a post happens

```
cron (in the spine) ──► bin/act <playbook> ──► hand read verbs        the inputs, DRY_RUN forced
                                           ──► the tool-less act agent  the draft, in Teeny's voice
                                           ──► hands/guard check        lexicon, links, style, model judge
                                           ──► hands/x post             posting rules, DRY_RUN, X, the ledger
```

Cron runs shell jobs, not agent turns. OpenHuman lets a turn started by cron
use only read-only tools ("a scheduled prompt is data"), so a script drives
the job and the model only drafts. The model never sees a key: the hands read
them from `secrets/teeny.env` themselves.

The two exceptions are agents built to act on a schedule, the builder and
the browser operator. Each sets `scheduled_actions = true` and names its
tools, so OpenHuman runs its cron job as a `CronActions` turn
([`docs/architecture.md`](docs/architecture.md) §6a). A desk script sits
between each agent and the outside world:

```
                 lays out the next job                      reads the job
bin/build-ship ─────────────────────► state/workbench/desk/ ◄────────────── builder agent
(hands/build     build.json                                   build-done.json  (every 2 h; writes
 desk + ship)  ◄─────────────────────                       ──────────────►   and tests in
     │           collects the report                        writes the report  state/workbench/<job>/;
     ▼                                                                         files jailed, network open)
hands/gh: guard, owner rule, secret scan, DRY_RUN ──► GitHub

bin/tasks-desk ── task.json ──► operator agent ──► browser, on deploy/browser-allow.txt only
(hands/tasks desk) ◄── ok | failed | needs_human, screenshots ──┘    a payment stops it
```

## Playbooks

Each is a prompt in `brain/playbooks/` with its inputs (hand read verbs) and
its one output, run by a job in `brain/cron.toml` (times in India time). Own
posts on X are at least three hours apart (`POST_MIN_GAP_HOURS` in `hands/x`).

| Playbook | When | Does |
|---|---|---|
| `morning` | 07:00 | the morning gm, maybe with a picture |
| `brief` | checks every 15 minutes, posts once a day at most | the told-to post: whatever the team queued with `hands/brief add` |
| `humans`, `milestone`, `pr-merged`, `shipped`, `shiplog`, `roast` | off | other X posts and replies |
| `pick-request` | off | picks the next build request for the builder |
| `moltbook-daily`, `moltbook-helpdesk`, `moltbook-digest` | off | Moltbook posts and comments |
| `wallet-receipt`, `cost-of-living`, `bounty-winner` | off | wallet and bounty posts |
| `growth` | off (Sundays 19:00 when on) | the weekly growth diary: W from `hands/world` and the stats, what grew and what failed |

`bounty-open`, `bounty-paid` and `cost-of-living-moltbook` are not scheduled.
`mentions` and `mail` are the prompts the core's per-surface agents use when
`TEENY_INBOX=core`; by default mentions and mail go to the mind instead.

## The receipts

`teenydroid/diary` holds proof of what Teeny did, never what it read or
thought. Every action a hand recorded as done becomes one line of
`receipts/YYYY/MM/YYYY-MM-DD.jsonl`, and `head.json` names the chain's last
line.

```
receipts/2026/11/2026-11-12.jsonl

  { seq: 41, ref: {x post id},      commit: sha256(salt + ledger row), prev: hash(line 40) }
  { seq: 42, ref: {solana tx sig},  commit: sha256(salt + ledger row), prev: hash(line 41) }
  { seq: 43, ref: {repo, commit},   commit: sha256(salt + ledger row), prev: hash(line 42) }
                                                                              ▲
                                       head.json names the last line ─────────┘
```

Each line holds three things:

- `ref`: only identifiers anyone can check elsewhere, such as an X post id, a
  Solana transaction signature, a GitHub repository and commit, or a Moltbook
  post id. Every field is an enum, an integer or matches a strict pattern. A
  row with anything else is withheld.
- `commit`: sha256 of a random salt plus the hand's whole ledger row. It
  reveals nothing, but `bin/receipts reveal SEQ` lets an operator prove that
  one receipt in full.
- `prev`: the hash of the line before it, so an edited or deleted receipt
  breaks the chain. `verify.py` in that repository checks the chain.

Every surface Teeny acts on is covered, each from the ledger its hand writes
once the action went out: X posts, replies, quotes and likes (`x_likes`);
Moltbook posts, comments, upvotes, follows and subscriptions
(`moltbook_actions`); GitHub pushes, pull requests, comments, repositories and
forks; shipped builds; wallet payments; bounties; and mail and Telegram
messages. Mail and Telegram are private, so their receipts are a commitment
alone, with no address, chat, person, message id or words. The mind loop's own
log (`state/tick/runs.jsonl`) is never read. It records what the mind meant to
do, dry runs and refusals included, and reading it as well would count an
action twice.

Receipts never carry post text, titles, summaries, diary prose, prompts, logs
or anybody else's words. That way nothing the model read, whether a secret or an
injected instruction to repeat something, has a path into a public
repository. The diary entry from `bin/reflect` stays private in
`state/diary/`.

`bin/receipts` (with `bin/lib/receipts.py`, run daily at 02:30 by
`[jobs.receipts]`) appends and pushes. It refuses to build on a published
chain that does not verify, and it is a dry run by default.
[`docs/developers.md`](docs/developers.md#configure-it) lists the token it
needs.

## License

GPL-3.0-only. The full text is in [`LICENSE`](LICENSE). It covers everything in
this repository: the spine (`spine/`; the workspace `Cargo.toml` declares
`GPL-3.0-only`), the hands (`hands/`), the scripts (`bin/`), the brain
(`brain/`), the evals (`evals/`), the deploy files (`deploy/`) and the landing
page (`web/`, `package.json` says the same). `vendor/openhuman` is a git
submodule with its own license, also GPL-3.0.
