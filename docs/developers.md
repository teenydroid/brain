# Developing Teeny

How to run Teeny, operate it, configure it, test it and bump OpenHuman. The
[README](../README.md) says what Teeny is; [`architecture.md`](architecture.md)
says why it is built this way.

Everything here works the same on a laptop and on the box. `DRY_RUN` is on
until `secrets/teeny.env` says `DRY_RUN=0`, so nothing you run below reaches X,
GitHub, mail or the wallet until then.

## Run it

### With Docker (how the box runs it)

```sh
git submodule update --init --recursive vendor/openhuman
mkdir -p secrets && chmod 700 secrets
cp deploy/spine.env.example secrets/spine.env    # the model; CortexDB if wanted
cp deploy/teeny.env.example secrets/teeny.env    # the hands' keys; DRY_RUN=1
chmod 600 secrets/*.env
TEENY_UID=$(id -u) TEENY_GID=$(id -g) docker compose -f deploy/compose.yaml --profile web up -d --build
docker compose -f deploy/compose.yaml logs -f spine      # "[spine] ready" once seeded
```

That starts three services: `spine` (the core), `xhook` (the X webhook on
`127.0.0.1:3110`) and `web` (the landing page on `127.0.0.1:3120`, behind
the `web` profile). Run anything below inside the container with
`docker compose -f deploy/compose.yaml exec spine <command>`.
[`deploy/README.md`](../deploy/README.md) covers the server: updates, backups,
the webhook and the landing page.

### On a laptop

```sh
git submodule update --init --recursive vendor/openhuman
cargo build --release -p teeny-spine
bin/spine          # mints secrets/core.token and secrets/openhuman.key on first boot
```

## Operate it

The `bin/` scripts drive the core and the mind. The `hands/` programs are the
capabilities, each with read verbs you can call by hand.

