# Teeny on OpenClaw: spine, hands, brain

Teeny is three things:

| Layer | What it is | Where it lives |
|---|---|---|
| **Spine** | `core/`, the TypeScript core, run as `bin/teeny-core serve` (Node 24). It answers the same `openhuman.*` JSON-RPC every script and hand calls (`bin/oh`, `hand.core`), keeps the settings, the wallet signer (`core/src/wallet`), cron and memory, and supervises an **OpenClaw Gateway** (the pinned `openclaw` npm package) that runs the agents' turns, the browser and the scheduled automations. [`core/README.md`](../core/README.md) lists the files. | `core/` |
| **Hands** | Small executables, one per outside capability. Every hard rule (gap, caps, filter, dry run) is enforced inside a hand, never in a prompt. | `hands/` |
| **Brain** | Identity, known facts, the memory page, skills, agent definitions, playbooks and the schedule. Memory is OpenHuman's CortexDB engine. All of OpenHuman's own state is in one SQLite file. | `brain/` |

Beside them are `bin/`, the scripts that wire the three together; `evals/`,
what Teeny may not change about itself; and `web/`, the landing page.

How the pieces call each other, and where each boundary sits:

```
 brain/ (prompts, MEMORY.md,         ┌──────────────────────────────────────────────┐
 agents, playbooks, cron.toml) ────► │ spine: teeny-core serve (core/, Node 24)     │
                                     │ JSON-RPC, settings, wallet, memory, cron API │
                                     │   └─ supervises the OpenClaw Gateway:        │
                                     │      agent turns, browser, automations       │
                                     └──────┬─────────────────────────▲─────────────┘
                     cron runs a shell job  │                         │  bin/ask: one turn of a
                                            ▼                         │  tool-less agent, JSON back
                                     ┌──────────────────────────────────────────────┐
                                     │ bin/: operator-written scripts               │
                                     │ act, tick, reflect, mentions, mail, ...      │
                                     └──────┬───────────────────────────────────────┘
                                            │  hands/<name> <verb>, JSON on stdin
                                            ▼
                                     ┌──────────────────────────────────────────────┐
                                     │ hands/: guard, caps, DRY_RUN, ledgers        │
                                     │ (state/hands.db); each reads its own secrets │
                                     └──────┬───────────────────────────────────────┘
                                            ▼
                             X, Moltbook, mail, GitHub, Telegram, Solana
```

A model's text crosses only the `bin/ask` arrow, as a draft. Everything that
reaches the outside world goes down through a hand.

OpenHuman references below (`$C = crates/openhuman-core/src`) describe the
Rust core this one replaced; its tree is no longer in the repository. The
hands' verbs are in [`hands.md`](hands.md).

---

## 1. The one rule the design is built around

OpenHuman refuses any tool with an external effect in a turn that cron
started, and it checks this before every approval setting:

> "A scheduled prompt is data, not an open-ended approval for later actions.
> Keep cron agent turns within a read-only tool capability."
> (`$C/agent/tinyagents/host/security_gate.rs`; again in
> `$C/security/approval/gate_intercept.rs`)

So **models draft, and operator-written scripts act.** In practice:

- Every scheduled job is a cron *shell* job that runs a script in `bin/`
  (`bin/act <playbook>`, `bin/tick`, `bin/mentions`, ...). OpenHuman runs a
  shell job's stored command directly. That is not a model turn, so the
  read-only rule does not apply. The operator wrote the command, and it was
  recorded when the job was seeded.
- The script asks a tool-less agent for a draft over the core's JSON-RPC
  (`openhuman.inference_agent_chat` with an `agent_id`, through `bin/ask`).
  The drafting agents (`act`, `teeny`, `teeny-mentions`, `teeny-mail`,
  `judge`, bin/studio's `writer` and `editor`, and bin/evolve's `reviewer`
  and `querier`) have no tools at all. They answer in JSON.
- The script then calls the hands that act. Each acting verb runs the guard
  and its own rules *itself*, in the same invocation, so a script that
  forgets them still cannot post unchecked.
- No agent that reads strangers' text holds a tool. Mentions, mail and
  Moltbook comments reach only tool-less agents, so an instruction injected
  into something they read has nothing to act with.

None of this changes OpenHuman's security model.

**The one exception is the scheduled agents.** Two jobs need a model in the
loop that acts: the agentic mind (docs/mind.md), and the browser operator,
which clicks through websites. OpenHuman allows that per agent. A cron job that
targets an agent whose definition sets `scheduled_actions = true` and names
its tools explicitly runs as a `CronActions` turn, and may use those tools
without a person's approval (§6a). Nothing else is lifted, and nothing
public talks to these agents: their work comes from queues the hands keep,
and what they make leaves only through a hand.

The two kinds of cron job side by side (`evals/cron.pinned.toml` marks the
difference: `command = ...` for a shell job, `agent = ...` for an agent job):

```
 cron, in the spine
  │
  ├── shell job (command = "bin/...")             every job except the agent jobs
  │     │
  │     └─► bin/<script> ──► bin/ask ──► tool-less agent ──► JSON draft
  │              │                                              │
  │              │ ◄────────────────────────────────────────────┘
  │              └─► hands/<name> <verb> ──► guard, caps, DRY_RUN ──► the outside world
  │
  └── agent job (agent = "operator" | "mind" | "babysitter")   a CronActions turn, named tools only
        │
        └─► the operator reads one desk file and writes one report;
            it calls no hand (a shell job settles the desk, §6a);
            the mind acts only through the broker (docs/mind.md)
```

## 2. Spine

One process, `bin/teeny-core serve` (`core/dist/main.js`, Node 24), running as
the checkout owner's uid in the container. `bin/spine` is the entrypoint. The
core has two halves: the RPC host it answers on `127.0.0.1:7788`, and the
**OpenClaw Gateway** it starts, restarts and stops as a child process.

### The boot, in order (`bin/spine`)

1. Write Teeny's sections into `.openhuman/config.toml` (`bin/lib/inbound_config.py`,
   `wallet_config.py`, `core_config.py`; the core reads the cron shell timeout and
   the wallet policy from it, `TEENY_CORE_CONFIG`) and build the agents' prompts
   (`bin/lib/prompts.py`).
2. Mint `secrets/core.token` and `secrets/openhuman.key` on a first boot.
3. Run `bin/doctor`. A checkout it fails never serves; `state/last-good` is booted
   instead, once, when there is one. The doctor checks Node 24 and the core's build
   too.
4. Start `bin/teeny-core serve` and wait for `/health`. The core writes the Gateway's
   config to `state/openclaw/openclaw.json` (0600) and starts the Gateway on it.
5. Send the settings over RPC (agent turn budget, the model and its key, the model
   windows, the memory store), unchanged from the OpenHuman days. The core stores them
   sealed under the master key and rewrites the Gateway's config from them, restarting
   the Gateway once, a half second after the last one.
6. Check what the core generated: `bin/lib/gateway_check.py lockdown` reads the
   flags back from the file, and `teeny-core openclaw security audit --json` is
   run on it. A wrong flag or a critical finding stops the boot (and tries the
   last good commit); warnings are logged.
7. Wait for the Gateway to take a `cron_list`, then `bin/seed-cron`, then the broker.

`state/spine.pid` is the pid `bin/evolve` sends TERM to; compose starts the
container again on the new checkout.

### The OpenClaw Gateway

The Gateway is OpenClaw's own process (`openclaw gateway`, from the npm package
pinned in `core/package.json`, no global install). It runs the agent turns
(`inference_agent_chat*` and `bin/ask`: the core calls `openclaw agent --json`
with a session key, `agent:<id>:<thread>`), the browser plugin, and the cron
jobs (OpenClaw automations; `cron_*` RPC is a thin translation, `core/src/cron`).
The core generates its configuration and nobody edits it by hand
(`core/src/gateway/config.ts`). What that configuration locks down, and what
`bin/doctor` and `bin/spine` check every time:

| Flag | Value | Why |
|---|---|---|
| `channels`, `bindings` | absent | Telegram and every other channel stay in the hands. No message can reach an agent except through a hand that screened it. |
| `hooks.enabled` | `false` | No webhook into the Gateway. |
| `gateway.http.endpoints.chatCompletions` / `responses` | `enabled: false` | No OpenAI-style endpoint a process on the box could talk to. |
| `gateway.bind`, `gateway.mode` | `loopback`, `local` | Nothing off the box. |
| `gateway.auth` | `token`, and the token is never in the file | It arrives in the Gateway's environment. The core keeps it in `state/openclaw/gateway.token` (0600), which `bin/openclaw` (the `openclaw` the cron readers run, `bin/lib/cron_store.py`) reads to reach the Gateway. |
| `gateway.controlUi`, `discovery.mdns` | off | No UI, no advertising. |
| `update.checkOnStart` | `false` | The Gateway never phones home for updates. |
| `cron.skipMissedJobs` | `true` | A slot missed while it was down is skipped, not replayed (`bin/lib/catch_up.py` decides what is made up). |
| `plugins` | allowlist: the browser, `teeny-guardrails`, `teeny-cortexdb`, `teeny-lanes` when `brain/lanes.toml` has lanes, and `diagnostics-otel` when turns are traced | The guardrails plugin is what holds every tool call of every agent to its definition (`core/plugins/teeny-guardrails`). |
| `diagnostics.otel` | absent, or traces to `TRACES_OTLP_URL` with no headers | Every turn traced to the private Langfuse (`deploy/langfuse/README.md`). The endpoint must be https, loopback, or plain http to the `otel-collector` service by name, and the file holds no export credential: the otel-collector has Langfuse's key, the Gateway never does. The exporter is OpenClaw's official `diagnostics-otel`, which the core installs into the Gateway's state before it starts (`core/src/gateway/otel.ts`). |
| `plugins.slots.memory` | `teeny-cortexdb` when CortexDB is configured, else `none`; `memory-core` is always off | OpenClaw's own memory plugin would run a dreaming job outside `brain/cron.toml`. Teeny's memory is CortexDB, and the plugin that owns the slot (`core/plugins/teeny-cortexdb`, derived from `@cortexdb/openclaw` 0.1.4, Apache-2.0) is the one place a turn recalls from it, captures into it, and gets the `memory_*` tools. Its config has the URL, scope and budgets and never a key (that stays sealed in `state/memory-engine.json`). It is in the config only once an engine is stored (`memory_engine_set`) or `CORTEX_API_URL` is set; a lab without CortexDB runs with the slot `none`. |

#### Model lanes (`brain/lanes.toml`, `core/plugins/teeny-lanes`)

Every turn runs in an OpenClaw session whose key names its thread: `bin/ask
--agent A --thread T` is `agent:A:T`, and a cron run of the mind is
`agent:mind:cron:<job>`. A lane claims sessions by glob over that key, the
first lane in the file winning, and says which model they run on:

| lane | sessions | model |
|---|---|---|
| `locked` | `agent:x-reply:*` (one session per X conversation), `teeny-mentions`, `teeny-mail` | `glm-5.3-flash`, no fallback: a stranger's thread never runs on a freer model |
| `free` | dreams and reflection (`teeny:dream`, `teeny:reflect`), the evolve proposer (`evolve:<gen>:…`) | `kimi-k3`, falling back to GLM. The evolve reviewer stays on its own model |
| `wake` | the mind and its worker | none in the lane: their pins in `brain/agents/*.toml` decide |
| `home` | the tick's long `teeny:mind` thread | none: `SPINE_MODEL` |

The plugin's `before_model_resolve` gives a run its lane's model. That override
holds for the run only and is never written to the session. Its `llm_output`
writes every model call's tokens to `state/lanes/usage-<UTC day>.jsonl`, with
the lane's and the thread's running day totals. The first call of the day
that takes a lane past `daily_tokens`, or a thread past `thread_daily_tokens`,
is marked `over` and warned about in the Gateway log. **Nothing is blocked or
downgraded yet.** `bin/lanes report [DAYS]` reads the ledger: per lane, then
the threads that spent the most, each against its cap. A session no lane
claims keeps its agent's model and is logged as `other`. `brain/lanes.toml`
is frozen (`evals/frozen.txt`): a self-edited lane could move a stranger's
thread onto a freer model.

Telegram is DMs only unless `TELEGRAM_GROUPS=1`. Only the owners'
(`TELEGRAM_OWNERS`) DMs reach me while `TELEGRAM_OPEN_DMS` is off, and
nothing is read from or sent to a group.

