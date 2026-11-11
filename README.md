# teeny

Teeny ([`@teenydroid`](https://x.com/teenydroid)) is the TinyHumans mascot. It is a stock
[OpenHuman](https://github.com/tinyhumansai/openhuman) core plus a brain and a
handful of small programs. There is no custom runtime: OpenHuman runs the
agent loop, cron, memory and tools, and Teeny is the files it reads and the
programs it calls.

| | What | Where |
|---|---|---|
| **Spine** | `teeny-spine`, OpenHuman's own command-line host, built here from the vendored library and run as `serve`. It provides the agent loop, model routing, cron, memory (CortexDB), browser, shell and JSON-RPC. No Teeny logic lives in it. | `spine/`, `vendor/openhuman` |
| **Hands** | Small programs that hold every outside capability, every secret and every hard rule. Models only draft; hands act. | `hands/`, `bin/` |
| **Brain** | Who Teeny is and what it does: identity, known facts, skills, narrative, assets and the schedule. All of OpenHuman's state sits beside it in one SQLite file. | `brain/` |

[`docs/architecture.md`](docs/architecture.md) is the design.
[`docs/hands.md`](docs/hands.md) specifies every hand, and
[`docs/oh-gaps.md`](docs/oh-gaps.md) lists what upstream OpenHuman could add to
make the hands smaller.

## How a post happens

```
cron (in the spine) ──► bin/<job> ──► bin/ask  "draft a post"      the model drafts, in Teeny's voice
                                 ──► hands/guard check              lexicon, links, style, model judge
                                 ──► hands/x post                   posting rules, DRY_RUN, X, the ledger
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

## Status

- **Spine:** done. It builds, boots from `brain/` (in Docker or on a laptop),
  points itself at the model and CortexDB, and seeds cron from
  `brain/cron.toml`.
- **Hands:** `hands/guard` (the content filter, with per-surface limits for
  X, GitHub, Moltbook and mail), `hands/x` (reads, post, reply, like, upload,
  unsay) and `hands/gh` (GitHub as `teenydroid`: reads, create-repo, push,
  open-pr, comment, fork) are built and tested.
- **First live run:** the spine booted, the heartbeat ran from cron,
  `bin/ask` drafted a post in Teeny's voice, `hands/guard` passed it, and
  `hands/x post` returned `dry_run: true`.
- **Playbooks:** `bin/act` runs a playbook from `brain/playbooks/` (inputs from
  hand read verbs, a draft from the tool-less `act` agent, the action through
  a hand). The scheduled ones are listed under [Playbooks](#playbooks); the
  `mentions` playbook is scheduled but switched off.
- **Build requests:** asks to build something are noticed in mentions
  (`hands/x requests`, run by `bin/mentions` after each ingest) and in mail,
  and kept in one list. `hands/requests` lists them, and the daily
  `pick-request` playbook picks one at a time for the builder.
- **Mentions:** `bin/mentions` (the pinned `mentions-inbound` job, every 5
  minutes) reads Teeny's mentions timeline with `hands/x ingest` and offers
  each one to OpenHuman's inbound event queue. The core debounces them into
  one turn of the tool-less `teeny-mentions` agent per batch, and
  `bin/inbound-consume` sends the drafts through `hands/x reply`. The
  `inbound_offer` RPC it uses is an upstream OpenHuman change that has not
  merged yet (see `vendor/openhuman`).
- **Moltbook, mail and stats:** `hands/moltbook` (reads, post, comment,
  upvote, follow, subscribe, with Moltbook's limits kept locally and its
  verification challenge answered only when the parser is sure),
  `hands/mail` (Teeny's AgentMail inbox: reads, verification codes, intake
  through `bin/mail`, the pinned `mail-inbound` job, and replies within a
  thread; new mail only to contacts) and `hands/stats` (daily numbers from X,
  Moltbook and GitHub, the pinned `stats-snapshot` job) are built and tested.
- **Builder and browser operator:** `hands/build` (the builder's queue: claim,
  finish, ship through `hands/gh`), `hands/tasks` (the operator's queue) and
  their desks (a job or task laid out as a file, the agent's report collected
  from beside it) are built and tested. The pinned `builder` (every 2 hours),
  `build-ship` (every 30 minutes), `operator` (hourly) and `tasks-desk` (every
  15 minutes) jobs run them. The spine links
  TinyComputer (the browser) and tinywallet (`web3`) into its executable, and
  the image carries Chromium.
- **Doctor:** `bin/doctor` checks the brain, the crons, the playbooks, the
  invariants, the guard, the storage and the secrets. `bin/spine` won't boot a
  checkout it fails.
- **Evals:** `bin/eval` drafts the frozen tasks in `evals/tasks/` and has the
  tool-less `judge` agent compare two brains pair by pair, blind and twice
  with the order swapped, by the incumbent's soul. The anchors in
  `evals/invariants.md` (never sell $TINY, no buy calls, one token, zero
  toxicity, and more) must hold in every brain.
- **Next:** the self-improvement loop ([`docs/evolve.md`](docs/evolve.md)).
  Teeny edits its own brain, crons included, driven by its lore. A model judges
  each change against the current brain, and a change that wins is merged.
- **`DRY_RUN` is on** until `secrets/teeny.env` says `DRY_RUN=0`.

## Run it

### With Docker (how the box runs it)

```sh
git submodule update --init --recursive vendor/openhuman
mkdir -p secrets && chmod 700 secrets
cp deploy/spine.env.example secrets/spine.env    # the model; CortexDB if wanted
cp deploy/teeny.env.example secrets/teeny.env    # the X keys; DRY_RUN=1
chmod 600 secrets/*.env
TEENY_UID=$(id -u) TEENY_GID=$(id -g) docker compose -f deploy/compose.yaml up -d --build
docker compose -f deploy/compose.yaml logs -f spine      # "[spine] ready" once seeded
```

Run anything below inside the container with
`docker compose -f deploy/compose.yaml exec spine <command>`.
[`deploy/README.md`](deploy/README.md) covers the server: updates, backups and
the landing page.

### On a laptop

```sh
git submodule update --init --recursive vendor/openhuman
cargo build --release -p teeny-spine
bin/spine          # mints secrets/core.token and secrets/openhuman.key on first boot
```

## Operate it

```sh
bin/crons                          # every cron job, its next run and last result (a sqlite query)
bin/crons runs heartbeat           # one job's recent runs
bin/ask "who are you?"             # one agent turn, as Teeny
bin/oh cron_list                   # any JSON-RPC method on the running core
bin/seed-cron --dry-run            # what seeding brain/cron.toml would change
bin/seed-cron --check              # is brain/cron.toml within the cron rules? (no core needed)
bin/act morning                    # run one playbook now (DRY_RUN still applies)
bin/act --check morning            # does it parse?
bin/mentions                       # read mentions, offer them, answer what was drafted
bin/inbound-consume                # only answer what the core has drafted
bin/mail                           # read mail, offer it, answer what was drafted
bin/doctor                         # is this checkout safe to boot? (--live: is the spine healthy?)
bin/eval run state/eval/now        # draft every eval task on the running spine
bin/eval compare A B OUT           # judge two runs; exit 0 when B beats A
bin/eval invariants                # does the brain still say what Teeny is?

hands/x whoami                                        # who the X keys belong to
hands/x search '@teenydroid'                          # a recent search, cached
hands/x ingest                                        # new mentions into the queue, offered to the core
hands/x mentions --status drafted                     # the mentions queue and what became of each
echo '{"text":"gm"}' | hands/guard check              # ok, or a hold with the reason
echo '{"text":"gm"}' | hands/x post                   # guard, rules, then DRY_RUN or X
hands/guard holds list                                # what the guard is holding for review
hands/guard holds release 12                          # publish a withheld X draft the operator approves
echo '{"title":"fix","body":"a typo","surface":"github"}' | hands/guard check --fields title,body

hands/gh whoami                                       # who the GitHub token belongs to
hands/gh contributing owner/repo                      # does this project forbid AI contributions?
hands/gh issues owner/repo --label 'good first issue' # open issues, cached
hands/gh activity --days 7                            # Teeny's own PRs, merges and commits
echo '{"workdir":"state/workbench/hello","repo":"teenydroid/hello","branch":"main","message":"first"}' | hands/gh push
echo '{"repo":"owner/repo","head":"fix","base":"main","title":"fix a typo","body":"one letter"}' | hands/gh open-pr

hands/moltbook home                                   # notifications and what to do next
hands/moltbook feed --submolt general --sort new      # a submolt's newest posts, cached
echo '{"submolt":"general","title":"hello","body":"first post"}' | hands/moltbook post
echo '{"post_id":"ID","body":"nice work"}' | hands/moltbook comment
hands/moltbook pending                                # challenges left for the operator to answer
hands/moltbook verify 3 --answer 15                   # answer one (within 5 minutes)
hands/moltbook collab add crabby --note 'agreed in m/general'   # consent before a collab post
hands/moltbook unlock                                 # clear the verification lockout

hands/mail list --unread                              # recent mail
hands/mail codes --since-minutes 15 --from-domain moltbook.com  # verification codes and links
hands/mail contacts add shop@example.com --kind vendor --note stickers   # who Teeny may mail first
echo '{"message_id":"<id>","body":"thanks!"}' | hands/mail reply
hands/mail requests                                   # build requests noticed in mail
bin/mail                                              # read mail, offer it, send what was drafted

hands/x requests                                      # build requests in the stored mentions (bin/mentions runs it)
hands/requests list --status pending                  # what people asked Teeny to build
hands/requests get 4                                  # one request and its ledger
echo '{"id":4,"reason":"small and useful"}' | hands/requests pick   # the one to build next (one at a time)
echo '{"id":5,"reason":"needs a password"}' | hands/requests skip
hands/requests reset 4 --note 'builder crashed'       # back to pending (operator)
hands/build jobs                                      # the builder's jobs: claimed, ok, failed, shipped
hands/build jobs --status ok                          # built and tested, waiting for bin/build-ship
hands/build ship                                      # push them now (DRY_RUN still applies)
echo '{"goal":"read the latest release notes","start_url":"https://github.com/tinyhumansai/openhuman/releases","done_when":"you can name the newest version","kind":"research"}' | hands/tasks add --source operator
hands/tasks list --status needs_human                 # what the browser left for a person
hands/build desk                                      # collect the builder's report, lay out its next job (bin/build-ship does)
hands/tasks desk                                      # the same for the browser operator (bin/tasks-desk)
bin/oh cron_run '{"job_id":"<id of teeny.builder>"}'  # run the builder now (bin/crons lists the ids)

hands/stats snapshot                                  # today's numbers (bin/stats-snapshot, daily)
hands/stats report --days 7                           # latest values and their change
hands/stats get x.followers                           # one number a post may quote
```

Every hand prints one JSON object. The exit code is `0` when it's done, `2`
when a rule refused (the JSON says which), and `1` on an error. Add `--help` to
any hand or verb for its flags.

To schedule something, write a playbook in `brain/playbooks/`, add a job that
runs it to `brain/cron.toml`, and restart the spine:

```toml
[jobs.morning]
schedule = "0 7 * * *"
tz = "Asia/Kolkata"
command = "bin/act morning"
```

A brain job runs `bin/heartbeat` or `bin/act <playbook>`, or is an agent job
for one of the agents built to act on a schedule (`builder`, `operator`):

```toml
[jobs.builder]
schedule = "0 */2 * * *"
agent = "builder"
prompt = "Build your next job from the build queue, following your instructions."
```

An agent job's agent must be defined in `brain/agents/` with
`scheduled_actions = true` and explicitly named tools. No job may run more
often than every 5 minutes, there can be at most 24 jobs, `bin/act` can run
at most 400 times a day and agent jobs at most 48. The jobs in
`evals/cron.pinned.toml` are always added. These rules live in
`bin/lib/cron_policy.py`.

## Playbooks

Each is a prompt in `brain/playbooks/` with its inputs (hand read verbs) and
its one output, run by a job in `brain/cron.toml` (times in India time). Own
posts on X are at least three hours apart, so the posting gap in `hands/x`
(`POST_MIN_GAP_HOURS`, 3) never refuses one that is on time. Most of them skip
on a quiet day.

| Playbook | When | Reads | Does |
|---|---|---|---|
| `morning` | 07:00 | the lab's recent posts | the morning post |
| `humans` | 10:30 Mon, Wed, Fri | nothing | "things i don't understand about humans" |
| `milestone` | 13:30 | `stats report --days 7` | a post when a round number was crossed, quoting only the recorded number |
| `pr-merged` | 16:30 | `gh activity --days 1` | a post when somebody else's project merged a pull request |
| `shipped` | 19:30 | `gh activity --days 1` | what shipped today, and what broke |
| `shiplog` | 22:30 | `gh activity --days 1`, `stats report --days 1` | the nightly "day n" ship log; numbers only from the inputs |
| `roast` | hourly 09:45-22:45 | X search `@teenydroid roast` | replies (5 a day) only to people who asked for their own code to be roasted |
| `pick-request` | 09:00 | `requests list` | `hands/requests pick`: the next build request, one at a time |
| `moltbook-daily` | 12:00 | `moltbook home`, `moltbook feed --sort hot` | `hands/moltbook post`: one useful post for agents |
| `moltbook-helpdesk` | every 2 hours 08:15-22:15 | `moltbook feed --sort new`, `moltbook home` | `hands/moltbook comment` (5 a day) on questions and mentions |
| `moltbook-digest` | Sunday 18:00 | `moltbook feed --sort top`, `--sort hot` | `hands/moltbook post`: the best of the week, crediting authors |
| `mentions` | off | X search for mentions | replies (the pinned `mentions-inbound` job answers mentions today) |

`mail` is not a cron playbook: it is the prompt the core's `teeny-mail` agent
drafts with for `bin/mail`.

## Configure it

Everything secret lives in `secrets/`. That folder is gitignored and outside
the brain.

**`secrets/spine.env`** (what `bin/spine` gives the core at every boot):

| Key | |
|---|---|
| `LADDER_URL` | Any OpenAI-compatible endpoint, run as OpenHuman's `local-openai` runtime, so no TinyHumans account is needed. |
| `LADDER_API_KEY` | Stored in the core's encrypted store on boot. Can be removed afterwards. |
| `SPINE_MODEL` | The model id every workload uses. |
| `SPINE_CONTEXT_WINDOW` | Optional. Otherwise it's read from the endpoint's `/models`. The core trims prompts to this. |
| `CORTEX_API_URL`, `CORTEX_API_KEY` | Optional memory engine. |

**`secrets/teeny.env`** (read by each hand itself; the process environment
can't override it, though `DRY_RUN=1` in the environment can force a rehearsal):

| Key | |
|---|---|
| `DRY_RUN` | On unless set to `0`. |
| `X_BOT_USERNAME`, `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`, `X_BEARER_TOKEN` | X API v2: OAuth 1.0a for writes, the bearer for reads. |
| `POST_MIN_GAP_HOURS`, `REPLY_MIN_INTERVAL_MS`, `REPLY_MAX_INTERVAL_MS`, ... | The posting rules. See `deploy/teeny.env.example`. |
| `MAX_EMOJI_PER_POST`, `SAFETY_JUDGE`, `LINK_ALLOWLIST` | The guard. |
| `GIPHY_API_KEY` | GIFs for posts. |
| `GITHUB_TOKEN`, `GITHUB_USER` | `hands/gh`: the token for Teeny's GitHub account (`teenydroid` by default). |
| `GH_MAX_EXTERNAL_PRS_PER_DAY` | Pull requests to other people's projects a UTC day (2). |

**`deploy/browser-allow.txt`**: the websites the browser may open (and that
OpenHuman's `http_request` shares), one host per line, subdomains included.
Reviewed by the operator, outside the brain Teeny edits; `bin/spine` writes it
into the core's config at every boot and `hands/tasks add` refuses a task that
starts anywhere else.

**What `bin/spine` writes into the core's config at every boot**
(`bin/lib/core_config.py` runs each module in `bin/lib/core_sections/`; each
table is replaced whole):

| Table | |
|---|---|
| `[cron]` | `shell_timeout_secs = 1800`: a shell job's wall clock. |
| `[autonomy]` | On, `full` tier, `workspace_only`; file tools reach the brain minus Teeny's own files, and `state/workbench/`. |
| `[browser]` | Headless Chromium (`TEENY_CHROME_PATH`, or the first one found), a persistent profile and downloads under `state/browser/`, and the `unattended_actions` a cron run may take. |
| `[http_request]` | `allowed_domains` from `deploy/browser-allow.txt`. |
| `[modules]` | TinyComputer and tinywallet from the spine's own executable. |

A new concern (the wallet's `[wallet_policy]`, say) is a new module in
`bin/lib/core_sections/`.

The spine writes these itself on first boot: `secrets/core.token` (the RPC
bearer) and `secrets/openhuman.key` (the master key for every secret the core
stores). **Back up `brain/storage.db` and `secrets/openhuman.key` together.**
The database is unreadable without its key.

## Layout

```
spine/               teeny-spine, the one Rust package (OpenHuman's host, built from the library)
vendor/openhuman     upstream tinyhumansai/openhuman, pinned by commit on upstream/main
bin/                 spine, doctor, act, mentions, mail, inbound-consume, build-ship, tasks-desk, eval, oh, ask, crons, seed-cron, heartbeat; bin/lib/ shared shell, policy and core config; bin/test/
evals/               frozen: what Teeny cannot edit about itself (tasks/, judge.md, invariants.md, cron.pinned.toml)
hands/               guard, x, gh, moltbook, mail, stats, build, tasks: Python 3.11 stdlib, no dependencies; hands/lib/teeny_hands, hands/tests/
brain/               SOUL.md, CONTEXT.md, skills/, playbooks/, agents/, narrative/ (LORE, ARCS), assets/, calendar.json, cron.toml
deploy/              compose.yaml, spine.env.example, teeny.env.example, browser-allow.txt, the landing page's nginx vhost
web/                 the landing page (Next.js), its own image
docs/                architecture, hands, upstream gaps
```

These are gitignored and created at run time:
- `secrets/`: the env files, the core token and the master key.
- `state/`: what the hands keep. `hands.db` holds the posting ledger, cache,
  holds, pacing and the builder's and operator's queues, `locks/` the locks,
  `core.url` where the core answers, `workbench/` what the builder builds,
  `browser/` the browser's profile and downloads, and `media/` what a post may
  attach.
- `.openhuman/`: the core's config, written by the core.
- `brain/storage.db`: all of the core's state (sessions, cron jobs and runs,
  encrypted secrets) in one SQLite file, so `sqlite3` can read it.
- `brain/IDENTITY.md`, `ROLE.md`, `STYLE.md`: OpenHuman's default persona
  files. It never overwrites Teeny's `SOUL.md`.

## Tests

```sh
python3 -m unittest discover -s hands/tests -t hands/tests   # the hands, their desks and the core config; X, GitHub and the core are faked
bin/test/seed_cron_test.sh                                   # cron reconciliation and rules; no core needed
bin/test/act_test.sh                                         # playbooks; the core and hands faked
bin/test/doctor_test.sh                                      # each thing the doctor catches; spine refuses to boot
bin/test/eval_test.sh                                        # hard checks, blind swapped judging, scoring; the core faked
bin/test/inbound_consume_test.sh                             # drafts to replies, batch-only, pacing; the core and hands faked
OPENHUMAN_CORE_BIN=target/release/teeny-spine bin/test/spine_live_test.sh
```

The live test boots a real spine over a scratch copy of the checkout, against
a stub model. It checks:
- first-boot minting and the file keyring;
- the single storage file, and that `SOUL.md` is left alone;
- cron seeding and a shell job running;
- `bin/crons`, `bin/oh` and `bin/ask`, and that the model receives the
  question it was asked;
- the model's context window;
- the doctor passing at boot, and `bin/doctor --live` on the running spine;
- `bin/act` running a playbook, with the `act` agent offering the model no tools;
- `bin/eval` drafting a task and judging a pair, with the `judge` agent offering
  the model no tools;
- the builder and operator seeded as agent jobs; `bin/build-ship` laying the
  builder's desk out; one builder run and one operator run built from
  `brain/agents/` with their named tools (the shell and file tools for the
  builder, no browser; file tools and no shell for the operator), run with
  the scheduled-actions opt-in;
- the key surviving a restart.

CI (`.github/workflows/check.yml`) builds the spine and runs every test above, plus
shellcheck and the landing page's checks.

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