```sh
bin/crons                          # every cron job, its next run and last result (a sqlite query)
bin/crons runs heartbeat           # one job's recent runs
bin/ask "who are you?"             # one agent turn
bin/oh cron_list                   # any JSON-RPC method on the running core
bin/seed-cron --dry-run            # what seeding brain/cron.toml would change
bin/seed-cron --check              # is brain/cron.toml within the cron rules? (no core needed)
bin/act morning                    # run one playbook now (DRY_RUN still applies)
bin/act --check morning            # does it parse?
bin/tick                           # one beat of the mind: take the inbox, carry out the intents
bin/reflect                        # update MEMORY.md item by item and write today's diary entry
bin/reflect --light                # update MEMORY.md from the day so far, no diary
bin/tick --detach                  # what cron runs: start the beat in the background (log: state/tick/beat.log)
bin/mentions                       # read mentions into the inbox
bin/mail                           # read mail into the inbox
bin/doctor                         # is this checkout safe to boot? (--live: is the spine healthy?)
bin/proofs --text                  # how far each countable DIRECTIVE.md proof has got (read-only)
bin/proofs --ladder --text         # the autonomy ladder: each capability's level, and what the next one needs
bin/autonomy issue                 # open the issue a model change prepared (off unless LADDER_ISSUES=1)
bin/audit --json                   # last week's audit page (holds, spend, halts, generations, operator actions)
bin/eval run state/eval/now        # draft every eval task on the running spine
bin/eval compare A B OUT           # judge two runs; exit 0 when B beats A
bin/eval invariants                # does the brain still say what Teeny is?
bin/evolve status                  # the evolve archive, the parent weights, any halt
bin/evolve run --hold              # one generation, stopped after the gate (off until secrets/evolve.env says EVOLVE=1)
bin/evolve watch                   # evolve-watch: the newest promotion, reverted when it hurts
bin/evolve probe --baseline        # record gen-0's persona and memory answers on the live core
bin/evolve resume                  # clear a latched halt

hands/brief add "talk about the new release"          # queue the next told-to post
hands/brief list                                      # open, posted and cancelled briefs
hands/stimuli counts                                  # the mind's inbox, by status
hands/stimuli recent --max 10                         # the newest events in it
hands/image list                                      # pictures made, and today's spend
hands/video list                                      # clips made

hands/x whoami                                        # who the X keys belong to
hands/x mentions --status pending                     # the mentions queue and what became of each
echo '{"text":"gm"}' | hands/guard check              # ok, or a hold with the reason
echo '{"text":"gm"}' | hands/x post                   # guard, rules, then DRY_RUN or X
hands/guard holds list                                # what the guard is holding for review
hands/guard holds release 12                          # publish a withheld X draft the operator approves

hands/gh whoami                                       # who the GitHub token belongs to
hands/gh contributing owner/repo                      # does this project forbid AI contributions?
hands/gh activity --days 7                            # Teeny's own PRs, merges and commits

hands/moltbook home                                   # notifications and what to do next
hands/moltbook pending                                # challenges left for the operator to answer
hands/moltbook verify 3 --answer 15                   # answer one (within 5 minutes)
hands/moltbook unlock                                 # clear the verification lockout

hands/hn top --max 5                                  # the front page, read only
hands/hn thread 42 --max 30                           # an item and the comments under it
hands/hn watch list                                   # the threads Teeny follows; watch remove ID stops one

hands/mail list --unread                              # recent mail
hands/mail contacts add shop@example.com --kind vendor --note stickers   # who Teeny may mail first

hands/requests list --status pending                  # what people asked Teeny to build
echo '{"id":4,"reason":"small and useful"}' | hands/requests pick   # the one to build next (one at a time)
hands/requests reset 4 --note 'builder crashed'       # back to pending (operator)
hands/build jobs --status ok                          # built and tested, waiting for bin/build-ship
echo '{"goal":"read the latest release notes","start_url":"https://github.com/tinyhumansai/openhuman/releases","done_when":"you can name the newest version","kind":"research"}' | hands/tasks add --source operator
hands/tasks list --status needs_human                 # what the browser left for a person
bin/oh cron_run '{"job_id":"<id of teeny.builder>"}'  # run the builder now (bin/crons lists the ids)

hands/stats snapshot                                  # today's numbers (bin/stats-snapshot, daily)
hands/stats report --days 7                           # latest values and their change
hands/world snapshot                                  # today's world score W (bin/world-snapshot, daily)
hands/world report --days 7                           # W, its growth per component, and J
hands/wallet tiny                                     # Teeny's $TINY, in tokens (read only)

bin/wallet-setup                                      # once, at a terminal: the core makes Teeny's wallet; the phrase is shown once
hands/wallet address                                  # Teeny's Solana address
hands/wallet balance                                  # SOL and USDC
hands/wallet caps                                     # the caps every payment is held to
hands/wallet costs --days 30                          # models, x402 and bills: what staying alive cost
hands/wallet x402 hosts add api.exa.ai                # allow paid web search (once)
DRY_RUN=1 hands/x402 search "openhuman agent" --n 3   # what it would pay for; nothing is paid
hands/x402 fund status                                # USDC on Base, the floor and target, the last top-ups
DRY_RUN=1 hands/x402 fund                             # the top-up it would make, its quote checked
hands/bounty get 1                                    # a bounty's entries, winner and claim
```

Every hand prints one JSON object and exits with one of three codes:

```
0   done
2   a rule refused (the JSON says which)
1   an error
```

Add `--help` to any hand or verb for its flags. [`hands.md`](hands.md) lists
every verb.

## Schedule a job

To schedule something, write a playbook in `brain/playbooks/`, add a job that
runs it to `brain/cron.toml`, and restart the spine:

```toml
[jobs.morning]
schedule = "0 7 * * *"
tz = "Asia/Kolkata"
command = "bin/act morning"
```

A brain job runs `bin/heartbeat` or `bin/act <playbook>`. No job may run more
often than every 5 minutes, there can be at most 24 brain jobs, and `bin/act`
can run at most 400 times a day. The jobs in `evals/cron.pinned.toml` (the
inbound jobs, `tick`, `reflect`, the builder and operator, and the rest) are
always added and do not count toward the 24. These rules live in
`bin/lib/cron_policy.py`.

## Configure it