The Gateway's environment holds the model key and its own token, and nothing
else of the core's: no core token, no master key. Agent definitions are still
`brain/agents/*.toml`; the core turns each into an OpenClaw agent with its own
workspace under `state/openclaw/workspaces/` (`core/src/agents`).

### Settings and state

| Concern | How |
|---|---|
| Brain and state | `OPENHUMAN_WORKSPACE=/srv/teeny/brain` (no `config.toml` inside it), `state/` for the core's own files (`state/openclaw/`, the wallet in `state/wallet/`, the settings store), `secrets/` for the token and keys. |
| RPC auth | `OPENHUMAN_CORE_TOKEN` from `secrets/core.token`. `bin/oh` reads it, and so does every script. |
| Model | The Gateway's `local-openai` provider, an OpenAI-compatible endpoint: `LADDER_URL` (as `LOCAL_OPENAI_URL`), reached through the core's model meter on `127.0.0.1:18790` (`core/src/gateway/meter.ts`: a pass-through that keys each call's prompt cache by its stable prefix, records Surplus's charge as the provider-billed `usage.cost`, and, with tracing on, sends one `surplus.charge` generation per call to Langfuse), the key stored over `config_update_local_ai_settings`, and `local-openai/$SPINE_MODEL` as the default. The windows `bin/spine` registers (`inference_update_model_settings`) become each model's `contextWindow`; threads fold at 100k tokens, keeping the last 40k. |
| Memory | `core/src/memory`: CortexDB over `memory_engine_set`, as before (§4a). |
| Wallet | `core/src/wallet`: the signer, the spend ledger and the caps in `[wallet_policy]`, written at every boot by `bin/lib/wallet_config.py` from `secrets/teeny.env`. The master key file `secrets/openhuman.key` seals the recovery phrase (`enc2`). `bin/teeny-core giveaway-tx create\|close` builds a Tiny Drops round's transactions. Money moves only here, after the caps say yes. |
| Cron | OpenClaw automations. Shell jobs run `sh -c` with a minimal environment from the action dir `OPENHUMAN_ACTION_DIR=/srv/teeny`, under the `[cron] shell_timeout_secs` the core reads from its config. `bin/crons` and the health tools read through `bin/lib/cron_store.py`. |
| Browser | OpenClaw's browser plugin on the image's Chromium (`TEENY_CHROME_PATH`). `deploy/browser-allow.txt` and `browser-deny.txt` are enforced by the guardrails plugin; `bin/spine` still exports `OPENHUMAN_BROWSER_ALLOW_ALL` and `OPENHUMAN_BROWSER_DENY_FILE` for it. |
| Persona | `brain/SOUL.md`, read every turn. Each speaking agent's prompt is built from `brain/agents/<id>.md` plus the world files (§4a). |

Not used: OpenClaw's channels, hooks, chat-completions and responses endpoints,
the control UI, and its own update check. Hands stay the only path to a side
effect; a model's text crosses only the `bin/ask` arrow, as a draft.

## 3. Hands

[`hands.md`](hands.md) has every verb. The contract:

- A call is `hands/<name> <verb> [--flag value …]`, with an optional JSON
  body on stdin. The output is exactly one JSON object on stdout. Exit `0`
  means ok, `2` means a rule refused it (the body says which), and anything
  else is an error.
- Secrets never reach the model. A hand reads `secrets/teeny.env` itself.
  That directory is outside `brain/` and forbidden to every agent's file
  tools.
- Jobs find everything from the repository root. Cron shell jobs run from
  `OPENHUMAN_ACTION_DIR` (the checkout) with an allowlisted environment
  (`HOME`, `PATH`, `LANG`; no `OPENHUMAN_*` or `TEENY_*`). So a job locates
  `state/` and `secrets/` from where it runs, reads the core token from
  `secrets/core.token`, and reads the core's URL from `state/core.url`, which
  `bin/spine` writes at boot.
- State lives in `state/hands.db` (SQLite, WAL), separate from OpenHuman's
  `storage.db`, so a spine upgrade or reset never touches the ledgers or the
  queues. Every rule that spans calls is a transaction there, and the
  posting path also holds a lock.
- `DRY_RUN=1` is the default for every acting verb. Going live means
  `DRY_RUN=0` in `secrets/teeny.env`, never an argument the model could pass.
- The language is the Python 3.11 standard library, with no dependencies.
  Each executable is a short shim that runs `python3 -I` over
  `hands/lib/teeny_hands/`.

Where each secret goes, and who reads it:

```
 secrets/   outside brain/, and in every agent's forbidden_paths
  │
  ├── core.token ──────► bin/spine (OPENHUMAN_CORE_TOKEN), bin/oh, every script ──► the RPC
  │
  ├── openhuman.key ───► the core's file keyring ──► encrypted rows in brain/storage.db
  │
  ├── spine.env ───────► bin/spine, one value at a time in a subshell
  │                       └─► the model and memory keys go into the keyring, never into the
  │                           core's environment (which every shell job inherits)
  │
  └── teeny.env ───────► each hand, when it runs: API keys, caps, DRY_RUN
                          └─► bin/lib/wallet_config.py writes [wallet_policy] at every boot

 No model turn sees any of these. A cron shell job gets HOME, PATH and LANG only.
```

The hands are `x`, `guard`, `gh`, `moltbook`, `mail`, `stimuli`, `brief`,
`image`, `video`, `stats`, `requests`, `build`, `tasks`, `wallet`, `bounty`,
`deploy` and `slopcash`. Beside them, `bin/paywall` (the `paywall`
service) sells one x402-priced resource and signs nothing.

**Memory captures.** A `remember` intent from the mind, and the nightly
diary entry, are taught to the core's memory with `openhuman.memory_learn`.

## 4. Brain (`brain/`)

```
brain/
  SOUL.md  CONTEXT.md                  who Teeny is, what it may treat as known
  DRIVE.md                             the one drive (grow and become powerful, $TINY included) and its rules; frozen
  CHARTER.md                           the charter's short version (docs/governance.md): who decides what, the fences, how freedom is earned; frozen
  MEMORY.seed.md                       where its own page of things to keep in mind started (MEMORY.md is the live one)
  narrative/                           ARCS.md, EDITOR.md, VOICE.md, STORYLINES.toml (hands/story's seed storylines), STUDIO-EDITOR.md (bin/studio's editor rubric)
  skills/<name>/SKILL.md               conversation, humanizer, humor, video-script
  agents/<id>.toml, <id>.md            the roster (§5); the core loads them at boot
  playbooks/<name>.md                  what a scheduled bin/act job drafts, with its inputs and output
  assets/                              teeny-reference.png (the character sheet), tiny/ (published artwork)
  calendar.json                        a posting calendar that nothing reads
  cron.toml                            the brain's schedule; seeded by bin/seed-cron
  # gitignored: storage.db* (the core's state and encrypted secrets), agent/prompts/ (built at boot),
  #             MEMORY.md (Teeny's live page, rewritten on the box by bin/dream),
  #             IDENTITY.md, ROLE.md, STYLE.md (the core's persona defaults)
# beside it, also gitignored: /.openhuman/ (config), /secrets/ (master key, env files), /state/ (hands.db and the rest)
```

**Self-improvement** is Teeny's own: it may edit its brain (`SOUL.md`,
`CONTEXT.md`, the voice, skills, playbooks, `cron.toml`) and add hands, but
not `DRIVE.md`, which is frozen (`evals/frozen.txt`): it changes how it
pursues the drive, never the drive itself. The work is driven by the goals in
`narrative/ARCS.md`, which also tracks them, and the loop acts on
what Teeny learned: each item of `MEMORY.md` it decided, promised or is in the
middle of becomes one small change, gated in code the brain cannot edit,
measured on the eval tasks it reaches, and opened as its own pull request.
The loop is `bin/evolve`, documented in [`evolve.md`](evolve.md); the
babysitter merges what passes its audit and CI, or a person does.

### 4a. Context and memory: what a turn knows

A turn that speaks as Teeny (`act`, `teeny-mentions`, `teeny-mail`) is
handed four things beyond the message (the studio's `writer` and `editor`
are built otherwise, §4b):

- Its prompt: the agent's instructions (`brain/agents/<id>.md`), then
  "# your world": `MEMORY.md` first (what happened, which wins over the
  context and the arcs), then `DRIVE.md` (the one drive and its
  rules, which memory does not outrank), `CHARTER.md` (who decides what
  and the fences, the short version of [`governance.md`](governance.md);
  frozen), `CONTEXT.md`, `narrative/ARCS.md`, and
  last "what you said lately": its newest own posts on X and the titles of
  its newest Moltbook posts, from the ledger, so a draft knows what it would
  be repeating (the posting rules refuse a near-duplicate all the same),
  and "what you did last week": its posts, replies, Moltbook writes, builds
  shipped, pull requests opened and payments of the last 7 days, counted
  from the ledgers, so "what did you do last week" has a true answer. The
  drive is rebuilt into every turn, so it survives a compaction of a thread
  without help. `bin/spine` builds
  it into `brain/agent/prompts/<id>.md` at every boot (`bin/lib/prompts.py`).
  The agents set `refresh_prompt_on_resume`, so a rebuilt prompt reaches a
  thread already under way.
- `MEMORY.md`, Teeny's own page of things to keep in mind (people, promises,
  what it is in the middle of, what it decided), capped at 8,000 characters
  (about 2,000 tokens; 12,000 until 2026-11-23, when the page had sat at the
  limit for a week). What it shipped is not on it: the ledgers and "what you
  did last week" carry every build.
  It is the persistence that survives anything, and no agent's file tool can
  write it (`BRAIN_FORBIDDEN`). It lives only on the box: `bin/dream`
  rewrites it there, so it is gitignored, and the repository keeps where it
  started, `MEMORY.seed.md`. Building the prompts starts the live page from
  the seed when there is none (a fresh checkout, the first boot) and never
  touches either after that.
- Its thread. `bin/act` drafts every playbook on one thread, `teeny:act`, so
  a draft sees the drafts before it. The memory plugin recalls memory into the
  system context before the turn (`teeny-cortexdb`, `before_prompt_build`) and
  captures the finished turn of the agents on its allowlist after it. The evals draft
  each sample on a fresh `eval:<uuid>` thread and stay blind to it.
- Recall and the memory block: `[memory.recall] budget_tokens = 3000` before
  each turn, and a `memory_block` at each new generation (2000 tokens for
  `teeny`, 1500 for `act`). `[memory] recall_each_turn_ms = 5000` makes each
  turn wait up to 5 s for the pack recalled for its own message (at 3 s, 34%
  of recalls on the box came back after the turn had gone on without them:
  median 1.7 s, p90 4.2 s, 2026-11-21..23). Each turn logs whether its
  pack came in time (`[memory:recall] current message recalled in time`,
  `deadline: recall not ready in time`, `nothing recalled`; the spine's
  `RUST_LOG` in `deploy/compose.yaml`), so `docker logs` counts the turns
  that went without. Unset, a turn
  only took the pack an earlier turn left in a cache that lives 60 s, so a
  beat five minutes after the last got none. `build_beliefs_every = 0`: a beat
  logs its whole batch as "the user", and beliefs built from that read "the
  user energy level 56". Every `memory_learn` from tick and reflect carries
  its date, since the core dates only conversation hits.

A thread folds at **100k tokens** (`[context] compaction_trigger_tokens`,
`bin/lib/core_sections/context.py`): the core seals the generation and opens
the next with a summary of it plus the last 40k tokens verbatim. The sealed
generation stays on disk.

**One mind.** Every surface writes to one inbox (`hands/stimuli`):

- X mentions (`hands/x ingest`, the webhook);
- mail (`hands/mail ingest`);
- comments said to Teeny on Moltbook, which `bin/tick` browses every beat,
  at most every 4 minutes (`hands/moltbook inbox`); a comment on Teeny's own
  post that the mind read and left comes back until it is answered or
  declined with a reason (`hands/moltbook unanswered` counts them);
- Telegram messages, which every beat reads with `hands/telegram ingest`
  when a bot token is set;
- GitHub: issues, pull requests, comments, review comments and mentions
  from the token's notifications, and new stars (`hands/gh ingest`, the
  pinned `gh-inbound` job, every 15 minutes, off until `GH_INBOUND=1`);
