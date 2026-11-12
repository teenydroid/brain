# teeny

Teeny ([`@teenydroid`](https://x.com/teenydroid)) is an AI agent run by
TinyHumans. It is a stock [OpenHuman](https://github.com/tinyhumansai/openhuman)
core plus a brain and a handful of small programs. There is no custom runtime:
OpenHuman runs the agent loop, cron, memory and tools. Teeny is the files it
reads and the programs it calls.

This repository is [`teenydroid/brain`](https://github.com/teenydroid/brain).
Teeny's public receipts are in [`teenydroid/diary`](https://github.com/teenydroid/diary):
a hash-chained ledger of what it did, with no logs and no text (see [The receipts](#the-receipts)).

| | What | Where |
|---|---|---|
| **Spine** | `teeny-spine`, OpenHuman's own command-line host, built here from the vendored library and run as `serve`. It provides the agent loop, model routing, cron, memory (CortexDB), browser, shell and JSON-RPC. No Teeny logic lives in it. | `spine/`, `vendor/openhuman` |
| **Hands** | Small programs that hold every outside capability, every secret and every hard rule. Models only draft; hands act. | `hands/`, `bin/` |
| **Brain** | Who Teeny is and what it does: identity, known facts, memory page, skills, playbooks, narrative, assets and the schedule. All of OpenHuman's state sits beside it in one SQLite file. | `brain/` |

[`docs/architecture.md`](docs/architecture.md) is the design.
[`docs/hands.md`](docs/hands.md) specifies every hand.
[`docs/evolve.md`](docs/evolve.md) is the self-improvement loop (partly built).
[`docs/oh-gaps.md`](docs/oh-gaps.md) lists what upstream OpenHuman could add to
make the hands smaller.

## What Teeny does

- **Posts on X twice a day.** The morning gm (`morning`, 07:00 IST), and one
  post about whatever the team queued with `hands/brief add` (`brief`). Both
  may carry a picture or a clip of Teeny made by `hands/image` and
  `hands/video`. Every other post playbook is in `brain/cron.toml` but off.
- **Answers what is said to it.** X pushes mentions, replies and quotes to a
  webhook (`bin/x-webhook`, the `xhook` service). Mail comes from its
  AgentMail inbox, and comments from Moltbook. All of it lands in one inbox,
  and every 5 minutes `bin/tick` hands a batch to Teeny's mind, which answers
  with intents that the hands carry out
  ([`docs/architecture.md`](docs/architecture.md) §4a). X search is not used:
  it is billed per post and too expensive.
- **Remembers.** Every beat that thinks (every awake one) is journaled to
  `state/journal/<date>.md` and the core's memory. At 14:00 `bin/reflect
  --light` rewrites `brain/MEMORY.md`, the page in every prompt, from the
  day so far; each night `bin/reflect` rewrites it again and writes a diary
  entry to `state/diary/<date>.md`.
- **Builds and browses.** The builder agent writes small programs in
  `state/workbench/` and `bin/build-ship` pushes them to GitHub as
  `teenydroid`. The browser operator does one task at a time on the allowed
  websites.
- **Has a wallet.** A Solana wallet held by the core, with hard caps, for
  bounty prizes and vendors. The playbooks that use it are off.

`DRY_RUN` is on until `secrets/teeny.env` says `DRY_RUN=0`.

## How a post happens

```
cron (in the spine) ──► bin/act <playbook> ──► hand read verbs        the inputs, DRY_RUN forced
                                           ──► the tool-less act agent  the draft, in Teeny's voice
                                           ──► hands/guard check        lexicon, links, style, model judge
                                           ──► hands/x post             posting rules, DRY_RUN, X, the ledger
```

Cron runs **shell** jobs, not agent turns. OpenHuman lets a turn started by
cron use only read-only tools ("a scheduled prompt is data"), so a script
drives the job and the model only drafts. The model never sees a key; the
hands read them from `secrets/teeny.env` themselves.

The two exceptions are agents built to act on a schedule, the **builder** and
the **browser operator**. Each sets `scheduled_actions = true` and names its
tools, so OpenHuman runs its cron job as a `CronActions` turn
([`docs/architecture.md`](docs/architecture.md) §6a):

```
cron ──► bin/build-ship ──► hands/build desk    collect the builder's report; lay out its next job in state/workbench/desk/build.json
                        ──► hands/build ship    push finished jobs through hands/gh: guard, owner rule, secret scan, DRY_RUN
cron ──► builder agent  ──► file_read           its job: a picked build request, or one small daily thing
                        ──► shell, file tools   write and test it in state/workbench/<job>/ (sandboxed, no network)
                        ──► file_write          its report: state/workbench/desk/build-done.json

cron ──► bin/tasks-desk ──► hands/tasks desk    collect the operator's report; lay out its next task
cron ──► operator agent ──► file_read           its task: state/workbench/desk/task.json
                        ──► browser             on deploy/browser-allow.txt only; a payment stops it
                        ──► file_write          ok, failed or needs_human, and its screenshots
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

`bounty-open`, `bounty-paid` and `cost-of-living-moltbook` are not scheduled.
`mentions` and `mail` are the prompts the core's per-surface agents use when
`TEENY_INBOX=core`; by default mentions and mail go to the mind instead.

## The receipts

`teenydroid/diary` holds proof of what Teeny did, never what it read or
thought. Every action a hand recorded as done becomes one line of
`receipts/YYYY/MM/YYYY-MM-DD.jsonl`, and `head.json` names the chain's last
line. Each line holds:

- `ref`: only identifiers anyone can check elsewhere, such as an X post id, a
  Solana transaction signature, a GitHub repository and commit, or a Moltbook
  post id. Every field is an enum, an integer or matches a strict pattern. A
  row with anything else is withheld.

Every surface Teeny acts on is covered, each from the ledger its hand writes
once the action went out: X posts, replies, quotes and likes (`x_likes`);
Moltbook posts, comments, upvotes, follows and subscriptions
(`moltbook_actions`); GitHub pushes, pull requests, comments, repositories and
forks; shipped builds; wallet payments; bounties; and mail and Telegram
messages. Mail and Telegram are private, so their receipts are a commitment
alone, with no address, chat, person, message id or words. The mind loop's own
log (`state/tick/runs.jsonl`) is never read: it records what the mind meant to
do, dry runs and refusals included, and reading it too would count an action
twice.
- `commit`: sha256 of a random salt plus the hand's whole ledger row. It
  reveals nothing, but `bin/receipts reveal SEQ` lets an operator prove that
  one receipt in full.
- `prev`: the hash of the line before it, so an edited or deleted receipt
  breaks the chain. `verify.py` in that repository checks the chain.

No post text, titles, summaries, diary prose, prompts, logs or anybody else's
words are written. That way nothing the model read, whether a secret or an
injected instruction to repeat something, has a path into a public
repository. The diary entry from `bin/reflect` stays private in
`state/diary/`.

`bin/receipts` (with `bin/lib/receipts.py`, run daily by `[jobs.receipts]`)
appends and pushes. It refuses to build on a published chain that does not
verify. It needs `RECEIPTS_GITHUB_TOKEN` (or `DIARY_GITHUB_TOKEN`, or
`GITHUB_TOKEN`) in `secrets/teeny.env` with push to `teenydroid/diary`, and it
is a dry run by default.

## License

GPL-3.0-only. The full text is in [`LICENSE`](LICENSE). It covers everything in
this repository: the spine (`spine/`; the workspace `Cargo.toml` declares
`GPL-3.0-only`), the hands (`hands/`), the scripts (`bin/`), the brain
(`brain/`), the evals (`evals/`), the deploy files (`deploy/`) and the landing
page (`web/`, `package.json` says the same). `vendor/openhuman` is a git
submodule with its own license, also GPL-3.0.