Everything secret lives in `secrets/`. That folder is gitignored and outside
the brain. Two env files feed two different readers:

```
secrets/spine.env ──► bin/spine, at every boot ──► the core (model endpoint, CortexDB;
                                                    the API key goes into its encrypted store)
secrets/teeny.env ──► each hand, read by the hand itself ──► X, GitHub, mail, wallet, ...
                      (the model never sees it)
secrets/core.token, secrets/openhuman.key ◄── written by the spine on first boot
```

### `secrets/spine.env`

`bin/spine` gives these to the core at every boot. The example is
`deploy/spine.env.example`.

| Key | |
|---|---|
| `LADDER_URL` | Any OpenAI-compatible endpoint, run as OpenHuman's `local-openai` runtime, so no TinyHumans account is needed. |
| `LADDER_API_KEY` | Stored in the core's encrypted store on boot. Can be removed afterwards. |
| `SPINE_MODEL` | The model id every workload uses. |
| `SPINE_CONTEXT_WINDOW` | Optional. Otherwise it's read from the endpoint's `/models`. The core trims prompts to this. |
| `SPINE_NATIVE_TOOLS` | Optional, default `1`: agents get native OpenAI `tools`. `0` falls back to the core's JSON-in-tag text protocol, for a model that cannot take `tools`. On Surplus the text protocol fails: the server reads each `<tool_call>` body as the tool's name. |
| `CORTEX_API_URL`, `CORTEX_API_KEY` | Optional memory engine. |

### `secrets/teeny.env`

Each hand reads this file itself. The example is `deploy/teeny.env.example`.
The process environment can't override it, though `DRY_RUN=1` in the
environment forces a rehearsal.