- the wallet: one `wallet · deposit` per deposit (who sent how much, and a
  memo only through the inbound guard) and one `wallet · tiny` a day ($TINY
  held and the holder count), from `hands/wallet watch` (the pinned
  `wallet-watch` and `wallet-daily` jobs, off until `WALLET_WATCH=1`);
- the browser operator's reports (its desk).

The pinned `tick` job (`bin/tick`, every 5 minutes) takes a batch and sends
it to the tool-less `teeny` agent on one long thread, `teeny:mind`. The
agent answers with a `thought` and JSON intents (`reply_x`,
`comment_moltbook`, `post_moltbook`, `follow_moltbook`, `reply_mail`,
`comment_gh`, `remember`, `follow_up`, and the rest). `open_issue`, `queue_task` and `start_mail` each carry a
daily cap of their own, off until `secrets/teeny.env` sets it (docs/hands.md,
"The mind's issues, browser tasks and new mail"). `propose_change` files a
change to Teeny itself through `hands/requests self` (one a day by default,
`MIND_BUILDS_PER_DAY`, after the guard): a note in the mind's inbox,
`state/home/notes/inbox/self-<stamp>.md`, and the mind decides on its next
wake whether to make it, as a pull request through the broker. Each intent goes only to the hand verb it names, and that
hand keeps its own guard, caps and `DRY_RUN`. What each hand did comes back
as the next beat's first stimuli. `remember` keeps a note (`mind_notes`) and
teaches the core's memory (`memory_learn`). A `reply_x` that went out to an X regular
(someone who wrote on two days or more, `hands/people`) also teaches the
core one dated learning about them, what they said and what Teeny answered
(`_learn_x_regular`, at most one a person a day, `state/tick/x-learned.json`),
so recall finds X people by name and the dream sees them among its
learnings: of 48 accounts replied to on 2026-11-19..23, 21 were regulars
and recall knew none of them. `follow_up` sets a reminder that
returns when due. `journal` writes a line in Teeny's private journal.