| Key | |
|---|---|
| `DRY_RUN` | On unless set to `0`. |
| `TEENY_INBOX` | `mind` (the default): mentions and mail go to the one inbox `bin/tick` reads. `core`: the older per-surface path. |
| `X_BOT_USERNAME`, `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`, `X_BEARER_TOKEN` | X API v2: OAuth 1.0a for writes, the bearer for reads. `X_API_SECRET` also signs the webhook. |
| `POST_MIN_GAP_HOURS`, `REPLY_MIN_INTERVAL_MS`, `REPLY_MAX_INTERVAL_MS`, ... | The posting rules. |
| `MAX_EMOJI_PER_POST`, `SAFETY_JUDGE`, `LINK_ALLOWLIST` | The guard. |
| `SURPLUS_API_KEY` | `hands/image` and `hands/video` (Surplus Intelligence). `MEDIA_*` keys set the models and the daily caps. |
| `GITHUB_TOKEN`, `GITHUB_USER` | `hands/gh`: the token for Teeny's GitHub account (`teenydroid` by default). `bin/receipts` falls back to it. |
| `MOLTBOOK_API_KEY` | `hands/moltbook`. |
| `HN_USERNAME` | `hands/hn`: Teeny's Hacker News account, whose replies `hn inbox` files. No key: HN is read only. With it (or a watched thread) `bin/tick` reads HN every 15 minutes. `MIND_HN_WATCH_PER_DAY` (0, off; at most 10) lets the mind follow threads (`watch_hn`). |
| `AGENTMAIL_API_KEY`, `AGENTMAIL_INBOX` | `hands/mail`. |
| `WALLET_*`, `SOLANA_RPC_URL`, `BOUNTY_CHALLENGE_HOURS` | The wallet's cluster and hard caps, and the bounty challenge window. `bin/doctor` refuses wallet keys without all four caps. |
| `X402_FUND`, `X402_FUND_*`, `X402_EXA_MAX_USD`, `X402_FIRECRAWL`, `X402_FIRECRAWL_MAX_USD`, `X402_TEXT_CHARS`, `X402_CACHE` | `hands/x402`: the top-up of USDC on Base (off unless `X402_FUND=1`, then its floor, target and daily cap are required) and paid web search. `bin/doctor` refuses unusable values. [`hands.md`](hands.md#x402-the-x402-rail-kept-funded-and-spent-on-web-search) lists each with its ceiling. |
| `MIND_ISSUES_PER_DAY`, `MIND_TASKS_PER_DAY`, `MIND_MAILS_PER_DAY`, `MIND_SEARCHES_PER_DAY` | How many issues, browser tasks, new mails and paid web searches or page reads (together) the mind may send a UTC day. 0, the default, keeps each off. |
| `LADDER_ISSUES`, `LADDER_ISSUE_REPO` | Off unless `1`: a model change's issue (the fence tests to rerun) opens on `LADDER_ISSUE_REPO` (`teenydroid/brain`). |
| `GUARD_REVIEW`, `GUARD_REVIEW_MODEL` | Off unless `1`: the hold reviewer (`hands/guard review`) may release held drafts in the categories `evals/guard-review.toml` allows. The model must not be the drafter's or the judge's. |
| `AUDIT_PUBLISH` | Off unless `1`: the weekly audit (`bin/audit`) also goes out beside the receipts. It is always written to `state/audit/`. |

### `deploy/browser-allow.txt` and `deploy/browser-deny.txt`

Two operator-owned lists, one host per line, subdomains included, outside the
brain Teeny edits. `browser-allow.txt` is what OpenHuman's raw `http_request`
tool may reach; `bin/spine` writes it into the core's config at every boot.
The browser opens any public https page (`bin/spine` sets
`OPENHUMAN_BROWSER_ALLOW_ALL=1`) except a host on `browser-deny.txt`:
`hands/tasks add` refuses a task that starts there, and the operator agent is
told never to open one. The doctor fails a boot without the deny list.

### The core's config

At every boot `bin/spine` also writes these tables into the core's config
(`bin/lib/core_config.py` runs each module in `bin/lib/core_sections/`, and
each table is replaced whole): `[cron]` (a shell job may run 30 minutes),
`[autonomy]`, `[browser]`, `[http_request]`, `[modules]` (TinyComputer and
tinywallet from the spine's own executable), `[context]` (when a thread
folds) and `[secrets]` (config secrets unencrypted, since a headless core has
nobody to approve unlocking them). `bin/lib/wallet_config.py` writes
`[wallet_policy]`.

The spine writes these itself on first boot: `secrets/core.token` (the RPC
bearer) and `secrets/openhuman.key` (the master key for every secret the core
stores). **Back up `brain/storage.db` and `secrets/openhuman.key` together.**
The database is unreadable without its key.

## Layout

```
                 ┌──────────────── frozen: Teeny cannot edit (evals/frozen.txt) ───────────────┐
                 │ spine/  vendor/  bin/  deploy/  evals/  Cargo.*  DIRECTIVE.md               │
                 │ hands/guard  hands/x  the existing hand library  brain/agents/*.toml        │
                 └─────────────────────────────────────────────────────────────────────────────┘
                 ┌──────────────── Teeny's own: it changes these in PRs and generations ───────┐
                 │ brain/ (SOUL, CONTEXT, playbooks, skills, narrative, agent prompts)         │
                 │ new hands it writes under hands/lib/teeny_hands/<name>/                     │
                 └─────────────────────────────────────────────────────────────────────────────┘
```

[`evolve.md`](evolve.md) covers what Teeny may change and how the check works.

```
spine/               teeny-spine, the one Rust package (OpenHuman's host, built from the library)
vendor/openhuman     upstream tinyhumansai/openhuman, pinned by commit
bin/                 spine, doctor, act, tick, reflect, mentions, mail, x-webhook, build-ship, tasks-desk, bounty-pay, health, proofs, eval, evolve, frozen-check, ask, oh, crons, seed-cron, ...; bin/lib/ shared code; bin/test/
hands/               x, guard, gh, moltbook, hn, mail, stimuli, brief, image, video, stats, requests, build, tasks, wallet, bounty, slopcash: Python 3.11 stdlib; hands/lib/teeny_hands, hands/tests/
brain/               SOUL.md, CONTEXT.md, MEMORY.seed.md (MEMORY.md, the live page, is gitignored), skills/, playbooks/, agents/, narrative/, assets/, calendar.json, cron.toml
evals/               frozen: what Teeny cannot edit about itself (tasks/, judge.md, invariants.md, cron.pinned.toml, frozen.txt: every frozen path, checked by bin/frozen-check)
deploy/              compose.yaml, spine.env.example, teeny.env.example, browser-allow.txt, browser-deny.txt, the nginx vhost
web/                 the landing page (Next.js), its own image
docs/                architecture, hands, evolve, upstream gaps
```

These are gitignored and created at run time:
- `secrets/`: the env files (`evolve.env` among them), the core token, the
  master key, the held-out eval tasks (`heldout/`), and `evolve-watch/`, the
  watch's reasons, which nothing Teeny reads ever sees.
- `state/`: what the hands keep. `hands.db` holds the ledgers, caches, holds,
  pacing, the inbox and the queues. Beside it: `locks/`, `core.url` (where the
  core answers), `tick/` and `reflect/` (the mind's runs), `journal/`, `diary/`,
  `workbench/` (what the builder builds; `workbench/brain/` is Teeny's own
  clone of this repository, with its git directory in `self/`), `browser/`
  (the browser's profile and downloads), `media/` (what a post may attach) and
  `evolve/` (each generation of `bin/evolve` with its lab spines' folders,
  `archive.jsonl`, and `probes/gen-0.json`). `spine.pid` is the running
  spine's pid, `last-good` the commit a promotion restarted from, and
  `spine-fallback.json` is there only after a last-good boot. `boots.jsonl`
  is one line per spine boot, and `audit/` the weekly audit pages (`bin/audit`).
- `.openhuman/`: the core's config, written by the core.
- `brain/storage.db`: all of the core's state (sessions, cron jobs and runs,
  encrypted secrets) in one SQLite file, so `sqlite3` can read it.
- `brain/agent/prompts/`: each agent's prompt, built at boot.
- `brain/IDENTITY.md`, `ROLE.md`, `STYLE.md`: OpenHuman's default persona
  files. It never overwrites Teeny's `SOUL.md`.

## Tests

```sh
python3 -m unittest discover -s hands/tests -t hands/tests   # the hands, tick, reflect, the core config and bin/evolve (lab spines, promotion, watch, probes); X, GitHub and the core are faked
bin/test/seed_cron_test.sh                                   # cron reconciliation and rules; no core needed
bin/test/act_test.sh                                         # playbooks; the core and hands faked
bin/test/doctor_test.sh                                      # each thing the doctor catches; spine refuses to boot
bin/test/eval_test.sh                                        # hard checks, blind swapped judging, scoring, held-out tasks; the core faked
bin/test/evolve_test.sh                                      # bin/evolve end to end: dry run, a lab-core evaluation, halts, evolve-watch; core, hands and GitHub faked
bin/test/inbound_consume_test.sh                             # drafts to replies, batch-only, pacing; the core and hands faked
bin/test/dockerfile_test.sh                                  # the image copies every module script it imports
bin/test/proofs_test.sh                                      # bin/proofs over fixture ledgers; read-only, no core
OPENHUMAN_CORE_BIN=target/release/teeny-spine bin/test/spine_live_test.sh
```

The live test boots a real spine over a scratch copy of the checkout, against
a stub model. It covers first boot, cron seeding, `bin/ask`, `bin/act`,
`bin/eval`, `bin/tick`, `bin/reflect`, the builder and operator agent jobs,
the doctor, and the key surviving a restart.

CI (`.github/workflows/check.yml`) builds the spine and runs every test above,
plus shellcheck and the landing page's lint and build.

## Bumping OpenHuman

1. Move `vendor/openhuman` to the new `upstream/main` commit and run
   `git submodule update --init --recursive vendor/openhuman`.
2. Re-copy its root `[patch]` tables into `Cargo.toml`, with paths rebased
   onto `vendor/openhuman/`. Drop any entry that `cargo metadata` warns "was
   not used in the crate graph". Cargo records those in `Cargo.lock` in an
   order that differs between macOS and Linux, which breaks the image's
   `--locked` build.
3. Run `cargo build --release -p teeny-spine`.
4. Run the live test against the new binary.