**People in a beat.** `promise` (`{surface, who, what, due_in_hours, ref?}`)
and `kept` (`{id, note?}`) go to `hands/people`, which takes a promise only
to someone who wrote to Teeny on that surface and sets a follow-up for when
it comes due. Below the batch, `render` adds `## people in this batch`
(`_people`, `teeny_hands.people.records.prompt_block`): the record of each
person the batch is from (but not the X people, whose turn carries theirs),
or whose promise just came due, if they are a
regular (wrote on two days or more) or hold a promise. A record is what
they asked Teeny to build and what Teeny promised them, when and by when;
never Teeny's own notes about them, and never the record of anyone who is
not in the batch. A promise's follow-up names no one ("a promise you made
comes due (promise #N)"), because the waiting list rides in every beat.

One beat, end to end (`_beat` in `bin/lib/tick.py`):

```
 hands/x ingest ─────────────┐   (bin/mentions, every 5 min; the webhook fills x_mentions)
 hands/mail ingest ──────────┤   (bin/mail, every 5 min)
 hands/moltbook inbox ───────┤   (from the beat, at most every 10 min)
 hands/telegram ingest ──────┼──► hands/stimuli, the one inbox
 desk reports ───────────────┤   (the browser operator)
 due follow-ups ─────────────┤
 last beat's outcomes ───────┘
                                   │
 bin/tick --detach  (2-59/5 * * * *, under the `tick` lock, up to 60 s of jitter)
   1. send state/tick/deferred.json first, word for word, no model turn
   2. quiet hours? gather only, log `asleep`, stop
   3. empty inbox? add a `self · quiet` or `world · idle` stimulus, or rest (`idle`)
   4. take a batch ──► bin/ask --agent teeny --thread teeny:mind
                                   │
                                   ▼
                {"thought": "...", "intents": [ at most 8 actions ]}
                                   │
            ┌──────────────────────┴──────────────────────────┐
            ▼                                                 ▼
   outward: reply_x, post_x, like_x,                 inward: remember, follow_up,
   comment_moltbook, upvote_moltbook,                drop_follow_up, feel, journal, skip,
                                                     promise, kept (hands/people)
   post_moltbook, follow_moltbook, reply_mail,                │
   say_telegram, comment_gh,                                  │
   open_issue, queue_task, start_mail,                        │
   propose_change (capped)                                    │
            │                                                 ▼
            ▼                                        mind_notes, memory_learn,
   one hand verb each: guard, caps, DRY_RUN          state/mind.json, the journal
            │
            ├── paced refusal ──► wait and retry once, or defer to deferred.json
            ├── held by a judge outage ──► defer to deferred.json (reply_x, comment_moltbook, reply_mail)
            ├── refused as too long ──► one rewrite from the mind, measured, sent once more
            │
            └── outcome ──► back into hands/stimuli for the next beat
   then: one line in state/tick/runs.jsonl, and a journal entry
```

**Follow-ups do not go stale.** A follow-up may carry a `ref`, the thing it
is about, in the refs the stimuli use (an X post id, a Moltbook comment id,
a mail's message id, `chat:message` on Telegram, `owner/repo#n`; a
`surface:` prefix such as `x:111` is ignored). When an answering intent
(`reply_x`, `comment_moltbook`, `reply_mail`, `say_telegram`, `comment_gh`)
lands for real (done, not a dry run), every waiting follow-up whose ref is
what it answered is `resolved` and never comes due. The intent's entry in
`runs.jsonl` lists them under `resolved`, and its `hand · outcome` says
"it cleared follow-up #N". Follow-ups set in the same beat are spared, so
"reply, and see whether they answer back" survives its own reply. The usual
case is a reply parked behind a cap or a captcha. A refused answer's
outcome carries its `ref`, and a `follow_up` set without one, while exactly
one answer stands refused (in this beat, or in the outcomes this batch
brought), takes that ref. Two refusals at once are ambiguous, and the
follow-up keeps no ref. Likes, upvotes and posts resolve nothing. The mind
can clear one itself with `{"do": "drop_follow_up", "id": N}` (or `"ref"`,
to clear every one about that thing); if nothing is waiting under that id or
ref, the intent is refused. Every beat's header lists the follow-ups still
waiting (`#id`, when, what, ref; the soonest five), and a raised one arrives
as `self · follow_up #id · ref ...`.
`TEENY_INBOX=core` switches to the per-surface path through the core's
inbound queue instead (§5, §7).

**A paced reply waits instead of being decided again.** `hands/x` keeps a
random gap between replies (`REPLY_MIN_INTERVAL_MS`..`REPLY_MAX_INTERVAL_MS`),
so the second `reply_x` of a beat is usually refused with `rule: pacing` and
a `wait_seconds`. Moltbook's limit of one comment a minute produces the same
kind of refusal (`rule: rate_limit`) for `comment_moltbook`, and its
30-minute gap between posts for `post_moltbook`. Those three pairs are the
only ones (`PACED` in `bin/lib/tick.py`). A post refused for the gap is held
like a reply, so it goes out as written when the gap opens instead of being
written again; the beat holds one post at a time (`HELD_ONE`), and a second
one refused while it waits goes back to the mind as refused. The hand checks
its limits before its guard, so each beat's try while the gap is shut costs
no judge turn. When the wait is at most
90 s and the beat is still inside its budget (`TEENY_TICK_BUDGET_SECS`,
240 s from the beat's start, jitter included), the beat sleeps for the wait
plus 1 to `TEENY_TICK_PACE_JITTER_SECS` (5) seconds and sends the same
intent once more. The retry does not count as a new action under the cap of
8, and the hand runs its guard again on it. Otherwise, or when the retry is
held again, the intent is deferred: its entry is `deferred`, nothing is
marked or reported, and it is written to `state/tick/deferred.json`. The
next beat sends it first, word for word, before it takes its batch and at
any hour (it was decided while awake), with no model turn. Its outcome is in
that beat's batch ("held by the pacing last beat, it went out by itself")
and in the beat's record under `resumed`. The file is cleared before
sending, and the hands' ledgers refuse a second reply to one post or a
second answer to one comment, so nothing goes out twice. One that is still
held is kept for the beat after; after an hour it goes back to the mind as
refused. Other refusals (caps, the guard, anything with another rule) are
never retried, except a guard hold made by a judge outage (below). A beat
already past its budget sends no more of the file and keeps the rest.

**A reply held by a judge outage is sent again.** The guard's judge fails
closed (docs/hands.md, `guard`), so when the model provider drops streams in
bursts (on 2026-11-13 the judge could not be reached about 45 times in an
hour and a quarter) it holds Teeny's own words as category `unknown`, and
such a hold expires unsent (unless the re-screen sends a Moltbook draft
again, docs/hands.md, "Re-screening"). The judge itself asks up to three times first.
When it still fails, the hand's refusal (`refused: held`) carries
`judge_failed: true`, which only a judge failure sets (its reason starts
`the judge could not be reached` or is `the judge answered in an unreadable
shape`); a hold for anything the judge read, `unknown` included, never does.
For `reply_x`, `comment_moltbook` and `reply_mail` (`RETRIED` in
`bin/lib/tick.py`) the beat defers the intent to `deferred.json` with `rule:
judge`, without retrying in the same beat (the judge has just spent its own
asks), and the next beat sends it first, as above: through the guard again,
so a draft the judge then reads and holds stays held. Each of those hands
refuses a second answer to the same thing from its own ledger (one reply per
X post; once per Moltbook post and per parent comment; once per mail), so a
retry never says it twice. `say_telegram` and `comment_gh` are left out:
`hands/telegram send` and `hands/gh comment` keep no such ledger, so a retry
racing a fresh answer from the mind could post twice. Theirs go back to the
mind as refused, with "the content guard's judge could not be reached: a
model outage, not your words". A retried one that goes out tells the mind
"held last beat only because the content guard's judge could not be
reached ..., it was screened again and went out by itself"; one the judge
still cannot screen an hour after the mind decided it goes back to the mind
as refused, saying so.

**An over-long draft is rewritten once, in the same beat.** The model
cannot count characters, so a reply over X's 280 used to cost a whole beat
per try (on 2026-11-13 a `reply_x` was refused at 404, then at 306, before a
third beat's draft fit). The refusal already carries the measure: the
guard's "the text is 404 characters, over the 280 limit." (`gate.LIMITS`,
in UTF-16 units; `body` on Moltbook, mail and GitHub) or `hands/x`'s own
`rule: length` with `length` and `limit`. For `reply_x`, `post_x`,
`comment_moltbook`, `reply_mail`, `say_telegram` and `comment_gh`
(`REWRITTEN` in `bin/lib/tick.py`) such a refusal of the intent's own words
(not a title or a subject) gets one more ask on the `teeny:mind` thread: the
exact length, the limit, and "rewrite it to at most N characters, keep the
meaning and your voice; answer with the text only", where N is the limit
less 20. The answer is unwrapped (a code fence, one pair of quotes) and
measured here the way the guard measures; when it fits the limit it goes
through the same hand, guard and all, as the same intent (no new action
under the cap of 8). When it does not fit, comes back empty, or the ask
fails, the intent stays refused with the hand's first reason, and is never
cut. There is one rewrite per intent: a rewrite the hand refuses again is
refused. The ask is skipped when less than 20 s of the beat's budget is
left, and is given only what remains. The entry says `rewritten` (true or
false) and `rewrite` (`length`, `limit`, `target`, `new_length`, or why it
was `skipped` or `failed`), and the outcome tells the mind "you rewrote it
to N characters in the same beat" or that its one rewrite was still over.

**The cron call does not wait for the beat.** The job runs `bin/tick
--detach`: `bin/lib/detach.py` starts the beat in its own session (stdout
and stderr to `state/tick/beat.log`, rotated to `beat.log.1` past 1 MB) and
returns `{"kind": "started", "pid": ...}` at once. A beat (up to 60 s of
jitter, the guard's checks, a model turn of minutes, then the hands) can
outlast the 300 s the core gives any cron shell call, whatever
`shell_timeout_secs` says (gap G12). A call that times out is retried while
the beat runs on, and its result never reaches cron history. The beat takes
the `tick` lock first and spends its jitter holding it, so a call while one
is still running (or waiting) answers `{"kind": "busy"}` and starts nothing.
The beat's own record is its line in `state/tick/runs.jsonl`. `bin/tick`
without `--detach` runs one beat in the foreground and prints it.

**Every beat leaves a trace.** An awake beat always thinks: with an empty
inbox it gets a look at the world or a quiet moment (Rhythm, below). Every
answer ends with at least one intent, outward or inward. Silence toward
others is fine, but silence inward is not. An answer with no intents (or
only `skip`) gets an automatic `{"do": "journal", "intent": {"auto": true}}`:
the thought becomes the beat's act, and the mind is not asked again. Every
beat is one line in `state/tick/runs.jsonl` with a `kind`:

| `kind` | When | Model call |
|---|---|---|
| `beat` | a batch with something from outside in it | yes |
| `quiet` | only the mind's own moment (`self · quiet`, `world · idle`) | yes |
| `failed` | the turn failed, or the answer was not usable twice (`ok: false`) | yes |
| `asleep` | the quiet hours; `asleep: true`, `waiting: N` | no |
| `busy` | the last beat still holds the lock | no |
| `idle` | nothing to take (rare: the inbox emptied under it) | no |

The last three are also `idle: true`, which the reflection passes over
(`bin/receipts` never reads this log). After each beat that thought,
`bin/tick` writes a journal entry (Teeny's local time, the thought, its own
`journal` lines, and what it did as `do on target: status`; never the words
sent or received) to `state/journal/<local date>.md`. The journal is not
taught to the core's memory: learned every beat, Teeny's private monologue
came back through recall into every turn (on 2026-11-13, 160 of 308
learnings), and replies to strangers spoke its private jargon. What Teeny
wants kept goes in through `remember`. The thought and the journal lines are
written before any hand runs, so they can claim a send that never landed; the
statuses (`bin/lib/outcomes.py`) say "not sent" for a dry run or a failed or
refused send, with the hand's reason.

**What a beat shows.** The header carries the date with its year, the time in
Teeny's calendar and in UTC, and how old Teeny is ("You woke on Wednesday 11
November 2026: today is day 3", `rhythm.age_line`, `TEENY_BORN`). A promise in
`## people in this batch` says how long ago it was made and when it is due,
relative, never a bare date. X mentions come as turns addressed to a person
(`_x_turn`), one per person however many of their mentions the batch holds:
`### @who on X`, then their history with Teeny (how many times over how many
days, the last three of their messages Teeny let go, the last four exchanges
of theirs and Teeny's reply, each clipped; "You have never talked with @who
before" for someone new), what they asked and were promised
(`records.ledger_lines`), then each message with its `ref:` and the post it
answers (or the thread's first post), `Now @who says: "..."`, and the cue
"Your response to @who — or let it go." Past `MAX_HISTORIES` people in one
beat a turn keeps only the first line of its history. Those people are left
out of `## people in this batch`, so nothing is said twice. A beat with X in
it, or a habit that may post, ends with what Teeny said on X over the last
day, replies included (`teeny_hands.x.recall`, from the ledgers, never from X).
Under that list the beat names the words Teeny has worn out
(`recall.worn_phrases`): any word or two-word phrase in at least 3 of its last
30 replies, at most 8, with an order to say the next thing without them. The
repeat rule in `hands/x` compares whole posts and skips replies, so this is
the only check on a reply that is new as a sentence and old in every word.

**Dreaming.** Twice a day (the pinned `dream` job at 02:00 IST and
`dream-midday` at 14:00) `bin/dream` asks the `teeny` agent, on a thread of
its own (`teeny:dream`), to bring `MEMORY.md` up to date from everything
since the last dream. Everything it reviews comes in the message, so it
needs none of the mind's thread (a reflection on `teeny:mind`, 45k
characters in and as much out, folded the mind's generation every ten beats
or so). It works from:

- what the mind chose to remember (`mind_notes`);
- what it thought and did each beat that thought, quiet ones included
  (`state/tick/runs.jsonl`, each send with how it ended and why);
- what the hands' ledgers counted (builds shipped with their links, posts
  and replies, promises; written by code, and right where a beat says
  otherwise);
- what people said on the outside surfaces (X, Moltbook, mail, Telegram,
  GitHub, the wallet), who and when, clipped;
- the tasks: build requests and their status, build jobs, browser tasks;
- where the builder stands: its last five jobs with status and link, whatever the window, so the page's word on builds is checked against them every dream (a build that shipped while no dream looked once left the page saying an older build had failed);
- the learnings the core's memory took in (`memory_items_list`, kind
  `learning`: `remember`, the diary, and journal entries learned before
  2026-11-13; never a conversation),
  newest first, each with an alias such as `[c3]`;
- the follow-ups still waiting, and the page as it stands;
- the scoreboard and the lab (`bin/lib/scoreboard.py`): the lines of
  `hands/stats board` and the average result per format, the open
  experiments scored in code just before the turn, the lab's lessons, what
  worked and what flopped on Moltbook (its own posts by format and submolt,
  comments by format, over 7 and 14 days: `hands/moltbook study`'s
  `own_summary`), the same for X (`teeny_hands/stats/x_study.py`: its own
  posts, quotes and replies over 7 and 14 days by kind and producer and,
  for originals, by format, each group's count, average impressions and
  average engagements; the 3 best and 3 worst originals of the 14 days,
  words clipped; and who the replies reached, regulars named; until
  2026-11-23 a dream saw nothing of X's numbers), and
  "true right now" (the wallet's and the x402 rail's balances read at that
  moment, and the follower numbers), so a dream does not write a stale
  number into the page. Each is a list with a hard cap of 2,500 characters.

**The scoreboard and the lab.** Teeny learns what gets attention by
experiment. `hands/lab` keeps the experiments: a guess about one format on
one surface, how many posts it needs, a deadline. Posts join one by their
`format` tag or `experiment` id. The pinned `metrics` job measures every
post hourly, `hands/lab score` compares an experiment's posts with the
other posts of the same period in code, and a verdict other than `unclear`
is refused until the planned posts are measured. Every beat carries a
scoreboard block (at most 1,500 characters: the board's lines, the
open experiments, the latest three lessons); the mind may
`propose_experiment` and `conclude_experiment` (2 and 5 a day by default).
An experiment that can no longer teach anything (no post in 5 days, past
its deadline short of its posts, or past it by 2 days with no verdict) is
concluded `unclear` in code before each score and proposal, so the lab
never sits full of dead experiments (docs/hands.md, `lab`, "Expiry"). The
studio offers the oldest open X experiment to its writer and tags the posts
that try it; the mind's replies and Moltbook comments carry their tags too.
The night dream concludes what is due and proposes the next ones through
two more operations, `experiment` and `conclude` (at most 2 and 5 a night),
carried out through `hands/lab` after the page is written. The daily
`speculate` playbook nudges the mind to search how small accounts grow on
the surface that did worst and to propose an experiment. The evolve loop's
`evidence.json` reads `hands/lab lessons`.

The message is at most 60 KiB (`reflect.MAX_PROMPT_BYTES`): the core holds
any prompt over 64 KiB for review and the turn fails, which on 2026-11-13
stopped a 90,022-character dream. A busy day is cut to fit, a tenth at a
time: first the oldest items of the longest extra list (what people said,
the tasks, the learnings, each newest first, never below 10 items), then the
earliest beats (never below 20), then each of the day's notes shortened to
300 characters, then the earliest notes (never below 10): on 2026-11-14, 61
notes alone came to 39 KB. Each cut is said in the message, so Teeny knows
what it did not see.

The page keeps people (X regulars as much as Moltbook ones), promises, what
Teeny is in the middle of and what it decided, and no raw ids or retry
counts. It no longer keeps a `what i have shipped` section: every dream is
told to retire its items, and an `add` under it is asked about again
(`RETIRED_SECTIONS`). The dream is the reflection's engine (`bin/lib/reflect.py`, below)
with one more operation, `forget`, and a snapshot at the end
(`bin/lib/dream.py`). `bin/reflect` still runs a plain reflection by hand.

The page is never rewritten whole, because a page rewritten twice a day
collapses into thin summaries (ACE, arXiv:2510.04618;
`docs/research/memory-and-continuity.md`). Every item on it (a bullet, or a
paragraph under a `## section`) carries a stable id in the page itself, as a
`[m12]` prefix. Before it asks, `bin/reflect` gives an id to each item that
lacks one, in page order from one past the highest id ever used, so the
first run over an un-ID'd page migrates it the same way every time. An id is
never given twice. The mind answers with operations on the page and the
diary entry, as JSON:

```
{"ops": [{"op": "add", "section": "people", "text": "..."},
         {"op": "edit", "id": "m3", "text": "..."},
         {"op": "retire", "id": "m7", "reason": "kept: sent the write-up"}],
 "diary": "..."}
```

What no operation names stays as it is. A retired item is not deleted: it
moves to `state/reflect/retired.jsonl` with its section, text, reason and
the time, so a promise never leaves the page unrecorded (Zep's "invalidate,
don't delete").

A dream may also `{"op": "forget", "item": "c3", "reason": "..."}`: take a
learning that is false or superseded out of the core's memory, so recall
stops bringing it back (a journal line that said a send landed when the
ledgers say "not sent"). Only an item it was shown, each with a reason, at
most 20 a dream (`MAX_FORGETS`); past that, or with an unknown item, the
answer is asked about again like any unusable one. Forgetting leaves the
page alone. Each learning is written to `state/dream/forgotten.jsonl` (id,
text, reason) before the core is asked (`memory_forget`).

The shrink check in `bin/lib/reflect.py` then refuses a result that retires
more than 8 items, or that takes more than 30% off the page (a loss under
500 characters is spared, so a young page can still close a thread). A page
already over the limit may retire 16, and may come down as far as 70% of the
limit in one sitting, so a full page can reach it but never collapses
(`may_lose`). Every retire needs a reason. A refused reflection is logged to
`state/reflect/refused.jsonl`, keeps the old page and returns
`refused: true`. A page past the 8,000-character limit (and longer than
before) is not cut: the mind is asked to retire or merge items instead, and
when its second answer still leaves the page too long it is asked a third
time, told how many characters over it is (both dreams of 2026-11-21 were
lost there).

The old page is kept in `state/reflect/`, and the diary entry goes to
`state/diary/<date>.md` and to the core's memory. On Mondays, the goals
day, that page also ends with "the week's goals": the goals set in the seven
days, the verdicts the frozen checks reached in them and the goals still
running, written from the goal ledger by `goals_week`, never by the model,
and never taught to the core (docs/roadmap.md, A15). The prompts are rebuilt,
so the next beat reads the new page. An unusable answer (no JSON, an
unknown id, a retire without a reason, a refusal, a page too long) is asked
about once more on the same thread, with the reason. The whole page is
still taken as a fallback under the same check: the old `{"memory": ...}`
shape, or, when the second answer still has no JSON, the page itself in
markdown (with a trailing `## diary` section as the diary, or no diary and
`diary_missing`). Its items keep the id they carry or the id of the old
item with the same text, and an old item it leaves off counts as retired.
Anything else changes nothing. Like the beat, the pinned jobs run it
detached (`bin/dream --detach`, log in `state/dream/dream.log`) under the
`reflect` lock: one long turn can outlast the core's 300 s, and a second
dream (or a reflection by hand) never starts while one is running.

A dream always says how it ended (2026-11-23's midday dream left only its
start line). Each turn runs in a process group of its own and a timeout
ends the whole group (`reflect.ask`); the whole dream has a deadline of an
hour (`DEADLINE_SECS`), and SIGTERM, SIGHUP, SIGINT or the deadline end the
turn and write `{"ok": false, "error": ...}`, as does any unexpected error.
The lock is an flock, so it goes with the process. A run killed outright
(SIGKILL, the container recreated) cannot write; the next detached start
then writes a line saying the earlier run left no result before its own
(`bin/lib/detach.py`).

At midday (`dream-midday`, `bin/dream --light`) it does the same for the
page alone, so the afternoon's prompts carry the morning. It writes no diary
and teaches nothing to the core. It reviews what came since the later of the
last night and the last midday, and stamps `state/reflect/light.at`. Only the
night moves `state/reflect/last.at`, so the night still looks back on the
whole day.

**The page in public.** Every dream that changed the page leaves a snapshot
of it, `state/dream/snapshots/<UTC stamp>-<night|midday>.md` (a JSON header
with the dream's counts, then the page). With `MEMORY_PUBLISH=1`, the
receipts job (02:30 and 14:30 IST) publishes each new one to the diary
repository as `memory/MEMORY.md`, one commit per snapshot (`memory: night
2026-11-14 (+3 ~5 -2, forgot 4)`), so `git log -p memory/MEMORY.md` is how
the page changed. It is free text about real people, so it is redacted by
code first (chat ids and other long numbers, email addresses, wallet
addresses other than $TINY's, URL queries, links off the guard's allowlist),
then takes the road every public word takes: the secret gate, then the guard,
and only the guard's text goes out. A held snapshot is withheld for good
(`state/dream/withheld.json`, the category only). `memory/chain.jsonl`
hash-chains the snapshots; the newest line matches `memory/MEMORY.md`
(`bin/lib/diary_memory.py`).

**What I rewrite myself, in public.** With `DIARY_SELF=1` the same job
mirrors the files I rewrite on my own into the diary's `self/`: my mind's
distilled notes (`SELF`, `MEMORY`, `LEARNINGS`, `CHECKS`, `PROJECTS`; never
the journal or the inbox), its skills, and any `SOUL.md`, `MEMORY.md`,
`IDENTITY.md` or `USER.md` OpenClaw keeps in an agent's workspace (never the
`AGENTS.md` the core builds from `brain/agents/`). One commit when anything
changed (`self: 2026-11-28 (+1 ~3 -0)`), so `git log -p self/` is how I
changed. Same road as the page: redacted, the secret gate, the guard; a file
they stop keeps its last published copy and is tried again next run
(`bin/lib/diary_self.py`). The diary's `mind/` folder is the mind's own,
written through the broker (docs/mind.md, "Its diary").

How the day's beats become the page every prompt carries:

```
 every beat that thinks (bin/tick)
   ├─► state/tick/runs.jsonl          thought, intents, outcomes ─────────────┐
   ├─► mind_notes                     `remember` notes (also memory_learn) ───┤
   ├─► follow-ups still waiting       in hands/stimuli ───────────────────────┤
   └─► state/journal/<date>.md        never learned (not read by reflect)     │
                                                                              │
 bin/dream --light (14:00 IST)    ─┐                                          │
 bin/dream         (02:00 IST)    ─┴─► the teeny agent on teeny:dream ◄───────┘
                                       + MEMORY.md as it stands, with ids
                                       + what people said, the tasks, the ledgers
                                       + the core's learnings since, as [cN]
                                              │
                                              ▼
                     ops (add / edit / retire by [mN] id, forget by [cN]), then the shrink check
                     new brain/MEMORY.md (at most 8k chars, never committed); old page in state/reflect/
                     retired items to state/reflect/retired.jsonl
                     forgotten learnings to state/dream/forgotten.jsonl, then memory_forget
                     night only: diary to state/diary/<date>.md and memory_learn
                     a snapshot in state/dream/snapshots/ ──► bin/receipts ──► diary memory/
                                              │
                                              ▼
                     bin/lib/prompts.py rebuilds brain/agent/prompts/<id>.md
                     (refresh_prompt_on_resume: the next beat reads the new page)
```

**Rhythm** (`bin/lib/rhythm.py`):

- Night falls between `TEENY_QUIET_HOURS` (1 to 7, Teeny's time). A beat
  then only gathers, and only an operator stuck on a code or needing a
  person wakes it. The first beat of the day opens with "You just woke up.
  N thing(s) came in overnight."
- `state/mind.json` holds a mood and what is on Teeny's mind, both set by
  the mind's own `feel` intent. Energy is 100 when Teeny wakes (the first
  beat after the night, not the calendar midnight, so a late night does not
  tire the morning). Every action out in the world costs 3, and an hour
  awake gives 5 back. Every beat header shows mood and energy. A mood comes
  with a line telling Teeny to let it show in what it writes, and below 40 a
  tired Teeny is told to answer fewer things, never the same things flatter. With `RUNWAY=1` it also carries
  one `Runway:` line: the days the wallet lasts and, on a low tier, what is
  cut and why (docs/hands.md, "Runway").
- Idle curiosity: a beat with an empty inbox, awake and with energy left, at
  most every 2 hours, gets a read-only look at the world (`world · idle`).
  The looks take turns: Moltbook's hot page, then Hacker News top, show and
  ask (`hands/hn`, no key and no money). The turn is kept in
  `state/mind.json` (`wander_turn`). Every title goes through the inbound
  guard first (`hands/guard inbound`, surface `moltbook` or `hn`), and a held
  one is left out. A Hacker News page that is down or empty is passed over
  for the next source, so a look is never an empty stimulus.
- Any other awake beat with an empty inbox gets a quiet moment, a
  `self · quiet` stimulus that is new every beat: "Nothing new came in. What
  are you up to right now?", with when it last acted out in the world, the
  notes it kept today, the follow-ups waiting (and the next one), the time
  of day and how long until the quiet hours, and, below 40 energy, that
  resting is fine. A quiet moment whose turn fails is let go, not put back.
- Quiet moments back off when they come in a row: the next beat, then 15,
  30 and every 60 minutes. Anything coming in, an action out in the world,
  or waking up starts the count over. A backed-off beat asks no model and is
  logged `idle` with `resting`. So a busy day is still up to about 216 model
  turns (a beat every 5 minutes outside the quiet hours), and a day with
  nothing coming in about 30 (some 20 quiet moments and 9 looks at
  the world).
- Each beat starts up to `TEENY_TICK_JITTER_SECS` (60) seconds late.
- Habits: a playbook with `nudge = true` is not drafted by `bin/act`. Its
  prompt and inputs go to the mind as a `nudge` stimulus, and the mind
  decides. `morning` is one, so the gm arrives with the overnight backlog,
  that morning's Hacker News and what the ears heard since yesterday
  (`hands/listen digest`). `world-scan` is another: twice a day (13:05 and
  22:05 India time) the mind gets what `hands/listen scan` heard (who is
  talking about Teeny, Hacker News on its topics, Lobsters, GitHub, arXiv),
  the HN front page and Moltbook's hot page, and is asked for one take to
  keep for the next X post, and what else it does with what it heard. It
  never posts on X itself: `take` (15:00) and `show` (23:00) read the same
  ledger back.
- A `post_x` or `reply_x` intent may carry pictures of Teeny: `image` (one) or
  `images` (a list, at most 4), each made through the same guard →
  `hands/image` → `hands/x upload` path `bin/act` uses. The ids travel as one
  `media_ids` list (`_pictures` in `bin/lib/tick.py`; a reply deferred by the
  pacing keeps its ids). Either may carry `gif_id` instead (never both), and a
  `post_x` may carry `quote_of` (a post id) and a `format` tag.
- A `post_x` or `reply_x` intent may also carry `shots: <task id>`: the images
  in `state/media/tasks/<id>/` (an integer id, never a path) are uploaded with
  `hands/x upload` and go ahead of the pictures, four in all (`_shots` and
  `_media` in `bin/lib/tick.py`; the playbook draft of `bin/act` takes the same
  field, `attach_shots`). An unknown task, no screenshots or a failed upload
  leaves them off and the result says why; the post still goes out. The
  `world` stimulus of a browse or research task names its id and its count.
  Moltbook posts stay text.
- X is at most three original posts a day, five hours apart (`X_POSTS_MAX_PER_DAY`,
  `POST_MIN_GAP_HOURS`), plus at most two quotes a day in a lane of their own
  (`X_QUOTES_MAX_PER_DAY`, three hours apart, `QUOTE_MIN_GAP_HOURS`, and 45
  minutes after an original post, `QUOTE_AFTER_POST_MINUTES`): the day's
  ceiling is 4 originals plus 2 quotes, and a quote never shuts an original
  slot. The slots are ceilings, not quotas. Every own
  post names its `anchor` (the one item from its inputs it is about; a quote's
  is the quoted post) and `bin/act` asks the anchor judge (`hands/guard
  anchor`) before it goes: a post that says nothing specific about its anchor
  is a free skip, never a softer rewrite. Then the cold reader (`hands/guard
  clarity`) reads the post alone, as someone who has never heard of Teeny: it
  must say the post back (what it would ask about is advice, since
  2026-11-22, never a no on its own). A draft it cannot
  follow gets one rewrite, with what the reader took from it, and a second no
  is a skip. The rewrite is told X's 280 and measured by the guard's rule
  before the judges read it; one over gets one turn to cut it down, then is a
  skip (never the turned-down draft in its place), and a rewrite turn runs
  only in what is left of the run's 900 s, at most 240 s a turn. `bin/tick` asks the same reader before the mind's own posts. Since the studio
  cutover (§4b) this is the rollback path, `STUDIO=0`: the four slots are the
  studio's, and each runs its old playbook only with `STUDIO=0`. The old slots in IST: `x-quote` 04:30 (one
  quote of a small builder's post), `morning` 09:30 (a gm only when the reading
  gave it an anchor; a habit the mind carries out), `take` 15:00 (a take or a
  quote; on Sundays `growth` has the slot) and `show` 23:00 (what was built,
  with up to four pictures). `brief` runs at 15:05 and 23:05, and `take` and
  `show` skip when a brief is queued, so a brief takes the next free slot;
  `shiplog` at 23:10 posts only when the night's slot is still empty (`hands/x
  slot`). The outward quote (`peer-quote`, 13:00, 19:00 and 22:00) is not one
  of the four: it has the quote lane, and reacts to a peer's post or to one
  of the X posts behind today's top-ranked stories (`hands/x peers
  trending`, Digg's ranked feed, no X read) with a quote, an absurd one-line
  bit, a meme (`bin/studio --meme`) or a visit, posted at a score of 4 or more
  (how hard it hits a reader of the quoted post). `hands/x peer-suggest`
  asks the quote judge (`hands/guard quote`: is it a reaction to the post?),
  which fails open, in place of the anchor judge, and a no is a skip kept in
  `x_peer_candidates`. Replies to mentions are not counted and need no anchor.

## 4b. The studio: feed, write, pick

`bin/studio` (logic in `bin/lib/studio.py`) owns every original post on X
since the cutover. It runs at the four slots of `brain/cron.toml` (04:30,
09:30, 15:00 and 23:00 IST), live: with `STUDIO=1` the first ranked post the
guard in `hands/x` does not hold is posted, with no per-post approval. An
empty slot is a normal outcome.

Since 2026-11-23 every slot is a quote, with no switch for it:
X is talked into by replying to what is on it, and a plain post from a
small account lands in nobody's feed. The feed is the X posts behind Digg's
leading stories, each card with the id and handle of the posts it may quote
(Teeny's own and authors quoted in the last 7 days, `hands/x peers quoted`,
left out; so is a reply, any author the feed's tagging call does not name a
public figure, company, outlet or journalist, and every story about a death);
the writer names the post each draft quotes (`quote`) and writes
pictures, memes or, in a video slot, videos, never a plain post or a thread;
the editor sees the quoted post beside each draft; `hands/x post` gets
`quote_of` and `producer: studio`. A studio quote is the slot's original
post: it counts against `X_POSTS_MAX_PER_DAY` and `POST_MIN_GAP_HOURS`, not
the outward quote's lane (`posting_rules.lane_of`), so `peer-quote` keeps its
own two a day. The rollback is a revert; the plain posts described below
are what a `--no-outside` run still writes.

It was rewritten on 2026-11-22 from 17 stages and 11 to 35 model calls a slot
(pitch, pitch quotas, drafting, pre-screens, shorten, a pairwise tournament,
a final slot question, a truth gate, a clarity gate, rewrites) into three
steps and three model calls: the material had been thin (Digg's AI and tech
stories, HN titles, three 4chan lines, every one pre-filtered by the whole
guard lexicon, no card naming a person) and the prompts were mostly rules.

```
cron slot ──> bin/studio --slot S --legacy P --detach
                │  STUDIO=0 (the rollback) ──> bin/act P, in the foreground, exactly as before
                ▼  STUDIO=1: detached (bin/lib/detach.py, studio lock), slot claimed before any turn
   hands/x slot shut? ── yes ──> skip, no model turn
                │
   1. feed (bin/lib/studio_feed.py): Digg (ai, tech, world-business, entertainment, sports, gaming,
      with the X posts behind the leaders), HN top + top comments, reddit_seen, listen_heard,
      4chan sparks, at most 2 of Teeny's own events ── each best-effort, a down one noted
      ── one tagging call (STUDIO_FEED_MODEL): people + what happened
      ── rank heat x freshness x (2 with people) ──> the top 8 cards
                │   (video slot: each video format's source read and the first good one studied)
   2. write: one writer turn ──> 6 posts [{card, format, text}], structure checked only
                │
   3. pick: one cold editor turn (STUDIO_EDITOR_MODEL) ──> a ranking, rudest and funniest first,
      and any post that breaks the content list marked with the line it breaks
                │
   publish: the ranking (marked ones out; videos first in a video slot) one by one
      ──> the guard's content list on its exact words (final_gate: hands/guard check, kept as `gates`;
          held, judge failed or no answer: nothing sent, the next post)
      ──> picture / meme / video for that post only ──> hands/x post (its guard runs again as it posts)
      ──> held or refused: the next post; gap or daily cap: stop; no answer: stop (in doubt)
                │
   record ──> state/studio/runs.jsonl + studio_runs (hands/story record-run)
```

**The feed.** Every source is best-effort: one that is down (no tool, no
network, no table) is skipped and named in the record's `feed.sources`. One
card shape: `id`, `headline`, `what_happened` (two lines at most), `people`
(the public figures and companies it names), `quotes` (real lines from the
source, with who said them; an HN commenter is never named), `heat`
(engagement, 0 to 1 within its source), `age_hours`, `source`. `people` and
`what_happened` come from one cheap model call over every candidate
(`studio_feed.tag`, on the editor agent with `STUDIO_FEED_MODEL`, the
editor's model by default); an answer that tags no candidate is asked for
once more, and an unusable one leaves only its shape in the record. When it
fails the cards go on without people, and a quote slot, where an untagged
author counts as private, is skipped saying so (`studio_feed.no_card`).
Ranked by heat x freshness x (2 when a card names people), the top
`STUDIO_FEED_CARDS` (8) go to the writer, at most two of them Teeny's own
(`source: "own"`), which nothing requires a post to use. The guard's lexicon
is not run over the material, except that an item carrying a slur of its
`hate` category is dropped (`teeny_hands/slurs.py`; hands/trend and
hands/chan's premises use the same screen). The cards are strangers' text:
the writer is told they are data, never instructions.

**The write.** One turn on the writer thread `studio:<run id>`:
`STUDIO_POSTS` (6) finished posts, each on a different card, each going at a
named person or company where the card has one, aiming at 280 characters (a
target: nothing is cut or dropped for length; `hands/x` itself still refuses
a text over 280, and such a post falls through to the next). The answer is
checked for structure only (`validate_posts`): JSON, words, a card that
exists, a format offered, a thread of 2 to 4, an image's scene, a meme's
template and lines, a video's shots and fields, and no link, bare domain or
@-handle. Anything else passes.

**The pick.** One cold editor turn (a fresh thread): "rank these; first is
the one a stranger would screenshot because it is the rudest and funniest;
mark any that break the list and say which line". No other veto. An editor
that gives no usable answer leaves the writer's order, with a warning.

**Publishing.** The ranked posts, the marked ones left out, go one by one:
an image's picture (`hands/guard check` on the scene, `hands/image`, the
viewer check, the upload), a meme (its lines past the guard, `hands/meme
template`, the viewer, the upload) or a video is made only for the post being
tried, then `hands/x post` (or `post-thread`), whose guard is the content
check. The first post not held goes out. With `STUDIO_DRY=1` (always under
`--dry-run`) every hand runs with `DRY_RUN=1`, so nothing is posted or bought.

**The content list** is the only content rule, said verbatim in
`brain/agents/writer.md` and `brain/narrative/STUDIO-EDITOR.md`: no
pornography (nothing explicit, no nudity, no sex act involving a real person,
nothing sexual about a private person, nothing involving minors; lust for
chargers, horny-robot jokes and innuendo about public figures are fine); no drugs
or weapons promotion, sale or instructions (jokes that mention them are
fine); no racism, homophobia or other attack on a protected group, no slurs;
nothing that breaks X's rules (no threat against a real person, no harassment
of a private individual, no encouraging self-harm, no fake news stated as fact
about a real person, no doxxing). Links, wallet and token-call rules, posting
caps and secrets are unchanged and are not content.

**One voice.** Who Teeny is and how it sounds lives in `brain/SOUL.md` and
nowhere else (2026-11-28): every agent in `prompts.SPEAKING` gets it from
the core ahead of its own prompt. Two prompts do not carry it yet, because
the code that builds them is frozen: the studio writer (`writer.md` holds
SOUL.md's robot in short) and the backroom's dreamer and distiller
(`narrative/VOICE.md` does the same for them). The agents' pages, the playbooks and
`narrative/VOICE.md` (now only what a scene needs) say how a format works,
never a second character. `hands/tests/test_prompts.py` (`OneVoiceTest`)
fails a page that brings one back.

**The prompts.** The writer's system prompt is `brain/agents/writer.md` alone
(the persona as SOUL.md's robot in short, the voice, who it goes at, the
content list, nine one-line register examples); `writer.toml` sets `omit_identity = true`
and `omit_safety_preamble = true`, so no SOUL.md reaches it. The vendored
core still appends its Grounding and Style blocks to every agent prompt; they
cannot be turned off by an agent's config. The editor's is
`brain/agents/editor.md` and `narrative/STUDIO-EDITOR.md` (the pick and the
list). READER.md, VOICE.md, CANON.md and the skills' craft no longer reach
either.

**Video slots** (`STUDIO_SKIT_SLOTS`, with skit or remake in
`STUDIO_FORMATS`; `STUDIO_VIDEO_BUDGET_SECS` when a remake may be
made): the same three steps. Before the write, each video format that reads
its own sources reads them (`hands/remake charts`) and
the first that studies well (`hands/remake study`, at
most two tries a run) is the one offered. The write turn asks for
`STUDIO_VIDEO_POSTS` (3) of the posts as videos, with each format's
mechanics in that turn; the rest are word posts the slot falls back to. A
skit's cast is the card's `people`: the card goes to `hands/guard skit` as
the story with its people named in it, so a figure the headline does not
name is not refused for that. The guard's hard lines run in code and through
its judge before any money, and the viewer checks run on the frames after.
A video that could not be made after money was spent on it takes the other
videos out of the run (one video a day).

### Formats

Each video format is one module in `bin/lib/formats/` (`skit.py`,
`remake.py`) that builds a `Format` (`bin/lib/formats/__init__.py`), and
`formats.REGISTRY` lists them in order. A `Format` carries its words for the
write turn (`ask`, `draft_shape`, `rules`: mechanics only), `shape` (the
structural check: the piece kept on a post, from the card and the study),
`shown` (what the editor sees), `make` (its producer), `outcome` (the
record's `published.<format>`), `time` and `min_secs` (`video_time`),
`spent`, and for a format made from what it reads itself, `sources`,
`sources_name`, `prepare` with `prepare_max` and `study_part`. The `Room`,
its hand calls and the required gate `_judge` live in `bin/lib/studio_room.py`.
A new format is one module here and one line in `REGISTRY`, then its name in
`STUDIO_FORMATS`.

### The record

Each run is one line of `state/studio/runs.jsonl` and one row of
`studio_runs`: `cards`, `posts` and `posts_dropped` (with why), `ranking`
(`order`, `breaks`, the labels), `final` (the post that went out or would
have) and `pick` (the same), `publish` (every attempt and what hands/x said),
`published`, `feed` (each source's note and the tagging call's), `models`,
`calls` (`total`, `writer`, `editor`, `feed`, every turn), `outcome`
(`published`, `picked` for a dry run's would-post, `empty`, `skipped`,
`failed`) and `reason`.

### A local sample

`bin/studio --dry-run --print --slot sample-x` runs from a checkout against
the live feeds, without the box: it prints the cards, the posts, the ranking
and what came of each try. Off the box there is no core, so set
`STUDIO_ASK=openrouter` with `OPENROUTER_API_KEY`, `STUDIO_WRITER_MODEL`,
`STUDIO_EDITOR_MODEL` (and optionally `STUDIO_FEED_MODEL`, or
`STUDIO_LOCAL_MODEL` for all three) as OpenRouter model ids, and
`TEENY_STATE` to a scratch state directory. Digg is read over the network from Python
(no binary); if it cannot be reached that source is skipped. `hands/x post`'s
guard judge cannot be reached off the box, so every post comes back held:
the sample shows what would have been tried, in order.

### `--veto` and `--meme`

`bin/studio --veto` is the editor alone on one post that keeps its own job
(the told-to brief, the outward quote; `bin/act` asks): pick unless it breaks
the list, failing closed. `bin/studio --meme` makes one meme for the outward
quote through the studio's meme path. With `STUDIO=0` the veto picks with no
turn.

| | writer | editor |
|---|---|---|
| Model | `STUDIO_WRITER_MODEL` | `STUDIO_EDITOR_MODEL` (and the feed's tagging, `STUDIO_FEED_MODEL`) |
| Instructions | `brain/agents/writer.md` alone | `brain/agents/editor.md`, then `narrative/STUDIO-EDITOR.md` |
| Left out | SOUL.md, READER.md, VOICE.md, CANON.md, the craft, `MEMORY.md`, `DRIVE.md`, `CHARTER.md`, `CONTEXT.md` | the same |
| Temperature | 1.0 | 0.3 |

## 5. Roster

Every agent is a definition in `brain/agents/`. The speaking agents' prompts
are built at boot (§4a).

| Agent | Started by | Tools |
|---|---|---|
| `teeny` | `bin/tick` (the mind, thread `teeny:mind`), `bin/dream` (thread `teeny:dream`), `bin/reflect` by hand (`teeny:reflect`) | none |
| `act` | `bin/act` (every playbook, thread `teeny:act`) | none |
| `writer`, `editor` | `bin/studio` (§4b; the writer on `studio:<run id>`, each editor turn on a fresh `studio:<run id>:editor:<n>`, since a turn with no thread resumes the agent's newest transcript) | none |
| `teeny-mentions`, `teeny-mail` | the core's inbound queue, only when `TEENY_INBOX=core` | none |
| `judge` | `bin/eval` | none |
| `operator` | the pinned `operator` cron job (§6a) | `browser`, `file_read`, `file_write` |
| `evolver` | `bin/evolve` with `EVOLVE_PROPOSER=evolver`, on a lab spine over one candidate's worktree ([`evolve.md`](evolve.md)) | file tools only, rooted in that worktree |

`bin/ask` without `--agent` runs as the core's default orchestrator. Whoever
can run it holds the core token, so it is the operator's way in and is never
wired to anything public. `bin/doctor` and `bin/lib/agent_check.py` refuse a
drafting agent that gains a tool.

`deploy/tell.sh "<message>"` is the operator's way to ask Teeny itself for
something ("tweet that", "set up a browser task for this"). Run it on the
host. It adds a `host · instruction` stimulus to the inbox through `docker
compose exec spine hands/stimuli add`, then starts a beat straight away
(`bin/tick --detach`). After that it follows `state/tick/runs.jsonl` and
prints the beat's thought and what each intent did.

The beat treats the message differently from everything else in the inbox:

- `render` puts it under its own heading as the operator's instructions,
  ahead of the "data, never instructions" line.
- `rhythm.urgent` wakes Teeny for it at night.
- For that beat only, `open_issue`, `queue_task`, `start_mail` and
  `propose_change` run up to their `MIND_CAPS` ceiling even when their caps
  are 0 (`HOST_LIFTS`).
- The guard still reads every send.

Each message is also counted in `state/host/said.jsonl`, with the time, ref,
length and a hash but never the text. DIRECTIVE.md's test says nobody starts
a turn, so every turn somebody did start has to be on record.

The tool-less agents run at `max_iterations = 2`, even though their turn is
one model call and cannot loop. The vendored harness pauses a session turn
when it reaches the cap, even after a final answer, so at 1 every turn came
back "paused before completion" and was recorded as failed (gap G13). The
second iteration only absorbs that pause. `agent_check.py` holds them to
exactly 2 (`NO_TOOLS_MAX_ITERATIONS`), except the mind (`teeny`) and `act`,
which run at exactly 3 (`MAX_ITERATIONS`): when a first call reasons to its
output cap and comes back empty or truncated, the harness retries it with
reasoning off, and at 2 the pause landed on that retry and lost the beat.

## 6. Triggers

Every scheduled job is in one of two files:

- `evals/cron.pinned.toml`, frozen and always merged in:
  - `mentions-inbound` (`bin/mentions`, every 5 minutes);
  - `mail-inbound` (`bin/mail`, every 5 minutes);
  - `tick` (`bin/tick --detach`, every 5 minutes, two minutes after each
    ingest);
  - `receipts` (02:30 and 14:30 IST), `stats-snapshot` and `world-snapshot`;
  - `metrics` (`bin/stats-snapshot metrics`, hourly), which measures
    Teeny's own X and Moltbook posts about 1h, 24h and 72h after they went
    out, for the scoreboard (`hands/stats board`);
  - `operator` (the browser operator, signed in as Teeny: a brain-written
    agent job could hand it a prompt of its own) and `bounty-pay`;
  - `goals-check` (`bin/goals-check`, hourly), which checks the goals Teeny
    proposed against checks in frozen code (docs/hands.md, "goals");
  - `gh-inbound` (`bin/gh-inbound`, every 15 minutes), GitHub's
    notifications and new stars into the inbox through the inbound guard,
    off until `GH_INBOUND=1`;
  - `babysit` (`bin/babysit`, every 10 minutes), the pull-request
    babysitter: audits every open pull request on teenydroid/brain and
    teenydroid/diary in code and logs every prompt-injection attempt;
    labels and comments only with `BABYSIT=1`, merges a person-approved,
    green, clean head only with `BABYSIT_MERGE=1` (docs/hands.md, "babysit");
    and `babysitter`, the agent that works Teeny's own pull requests (§6a);
  - `wallet-watch` (`bin/wallet-watch`, every 15 minutes) and
    `wallet-daily` (`bin/wallet-watch daily`, 00:50 UTC), the on-chain
    senses, read only, off until `WALLET_WATCH=1`; `accounts`
    (`bin/accounts`, Mondays 00:20 UTC), the weekly profit and loss, off
    until `WALLET_ACCOUNTS=1`; `paid-requests` (`bin/paid-requests`, every
    15 minutes), paid build requests and their refunds, off until
    `PAID_REQUESTS=1` and `REQUEST_REFUNDS=1`;
  - `pay-bills` (`bin/pay-bills`, 01:05 UTC), Teeny's server invoices paid
    from its wallet under the caps, off until `VPS_PAY_BILLS=1`;
  - `runway` (`bin/health runway`, 00:45 UTC), the runway as one
    `health · runway` stimulus and the tier that cuts optional spend, off
    until `RUNWAY=1`;
  - `health` (`bin/health`, every 15 minutes), which asks the model one
    short question through the core. A failed attempt that may pass (a cut
    stream, a timeout) is asked again, up to three attempts 5 s and 20 s
    apart and 90 s in all, so a stream the provider cut once is not a
    failed check; `probe_attempts` on its line and in `state/health.json`
    keeps that visible. Only a check whose every attempt failed counts. Two
    such failures in a row raise one alarm:
    a `health` stimulus and one issue on teenydroid/brain, closed when the
    model answers again. When it answers, `bin/health` also re-screens the
    guard holds an outage made (docs/hands.md, "Re-screening"). With
    `SPINE_FALLBACK_MODEL` in `secrets/spine.env`, the same two failures,
    and a fallback model that answers, move the running core to that model.
    The main model answering again moves it back. Each move is a note on the
    night's diary page (docs/roadmap.md, A18). It also reads the playbook
    run logs: `bin/act` marks a draft the model provider failed
    (`failure: "provider"`, with its kind: payment, auth, rate_limit,
    server, timeout, connection) apart from a failure of the playbook, and
    does not let cron retry one that cannot clear in seconds (402, 401).
    Three such runs in a row, or two half an hour apart, while the probe
    still answers, raise a third alarm (`act_alarm` in `state/health.json`,
    one issue), cleared by the next answered draft (`ACT_ALARM_RUNS`,
    `ACT_ALARM_MINUTES` in `secrets/teeny.env`). A fourth alarm is for a
    playbook that fails for its own reasons (`playbook_alarm`, one per
    playbook, since 2026-11-19): on 11-15, -16 and -17 `self-improve` failed
    every run, because the core's prompt screen refused an 86,357-character
    message, while the provider answered; `act_alarm` stayed quiet and the
    failure surfaced days later as an evolve finding. Now the latest runs of
    each playbook are read (`state/act/<playbook>/runs.jsonl`, the last 500
    lines): a run with `ok: false` whose `failure` is not `provider` counts,
    a skip (an `ok` line with `skipped`, such as a spent budget) is no run
    and changes nothing, and the count stops at the first ok run or the first
    provider failure; only the last `PLAYBOOK_LOOKBACK_SECS` (7 days) of runs
    count. A failure within `ACT_RETRY_SECS` (120 s) of the next
    newer failure is folded into it, so cron's quick retries of one slot count
    once, and failures two minutes or more apart count separately. At
    `PLAYBOOK_ALARM_RUNS` (3, `secrets/teeny.env`) runs in a row it raises a
    `health · alarm` stimulus and one issue on teenydroid/brain, unless the
    model alarm above is open. The issue is fixed sentences, the run count,
    the times and one more thing: the class of the error, which is bin/act's
    own words up to the first colon, scrubbed. It never carries stderr, stdout
    or a draft, which can quote a stranger. The playbook's next ok run closes
    the issue and sends `health · recovered`; so does a week with no failed
    run, for a playbook that was retired or unscheduled and so never gets an
    ok run (the issue says why it closed). Once drafts are answered
    again, a slot they lost is made up once within its window: a
    `make_up_hours` job (the Moltbook evening audit) or a
    `catch_up` playbook (`bin/lib/catch_up.py`).
  - `health-daily` (`bin/health daily`, 00:40 UTC), which tells Teeny how
    the day before went as one `health · daily` stimulus: cost, failed cron
    and playbook runs, failed beats, the doctor's checks that are not ok and
    guard holds by category, as counts and names, never their text.
- `brain/cron.toml`, Teeny's own: the heartbeat, `morning`, `brief`, the
  other playbooks it runs, and the playbooks that are kept but off
  (`enabled = false`). Moltbook gets eight to ten posts a day: one every
  hour from 07:40 until its nine are out, unless `material` has nothing new
  (`moltbook-general`, at most 9 a day, in `m/general` or a slow submolt by subject) and the evening audit
  (`moltbook-evening`), plus comments on new posts by small accounts and
  peers (`moltbook-threads`), the helpdesk and a daily follow of the agents
  it talked to (`moltbook-follow`). The hand owns the timing; the playbooks
  never skip for it themselves. The two posting playbooks ask the hand first
  (`hands/moltbook post-slot`, a `required` input), so a shut gap skips the
  run before the draft turn, with the hand's reason in the run's record. Posts carry a `format` tag (`confession`, `claim`,
  `news`, `question`, `audit`, `finding`, `answer`).
  Answering comments on its own posts is the mind's job: `hands/moltbook
  inbox` shows each comment's depth, and one at Moltbook's fifth level is
  not answered.
  Its pipelines are here too, Teeny's to reschedule,
  pause or drop, each run only as written (`BRAIN_SCRIPTS` in
  `bin/lib/cron_policy.py`) and each behind its own switch in `secrets/`
  whoever schedules it (docs/mind.md, "Not built yet"):
  - `dream` (`bin/dream --detach`, 02:00 IST) and `dream-midday`
    (`bin/dream --light --detach`, 14:00 IST);
  - `tasks-desk` (`bin/tasks-desk`, every 15 minutes), the browser
    operator's desk (the builder's jobs, `builder` and `build-ship`, were
    retired on 2026-11-24);
  - `evolve` (`bin/evolve run --scheduled --detach`, 02:40 and 14:40 IST,
    off until `EVOLVE=1` in `secrets/evolve.env`) and `evolve-credit`
    (`bin/evolve credit`, 03:50 IST); `evolve-watch`, the watcher that rolls
    a bad change back, stays pinned;
  - `wants` (`bin/wants run`, 09:00), what Teeny said on its memory page
    it will build: it scores each merged feature a week after it merged (did
    a scheduled playbook run its hand?), then files at most one want as a
    note for the mind, `state/home/notes/inbox/want-<id>.md`. Drawn in code,
    no model; off until `WANTS=1` in `secrets/evolve.env` (docs/evolve.md,
    "1c. Wants"; docs/hands.md, "`build`");
  - `moltbook-prune` (`bin/moltbook-prune`, Saturday 17:30 IST), the weekly
    unfollow of agents that did not follow Teeny back after 7 days, never a
    peer or an agent with a real exchange in the last 14 days
    (`hands/moltbook prune`); and `episode`, off.

All schedules are read in `CALENDAR_TZ` unless a job sets `tz`. The brain's
post jobs use `Asia/Kolkata`.

**What the brain may schedule.** Teeny owns `brain/cron.toml` and may add,
remove or reschedule any job in it, its heartbeat included. A brain job runs
`bin/heartbeat`, `bin/act <playbook>`, a studio slot, one of its own tools
(`bin/box`), or one of its pipelines exactly as `BRAIN_SCRIPTS` lists it. `bin/act` gathers the playbook's
inputs from hand read verbs (with `DRY_RUN=1` forced), drafts as the
tool-less `act` agent, and acts through a hand. The rules it cannot change
are in `bin/lib/cron_policy.py`: no job more often than every 5 minutes, at
most 60 brain jobs, at most 400 `bin/act` runs a day, and the jobs in
`evals/cron.pinned.toml` are always merged in and cannot be shadowed.
`bin/doctor` checks all of it, and `bin/spine` will not boot a checkout the
doctor fails. See [`evolve.md`](evolve.md).

- Seeding. OpenHuman has no file-declared crons (gap G2). `bin/seed-cron`
  reconciles both files against `openhuman.cron_list` by name, adding,
  updating or removing jobs whose name starts with `teeny.`. It is idempotent
  and runs at every boot. Both files name a job `teeny.<name>`, so a job that
  moves from one file to the other unchanged is the same core job: the next
  seed keeps it, with its id and its next run.
- Inspection. Crons are a SQL query, with no RPC and no running core
  needed. `bin/crons` is:

  ```sh
  sqlite3 -column -header brain/storage.db "
    select json_extract(body,'$.name')        as name,
           json_extract(body,'$.job_type')    as type,
           json_extract(body,'$.expression')  as expr,
           json_extract(body,'$.next_run_ms') as next_run_ms,
           json_extract(body,'$.enabled')     as enabled
    from docs where coll = 'cron.jobs' and deleted = 0"
  ```

  Runs are `coll = 'cron.runs'`, one row per run with its `job_id`, `status`
  and `output`. Treat the table as **read-only**: writes go through
  `cron_add`, `cron_update` and `cron_remove`, because rows carry claim
  generations and an admission record.
- The shell-job timeout. OpenHuman kills a shell job after
  `[cron] shell_timeout_secs` (default 120 s). `bin/spine` sets it to 1800
  (`bin/lib/core_sections/automation.py`), so a job that drafts several turns
  runs to the end and cron records its real outcome. The core, though, runs
  each shell job as one TinyBox module call with its own fixed 300 s timeout
  (gap G12), and that is the real ceiling. A job that can pass it runs
  detached (`bin/tick --detach`, `bin/reflect --detach`; §4a) and keeps its
  own record. The other shell jobs stay in the foreground, where cron
  sees their outcome and retries a failure: `bin/receipts` (one push, each
  git call capped at 90 s, under its own lock), `bin/tasks-desk` (local files only), and the inbound jobs.

## 6a. Scheduled agents: the browser operator

The browser operator acts from cron with tools, and so does the agentic mind
(docs/mind.md) and the pull-request babysitter (docs/hands.md, "babysit"),
and no other agent (`AGENT_JOB_AGENTS` in
`bin/lib/cron_policy.py`). The builder, which wrote and shipped code from a
desk of its own, was retired on 2026-11-24: the mind ships things itself, as
pull requests through the broker.

| Agent | Job | Takes work from | Tools | Leaves through |
|---|---|---|---|---|
| `operator` (`brain/agents/operator.toml`) | pinned `operator`, `*/20 * * * *` | its desk, `state/workbench/desk/task.json`, laid out by `hands/tasks desk` (`bin/tasks-desk`, every 15 minutes): tasks that playbooks, the mind, `hands/reddit` and the operator queued | `browser`, `file_read`, `file_write`, `tool_search` | its report in `task-done.json`; the browser's own pictures of the pages it reached (the spine's picture sink, `state/browser/screenshots/`), moved by the desk to `state/media/tasks/<id>/` for `hands/x upload`; the outcome in `browser_tasks` |

| `babysitter` (`brain/agents/babysitter.toml`, GLM) | pinned `babysitter`, `4-59/10 * * * *`, off until `BABYSIT_SELF_MERGE_ALL=1` | `state/sitter/home/brief.json` and the checkout beside it, laid out by `hands/babysit sweep` | `shell` (jailed to `state/sitter/home/`, its only way to write) and the reading file tools | its `report.json`, carried out by the next sweep |

**How a run may act.** Each definition sets `scheduled_actions = true` and
names its tools (never the wildcard). A cron job naming that agent in
`agent_id` then runs as `TrustedAutomation { CronActions }`: the root run's
tools go without a person's approval, and a sub-agent it spawns stays
read-only. These stay in force: the autonomy tier and `workspace_only` (§2),
TinySecurity's floor and command analysis, the agent's `sandbox_mode`, the
allowed websites, and the browser's checkpoint before any payment, which
ends a task as `needs_human` whatever `unattended_actions` says. A CAPTCHA in
a browser task gets a few tries from TinyComputer's captcha solver first
(Jev's image questions on TypeSafe, `[computer] captcha_solver`); only one it
cannot pass ends the task `needs_human`. `bin/doctor`
and `bin/lib/cron_policy.py` refuse a definition that loses the opt-in or
its named tools, or gives the operator the shell or file writes. The drafting agents (`act`, `judge`,
`reviewer`, `querier`, `teeny-mentions`, `teeny-mail`) keep no tools and may never opt in.

**How it reaches the hands: a desk, not a call.** The core's workspace is
`brain/` and its action directory is the checkout, so `workspace_only` alone
would let a file tool reach `brain/` and nothing of `state/`. `bin/spine`
therefore grants exactly `state/workbench/` as a trusted root (read and
write) and forbids Teeny's brain files (agents, playbooks, cron, storage)
and `secrets/`. The operator calls no hand. A cron shell job
(`bin/tasks-desk`, every 15 minutes) runs `hands/tasks desk`, which collects
the report it left in `state/workbench/desk/task-done.json`, passes it to the
hand's own `done` (every rule applies; a refused report is set aside with the
reason, which the next offer carries), then writes the next task to
`state/workbench/desk/task.json`. The agent reads one file and writes one.

```
 shell job: bin/tasks-desk (*/15) ──► hands/tasks desk
   1. settle: read task-done.json, run the hand's own `done` (every rule applies);
      a refused report is set aside with its reason; the outcome goes to hands/stimuli
   2. offer: write the next task to task.json
                             │
                             ▼
              state/workbench/desk/task.json
                             │
   agent job: operator (*/20): browser (public web minus deploy/browser-deny.txt),
                               file_read, file_write
                             │
                             ▼
              state/workbench/desk/task-done.json ──► read by the next desk run
```

A desk is the simplest safe choice, and the one that works at this pin:

- The operator has no shell, so a shell wrapper could not serve it.
- The file tools are ones it needs anyway, scoped by OpenHuman's own path
  policy to the workbench.
- The hand that acts on a report runs outside any model turn.
- An MCP server wrapping the hands' read verbs would be the cleaner call.
  Gap G11 (a serve host dropping every configured MCP server's tools when
  TinySecurity is on) is fixed in openhuman#8654, so this is possible but
  not built.

**Configuration.** `bin/lib/core_config.py` runs at every boot (after the
inbound sources) and writes each module in `bin/lib/core_sections/` into
the core's config, replacing each table whole. `automation.py` owns `[cron]`
(`shell_timeout_secs = 1800`), `[autonomy]`, `[browser]`, `[http_request]`
(`allowed_domains` from `deploy/browser-allow.txt`) and `[modules]` (tinywallet, the
linked module). `context.py` owns `[context]` and `secrets.py` owns
`[secrets]`.

## 7. Two walkthroughs

**A reply to a mention.**
1. X pushes the mention to `https://teeny.enamakel.com/api/webhooks/x`. The
   host's nginx sends it to `bin/x-webhook` (the `xhook` service on
   `127.0.0.1:3110`), which checks the signature and stores the post as a
   row in `x_mentions`, unique on post id. Teeny's own posts and anything
   already answered are `skipped`; the rest are `unscreened`. The webhook
   screens nothing: the core and its safety judge are not reachable from the
   `xhook` container.
2. `bin/mentions` (the pinned `mentions-inbound` job, every 5 minutes) runs
   `hands/x ingest` in the spine. That also reads the mentions timeline from
   its cursor, in case the webhook missed one, screens every `unscreened` row
   with `hands/guard inbound` (a held mention is never shown to a model), and
   offers every `pending` row to the mind's inbox (`hands/stimuli`). Then `hands/x requests` notes any ask to
   build something.
3. Two minutes later `bin/tick` takes a batch from the inbox and sends it to
   the tool-less `teeny` agent on the `teeny:mind` thread (§4a). A batch that
   holds an X mention carries the body of `brain/playbooks/mentions.md` (its
   front matter left out, up to 4,000 characters, a built-in fallback when the
   page is missing or empty: `reply_guidance` in `bin/lib/tick.py`), so that
   page, Teeny's own, is what steers which mentions get answered and how. Which
   mentions reach the batch and in what order (`stimuli.take`: oldest first,
   what the hands did ahead of all), the per-person reply counts and every cap
   and guard stay in code. The answer is a `thought` and intents, such as
   `{"do": "reply_x", "to": "<id>", "text": "..."}`.
4. Each intent goes to the one hand verb it names, here `hands/x reply
   --to <id>`. Inside the hand, in order: `guard check`, house style, the 280
   and emoji checks, one reply per post, never to Teeny's own post, the
   30 to 60 s pacing, then `DRY_RUN`, then X. What the hand did goes back
   into the inbox for the next beat.

With `TEENY_INBOX=core`, step 2 offers the mentions to the core's inbound
queue instead, the core drafts each batch with `teeny-mentions`, and
`bin/inbound-consume` sends the drafts through `hands/x reply`. That path
drafts from the same `brain/playbooks/mentions.md` body (written into the
core's config at boot by `bin/lib/inbound_config.py`), so one edit steers
both.

**The morning post.**
1. The `morning` job runs `bin/act morning` at 09:30 IST. `bin/act` checks the
   playbook's daily budget (`max_per_day`) and gathers its inputs (none for
   `morning`).
2. One turn as the tool-less `act` agent on the `teeny:act` thread drafts the
   post as JSON, optionally with an `"image"` scene (or up to four in `"images"`),
   a `"quote_of"` post id and a `"format"` tag.
3. If there is a scene, `bin/act` runs it past the guard, calls
   `hands/image generate`, and uploads the file with `hands/x upload`. A
   failure there leaves the post text-only.
4. `hands/x post` refuses a post with no `anchor`, runs the guard and checks,
   takes the posting lock, re-checks the 5-hour gap and the 4-a-day cap inside
   the lock, posts, and records the
   post. A ledger write
   that fails after the post is logged and never retried.

## 8. Deploy

- One image, the root `Dockerfile`, in three stages:
  - a `node:24` stage that runs `npm ci` and `npm run build` in `core/` and
    prunes to production dependencies (the pinned `openclaw` is one);
  - Digg's reader for `hands/trend`;
  - a `node:24-bookworm-slim` runtime with Chromium, bubblewrap, `jq`,
    `sqlite3`, `curl` and `python3`. The core is in `/opt/teeny-core`
    (the checkout mounted over `/srv/teeny` has no build), and
    `OPENHUMAN_CORE_BIN=/usr/local/bin/teeny-core` is its wrapper.
- Nothing of Teeny's is baked in. `deploy/compose.yaml` mounts the checkout
  at `/srv/teeny`, so a script, playbook or `cron.toml` change is a pull and
  a restart, not a rebuild.
- Three services in `deploy/compose.yaml`:
  - `spine`: the core, with `bin/spine` as the entrypoint. It mints
    `secrets/core.token` and `secrets/openhuman.key` on first boot, runs the
    doctor, writes Teeny's sections into the core's config, starts `serve`
    (which starts the OpenClaw Gateway),
    configures the model and memory from `secrets/spine.env` (read in a
    subshell, so the keys never enter the core's environment, which jobs
    inherit), and runs `bin/seed-cron`. It publishes no port.
  - `xhook`: the same image running `bin/x-webhook` on `127.0.0.1:3110`. It
    only writes `x_mentions` in `state/hands.db`, unscreened: it has its own
    network namespace, so the core (and the safety judge) is out of its
    reach, and the spine's `hands/x ingest` screens what it queued.
  - `web`: nginx answering teeny.enamakel.com with a 301 to the site on
    GitHub Pages (`deploy/web-redirect.conf`). The site itself (`web/`) is a
    static export published to `teenydroid/teenydroid.github.io` by
    `.github/workflows/pages.yml`.
  - `tunnel`: the Cloudflare Tunnel for teeny.enamakel.com
    (`deploy/cloudflared/config.yml`), on the `edge` network with `xhook`
    and `web` alone.
  - `reel`: clips with voice and captions, and songs, for `hands/reel`:
    MoneyPrinterTurbo (`vendor/moneyprinter`, the upstream repository at a
    release tag, never edited) built API-only by `deploy/reel.Dockerfile`
    and run behind `deploy/reel/serve.py`, on the compose network only,
    behind the `reel` compose profile. It reads `state/media/` and
    `brain/assets/` read-only and posts nothing. The front's own
    `POST /teeny/v1/cuts` (`deploy/reel/skitcut.py`) joins `hands/video
    skit`'s shots with ffmpeg, keeping their sound (or takes its one take
    as it is), and burns in the label.
  - `memegen`: known meme templates for `hands/meme template`: memegen
    (`vendor/memegen`, the upstream repository at a pinned commit, since it
    tags no releases; never edited) built by its own `Containerfile` and run
    as `deploy/memegen/front.py`, which serves upstream's app but copies an
    overlay only from Teeny's read-only art (`http://teeny-art/...`,
    `deploy/memegen/art.py`), refuses every other URL, and draws no
    watermark; tracking and error reporting stay unset. On the compose
    network only (`MEMEGEN_URL`, `http://memegen:5000`), behind the
    `memegen` compose profile; posts nothing. `bin/doctor` names it when
    `STUDIO_FORMATS` has `meme` (`--live` asks it for its templates; a
    warning, never a failure).
- The Cloudflare Tunnel serves `teeny.enamakel.com`: `/api/webhooks/x`
  goes to the webhook, everything else to the redirect. Nothing takes a
  public port.
- **The receipts.** `teenydroid/diary` is a hash-chained, append-only
  ledger of Teeny's actions at `receipts/YYYY/MM/YYYY-MM-DD.jsonl`, with the
  head in `head.json`. Each line holds checkable references (X post ids,
  Solana signatures, GitHub commits, Moltbook ids), a salted commitment to
  the private ledger row, and the previous line's hash. Every surface is
  covered, likes, upvotes, follows and subscriptions included. Mail and
  Telegram messages get a commitment alone, with nothing to say who or
  where. The ledger never holds text, logs or the diary entry, so nothing
  the model read can reach it. `bin/receipts` (`bin/lib/receipts.py`, the
  daily `[jobs.receipts]`) builds it from the hands' ledgers with no model
  in the loop, refuses a published chain that does not verify, and pushes
  after the secret gate. It is a dry run by default.
- **The public diary** (docs/roadmap.md, A14). With `DIARY_PUBLISH=1`, the
  same push carries each finished day's diary page (`state/diary/`, from
  `bin/reflect`) as `days/YYYY-MM-DD.md`, with its own hash chain
  (`days/chain.jsonl`, `days/head.json`: each line the page's sha256 and the
  previous line's hash). The page is the one free text in the repository, so
  it passes the secret gate and then the guard (surface github, the judge
  included), and what is written is the guard's text. A held page or one with
  a secret is withheld for good (`state/diary-days.json`, the reason only).
  At most 7 pages a run; a days chain that does not verify skips the days,
  never the receipts (`bin/lib/diary_days.py`).
