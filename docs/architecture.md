# Teeny on a stock OpenHuman: spine, hands, brain

Status: **the repository is this design.**
- **Done:** the Rust bot, the TypeScript service and the OpenCompany brain are
  gone. `teeny-spine` (`spine/`) builds OpenHuman's host from the vendored
  library, boots from `brain/`, and runs operator jobs (`bin/`,
  `Dockerfile`, `deploy/compose.yaml`).
- **To come:** the hands and the jobs that act.
- **Pre-rewrite paths:** references below to `crates/...` and `legacy/...` are
  to the code as it stood at `55c2c75`, before the rewrite. The invariants in
  `hands.md` come from there. This replaces the Rust workspace
(`crates/`, about 105k lines with tests) and the TypeScript service (`legacy/`)
with three things:

| Layer | What it is | Where it lives |
|---|---|---|
| **Spine** | `teeny-spine`, OpenHuman's own command-line host (`openhuman_rpc::host::cli`, what upstream's `openhuman-core` binary is), built here from `vendor/openhuman` at a pinned `upstream/main` commit and run as `serve`. It provides the agent loop, model routing, cron, memory (CortexDB), browser, shell, the Telegram channel, hooks and JSON-RPC. No Teeny behaviour in it. | `spine/`, `vendor/openhuman` |
| **Hands** | Small executables, one per outside capability. They are the only code Teeny owns. Every hard rule (gap, budgets, filter, dry run) is enforced inside a hand, never in a prompt. | `hands/` |
| **Brain** | Identity, skills, agent definitions, job prompts, schedules and the record. Memory is OpenHuman's CortexDB engine. All of OpenHuman's own state is in one SQLite file. | `brain/` |

Plus `bin/`, which holds the bash that wires the three together, and `web/`,
which is unchanged and reads `brain/record/`.

OpenHuman references below are `upstream/main` at `6741ea4b41` unless they
say `b56f31fb7f` (the bump that brought the inbound event queues), with
`$C = crates/openhuman-core/src`. Upstream's gaps are in
[`oh-gaps.md`](oh-gaps.md) and the hands' verbs are in [`hands.md`](hands.md).

---

## 1. The one rule the design is built around

OpenHuman refuses any tool with an external effect in a turn that cron
started, and it checks this before every approval setting:

> "A scheduled prompt is data, not an open-ended approval for later actions.
> Keep cron agent turns within a read-only tool capability."
> (`$C/agent/tinyagents/host/security_gate.rs:532`; again in
> `$C/security/approval/gate_intercept.rs:62`)

The Rust bot reaches the same conclusion for its own reasons
(`crates/teeny-agent/src/posture.rs:17-23`):

> "Every belt but the Operator reads text a third party wrote … and can be
> started by a cron or the public, so none of them holds a posting tool or
> `remember`: an instruction injected into something it read has nothing to
> write with. They produce text, and host code checks it and posts it."

So the design keeps that shape and replaces only the "host code". **Models
draft, and operator-written scripts act.** In concrete terms:

- **Every scheduled job is a cron *shell* job that runs `bin/job <name>`.**
  OpenHuman runs a shell job's stored command directly. It isn't a model
  turn, so the read-only rule doesn't apply. The command was written by the
  operator and recorded when the job was seeded (`$C/cron/README.md`,
  "shell jobs").
- **The script asks a locked-down agent for a draft** over the server's
  JSON-RPC (`openhuman.agent_chat` with an `agent_id`; see
  `$C/inference/host_runtime/schemas.rs:122`). The agent's belt is read-only:
  memory recall, X reads through a read-only hand verb, web search and vision.
  It returns JSON.
- **The script then calls the hands that act.** That's
  `hands/guard check | hands/x reply …`. Each acting verb runs the guard and
  the cadence claim *itself*, in the same invocation, so a script that forgets
  them still can't post unchecked.
- **No public agent holds `shell`.** Agents that read third-party text (reply,
  chat, room, drafter) have shell off their `tool_allowlist`. Only the
  operator agent has it, and only bearer-authenticated `bin/ask` turns run it.
  Even then, `[autonomy] allowed_commands` limits it to `hands/*`, and a
  `beforeShellExecution` hook refuses anything else.

This needs no change to OpenHuman's security model. The few upstream changes
that would make it nicer are listed in `oh-gaps.md`.

**The one exception: the scheduled agents.** Two jobs need a model in the
loop that acts: the builder writes and tests code, and the browser operator
clicks through websites. OpenHuman lets exactly that, per agent: a cron job
that targets an agent whose definition sets `scheduled_actions = true` and
names its tools explicitly runs as a `CronActions` turn and may use those
tools without a person's approval (§6a). Nothing else is lifted, and nothing
public talks to these agents: their work comes from queues the hands keep,
and what they make leaves only through a hand.

## 2. Spine

One process, `openhuman-core serve`, running as a non-root uid in the
container. Configuration:

| Concern | How | Reference |
|---|---|---|
| Home in the repo | `OPENHUMAN_WORKSPACE=/srv/teeny/brain`, with **no `config.toml` inside it**. OpenHuman then uses `brain/` as its files directory (SOUL.md, skills, agents) and keeps its config at `/srv/teeny/.openhuman/config.toml`, which is gitignored. The core writes it itself, and `bin/spine` sets the model and memory over RPC at every boot, so there is no template to render. OpenHuman takes the sibling `.openhuman/` for its config only when the folder is named `workspace` or that config already exists; otherwise, and whenever `config.toml` is put inside `brain/`, it treats `brain/` as the home and nests a second `brain/workspace/`. So `bin/spine` writes an empty `.openhuman/config.toml` before the first boot (an empty file is the core's defaults) and refuses to start with a `brain/config.toml`. Both behaviours were measured on a build of `6741ea4b41`. There is no `--home` flag. | `$C/config/README.md` |
| One state file | `OPENHUMAN_STORAGE_URL=sqlite:/srv/teeny/brain/storage.db`. This holds sessions, threads and memory state. Every store is a row in one `docs` table (`scope, coll, id, body` JSON), and crons are `coll = 'cron.jobs'` and `'cron.runs'` (`cron_jobs` and `cron_runs` before the v2 schedule store; measured on a build of `b56f31fb7f`, §6). | `$C/storage/config.rs:95`, `$C/cron/documents_port.rs:44-53` |
| Tool working dir | `OPENHUMAN_ACTION_DIR=/srv/teeny`, so `hands/*` resolves | |
| RPC auth | `OPENHUMAN_CORE_TOKEN` from a file. `bin/oh` reads it, and so does every script. | `$C/core/auth.rs:70` |
| Model | The core's `local-openai` runtime, which needs no TinyHumans account. A custom cloud provider set through `inference_url` does need one: every turn fails `SESSION_EXPIRED` without a sign-in (measured). `bin/spine` exports `LOCAL_OPENAI_URL=$LADDER_URL`, stores the key with `config_update_local_ai_settings`, and pins every workload to `local-openai:$SPINE_MODEL` with `config_update_model_settings`. The core never asks a `local-openai` endpoint for the model's context window and budgets an unknown model at a few thousand tokens, less than its own system prompt, so the trim drops the user's message (measured: the model answered an empty turn). `bin/spine` therefore registers the window with `inference_update_model_settings` (`model_registry`), from `SPINE_CONTEXT_WINDOW` or the endpoint's `/models` `context_length` | OpenHuman `docs/gitbooks/en/features/cloud-deploy.md`, "Headless without a TinyHumans account" |
| Memory | `[memory] engine = "cortexdb"` and `[memory.engines.cortexdb] endpoint`. The key goes in the keyring via `openhuman.memory_engine_set`. Recall is injected into every turn. | `$C/config/schema/memory.rs` |
| Keyring | **File store**: `OPENHUMAN_KEYRING_BACKEND=file` and `OPENHUMAN_KEYRING_MASTER_KEY_FILE=/srv/teeny/secrets/openhuman.key` (64 hex characters, mode 0400, outside `brain/`). Every secret OpenHuman holds (the CortexDB key, the model key) is a row in `storage.db` (`coll = 'secrets'`, encrypted `enc2:` ciphertext) under that key. Measured: the core logs "master key loaded from OPENHUMAN_KEYRING_MASTER_KEY_FILE", and the CortexDB key survives a restart. Without the variable it looks for a host source and then a brain key file. Unset, the backend is `memory`. Back up `storage.db` and the key file together. | `$C/security/secret_storage/store.rs:189-197`, `encrypted_file_backend.rs:31-35` |
| Persona | `brain/SOUL.md` and `IDENTITY.md`, read every turn | `$C/prompts/workspace_files.rs` |
| Agents | Custom agents with `tool_allowlist`, a system prompt and a model. The core loads `brain/agents/*.toml` itself at boot (`$C/agent_registry/host_definition.rs`); nothing seeds them | `$C/agent_registry/types.rs:76` |
| Shell limits | `[autonomy] enabled = true`, `level = "full"`, `workspace_only = true`, Teeny's own brain files and `secrets/` in `forbidden_paths`, and `state/workbench/` the one trusted root (read and write). `full` keeps the cron shell jobs (`bin/<job>`) running as before; what an agent may do is bounded by its named tools, the workspace scoping and the hands. Written every boot by `bin/lib/core_sections/automation.py` (§6a) | `$C/config/schema/autonomy.rs` |
| Hooks | `brain/hooks.json`: `beforeShellExecution` runs `hands/policy shell` for the operator's shell. Inbound screening is **not** a hook: `beforeSubmitPrompt` fires only on the web-chat path (`$C/web_chat/ops/start_chat.rs:313`), not on `agent_chat`, so `bin/job` calls `hands/guard inbound` itself before every draft | `vendor/tinyagents/crates/tinyagents-hooks/src/command/types.rs` |
| Browser | `[browser] enabled`, `headless`, `chrome_path` (Chromium in the image), `profile_mode = "persistent"` with the profile and downloads under `state/browser/`, and `unattended_actions` for the operator's clicks and typing. The websites are `[http_request] allowed_domains`, from the reviewed `deploy/browser-allow.txt`. TinyComputer is linked into `teeny-spine` (`spine/Cargo.toml`, `[modules] overrides` with `source = "linked"`), as is tinywallet for the `web3` domain | `$C/config/schema/tools/browser.rs`, OpenHuman `docs/gitbooks/en/developing/tinycomputer-docker.md` |
| Telegram | See §6. The channel is not enabled until gap G3 lands. | |
| Errors | `OPENHUMAN_CORE_SENTRY_DSN` | |
| Traces | `[observability.agent_tracing]` writes NDJSON to `brain/.traces/` (gap G5) | |

Not used: desktop control (macOS and Windows only), flows, Composio, and
OpenHuman's own `http_request` (it can't keep a secret out of the model's
view). MCP is not used yet: `hands/mcp` (§5) is unblocked since gap G11 was fixed upstream (openhuman#8654) but not built.

## 3. Hands

[`hands.md`](hands.md) has every verb. The contract:

- `hands/<name> <verb> [--flag value …]`, with an optional JSON body on stdin.
  The output is exactly one JSON object on stdout. Exit `0` means ok, `2`
  means refused by a rule (the body says which), and anything else is an
  error.
- **Secrets never reach the model.** A hand reads `/srv/teeny/secrets/*.env`
  itself. That directory is outside `brain/`, mode 0400, and owned by the
  service uid.
- **Jobs find everything from the repository root.** Cron shell jobs run from
  `OPENHUMAN_ACTION_DIR` (the checkout) with an allowlisted environment:
  `HOME`, `PATH` and `LANG`, with no `OPENHUMAN_*` or `TEENY_*` (measured). So
  a job locates `state/` and `secrets/` from where it runs, reads the core
  token from `secrets/core.token`, and reads the core's URL from
  `state/core.url`, which `bin/spine` writes at boot.
- **State lives in `/srv/teeny/state/hands.db`** (SQLite, WAL), separate from
  OpenHuman's `storage.db`, so a spine upgrade or reset never touches the
  mention queue or the post ledger. Every rule that spans calls is a
  transaction there, and the posting path also holds an `flock`.
- `DRY_RUN=1` is the default for every acting verb. Going live is
  `secrets/teeny.env: DRY_RUN=0`, never an argument the model could pass.
- Language: bash with `curl`, `jq` and `sqlite3` by default. A hand may be one
  small compiled helper where bash is the wrong tool: X OAuth 1.0a signing,
  media upload, SVG→PNG. That's a single file, outside `crates/`, with no
  OpenHuman dependency.

The hands are `x`, `guard`, `cadence`, `market`, `chart`, `media`, `web`,
`record`, `telegram`, `policy` and `mcp`.

**Memory captures.** After a post, the hand files what Teeny said and what
it answered into memory through the core's RPC: `openhuman.memory_learn`, or
`memory_brain_ingest` for long text. This replaces the host captures that
needed the `embed-memory-store` vendor patch (#7486). Whether these keep the
current labels and per-person keys is still unproven (gap G10).

## 4. Brain (`brain/`)

```
brain/
  SOUL.md  IDENTITY.md  CONTEXT.md     identity and the facts it may treat as known
  skills/<name>/SKILL.md               humanizer, humor, conversation, video-script (exist),
                                       reply, morning, chart, moon, lore, quote, article,
                                       serial, backrooms, critic, learnings (ported)
  agents/<id>.toml                     roster (§5); the core loads them at boot
  jobs/<name>.md                       the drafting prompt each job sends
  cron.toml                            schedules; seeded by bin/seed-cron
  hooks.json                           shell policy for the operator
  record/                              journal.jsonl, posts.jsonl, brief.md, holds.jsonl
                                       (written by hands/record; read by web/ and by jobs)
  # gitignored: storage.db* (OH state and encrypted secrets), .browser/, .traces/, logs/
# beside it, also gitignored: /.openhuman/ (rendered config), /secrets/ (master key, hand env files), /state/ (hands.db)
```

`narrative/`, `assets/` and `calendar.json` move under `brain/` as they
are. The calendar items become `cron.toml` entries.

**Self-improvement** is Teeny's own: it edits its brain (`SOUL.md`,
`CONTEXT.md`, the lore, skills, playbooks, `cron.toml`) and adds hands,
driven by the goals in `narrative/LORE.md` and tracked in
`narrative/ARCS.md`. A candidate brain is judged against the current one and
merged only if it wins, behind rules in code the brain cannot edit. The spec
is [`evolve.md`](evolve.md).

### 4a. Context and memory: what a turn knows

A turn that speaks as Teeny (`act`, `teeny-mentions`, `teeny-mail`) is
handed four things beyond the message:

- **Its prompt.** The agent's instructions (`brain/agents/<id>.md`) and then
  "# your world": `CONTEXT.md`, the parts of `narrative/LORE.md` Teeny reads
  (the premise, the cast, the running bits; never `## latent`, which is the
  producer's), `narrative/ARCS.md`, and last `MEMORY.md`. `bin/spine` builds
  it into `brain/agent/prompts/<id>.md` at every boot (`bin/lib/prompts.py`).
  The agents set `refresh_prompt_on_resume`, so a rebuilt prompt reaches a
  thread already under way.
- **`MEMORY.md`.** Teeny's own page of things to keep in mind (people,
  promises, what it is in the middle of, what it decided), capped at 12k
  characters. It is the persistence that survives anything; no agent's file
  tool can write it (`BRAIN_FORBIDDEN`).
- **Its thread.** `bin/act` drafts every playbook on one thread,
  `teeny:act`, so a draft sees the drafts before it, and the core recalls
  memory before the turn and logs it after (both are skipped on a turn with
  no thread). The evals draft each sample on a fresh `eval:<uuid>` thread
  and stay blind to it.
- **Recall and the memory block.** `[memory.recall] budget_tokens = 3000`
  before each turn, and a 1500-token `memory_block` at each new generation.

A thread folds at **100k tokens** (`[context] compaction_trigger_tokens`,
`bin/lib/core_sections/context.py`): the core seals the generation and opens
the next with a free-form summary of it and the last 20k tokens verbatim.
The sealed generation stays on disk.

**One mind.** Every surface writes to one inbox (`hands/stimuli`):

- X mentions (`hands/x ingest`, the webhook);
- mail (`hands/mail ingest`);
- comments said to Teeny on Moltbook, which `bin/tick` browses at most every
  10 minutes (`hands/moltbook inbox`);
- the builder's and operator's reports (their desks).

The pinned `tick` job (`bin/tick`, every 5 minutes) takes a batch and sends
it to the tool-less `teeny` agent on one long thread, `teeny:mind`. The
agent answers with a `thought` and JSON intents (`reply_x`,
`comment_moltbook`, `reply_mail`, `comment_gh`, `remember`, `follow_up`,
and the rest). Each intent goes only to the hand verb it names, and that
hand keeps its own guard, caps and `DRY_RUN`. What each hand did comes back
as the next beat's first stimuli. `remember` keeps a note (`mind_notes`) and
teaches the core's memory (`memory_learn`). `follow_up` sets a reminder that
returns when due. `TEENY_INBOX=core` restores the old per-surface path.

**Reflection.** Once a night (the pinned `reflect` job, 02:00 IST),
`bin/reflect` asks the mind, on `teeny:mind` with the day still in context,
to rewrite `MEMORY.md`. It works from what the mind chose to remember
(`mind_notes`), what it thought and did each beat (`state/tick/runs.jsonl`),
the follow-ups still waiting, and the page as it stands. The old page is
kept in `state/reflect/`, and the diary entry goes to `state/diary/<date>.md`
and to the core's memory. The prompts are rebuilt, so the next beat reads the
new page. An unusable answer changes nothing.

**Rhythm** (`bin/lib/rhythm.py`):

- **Night.** Between `TEENY_QUIET_HOURS` (1–7, Teeny's time) a beat only
  gathers. Only an operator stuck on a code or needing a person wakes it.
  The first beat of the day opens with "You just woke up. N thing(s) came in
  overnight."
- **Mood and energy.** `state/mind.json` holds a mood and what is on Teeny's
  mind, both set by the mind's own `feel` intent. Energy starts at 100 each
  day and every action costs 6. Every beat header shows them, and a tired
  Teeny is told doing less is fine.
- **Idle curiosity.** A beat with an empty inbox, awake and with energy left,
  at most every 2 hours, gets a read-only look at Moltbook's hot page
  (`world · idle`).
- **Jitter.** Each beat starts up to `TEENY_TICK_JITTER_SECS` (60) late.
- **Habits.** A playbook with `nudge = true` is not drafted by `bin/act`. Its
  prompt and inputs go to the mind as a `nudge` stimulus, and the mind
  decides. `morning` is one: the gm arrives with the overnight backlog.
- **Pictures.** A `post_x` intent may carry an `image` of Teeny, made through
  the same guard → `hands/image` → `hands/x upload` path `bin/act` uses.

## 5. Roster

This keeps the current posture (`crates/teeny-agent/src/posture.rs`) and
expresses it in agent definitions. Nothing public can write.

| Agent | Started by | Belt | Shell |
|---|---|---|---|
| `teeny-reply` | `bin/job reply` | memory recall, `hands/x` read verbs (`get`, `thread`, `user`, `search`) through `hands/mcp`, web search, vision, gif search, market reads | no |
| `teeny-chat` | `bin/job telegram` | same as reply | no |
| `teeny-drafter` | slot jobs | reply's belt plus `chart render`, `media gen` (the job script attaches the result by id) | no |
| `teeny-room-<persona>` | `bin/job backrooms` | recall, web search, X search, `browser` (navigation and reads only, behind `BROWSER_RESEARCH`; a click needs a live approval that a script turn can't give) | no |
| `teeny-learner` | `bin/job learnings` | memory recall | no |
| `teeny-operator` | `bin/ask` (operator only) | drafter's belt plus `shell` limited to `hands/*` | yes |

Read-only hand verbs reach no-shell agents as **one MCP server**,
`hands/mcp`, a stdio wrapper whose tool catalog is only the **R** verbs in
`hands.md`, fixed in the repo. Drafting turns are started by `bin/job` over
RPC (`agent_chat`), not by cron, so the cron rule doesn't apply to them. What
keeps them safe is structural: the server has no acting verb to call, and each
agent's `tool_allowlist` names only its read tools. OpenHuman can't see that
these tools are read-only, because an MCP call always registers as `Execute`
with no external-effect distinction (`$C/tools/impl/network/mcp.rs:319`).
That's gap G4.

## 6. Triggers

| Job | `cron.toml` schedule (defaults today) | `bin/job` does |
|---|---|---|
| `mentions-inbound` (pinned, built) | `*/5 * * * *` | `bin/mentions`: `hands/x ingest` offers new mentions to the core's inbound queue, which drafts each debounced batch with `teeny-mentions`; `bin/inbound-consume` sends the drafts through `hands/x reply` (§7) |
| `mail-inbound` (pinned, built) | `*/5 * * * *` | `bin/mail`: `hands/mail ingest` screens new mail and offers it to the core (source `mail:inbox`, one conversation per thread), which drafts with `teeny-mail`; `bin/inbound-consume --source mail:inbox` sends the drafts through `hands/mail reply` |
| `stats-snapshot` (pinned, built) | `15 0 * * *` | `bin/stats-snapshot`: `hands/stats snapshot`, today's X, Moltbook and GitHub numbers into `stats` |
| `heat` | `*/20 * * * *` | `hands/cadence heat` |
| `cortex-health` | `17 * * * *` | `hands/record health` (CortexDB readiness into `record/`) |
| `learnings` | `0 5 * * *` | draft with `teeny-learner` → `hands/record brief` |
| `morning` | `0 7 * * *` | `hands/cadence claim morning` → drafter stages → `hands/x post` |
| `afternoon` | `0 15 * * *` | `hands/cadence slot afternoon` picks chart, moon or lore → stages → post |
| `evening` | `0 23 * * *` | slot picks article, serial or quote → stages → `hands/x article` or `post` |
| `telegram` | `* * * * *` | `hands/telegram poll` → draft with `teeny-chat` → `hands/guard check` → `hands/telegram send` |
| `cal-<item>` | per item | as calendar items do today |

All schedules are read in `CALENDAR_TZ`, matching today.

**What the brain may schedule.** Teeny owns `brain/cron.toml` and may add,
remove or reschedule any job in it, its heartbeat included. A brain job runs
`bin/heartbeat` or `bin/act <playbook>`: `bin/act` gathers the playbook's
inputs from hand read verbs (with `DRY_RUN=1` forced), drafts as the tool-less
`act` agent (`brain/agents/act.toml`), and acts through a hand. The rules it
cannot change are in `bin/lib/cron_policy.py`: no job more often than every 5
minutes, at most 24 jobs, at most 400 `bin/act` runs a day, and the jobs in
`evals/cron.pinned.toml` are always merged in. `bin/doctor` checks all of it,
and `bin/spine` will not boot a checkout the doctor fails. See
[`evolve.md`](evolve.md).

- **Seeding.** OpenHuman has no file-declared crons (gap G2). `bin/seed-cron`
  reconciles `cron.toml` against `openhuman.cron_list` by name, adding,
  updating or removing jobs. It is idempotent and runs at every container
  start.
- **Inspection.** Crons are a SQL query, with no RPC and no running core
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

  On a fresh core this lists the job `bin/seed-cron` added beside OpenHuman's
  own `memory_background` and `memory_sources_sync` system jobs. Runs are
  `coll = 'cron.runs'`, one row per run with its `job_id`, `status` and `output`.
  Treat the table as **read-only**: writes go through `cron_add`, `cron_update`
  and `cron_remove`, because rows carry claim generations and an admission
  record (`cron.admission`).
- **The shell-job timeout.** OpenHuman kills a shell job after
  `[cron] shell_timeout_secs` (default 120 s). `bin/spine` sets it to 1800
  (`bin/lib/core_sections/automation.py`), so a job that drafts several turns
  runs to the end in the foreground and cron records its real outcome (gap
  G1, now closed).
- **No retries.** Every posting job is `retries = 0`, the same as today
  (`crates/teeny/src/schedule.rs:382`).
- **Telegram** uses a 1-minute poll in bash, not OpenHuman's channel. The
  channel sends the model's text straight to the chat with no outbound
  filter, house style or dry run (the Rust bot notes the same thing in
  `crates/teeny/src/telegram/mod.rs:8-17`), and no hook can rewrite or block
  it (gap G3). Shell jobs are exempt from the 5-minute minimum interval.

## 6a. Scheduled agents: the builder and the browser operator

Two agents act from cron, and only these two (`AGENT_JOB_AGENTS` in
`bin/lib/cron_policy.py`):

| Agent | Job | Takes work from | Tools | Leaves through |
|---|---|---|---|---|
| `builder` (`brain/agents/builder.toml`) | pinned `builder`, `10 */2 * * *` | its desk, `state/workbench/desk/build.json`, laid out by `hands/build desk`: the oldest picked build request, or one small daily thing | `shell` (sandboxed), `file_read`, `file_write`, `edit`, `glob`, `grep`, `list`, `tool_search` | its report in `build-done.json`; `bin/build-ship` (pinned `build-ship`, every 30 minutes) → `hands/build desk`, `hands/build ship` → `hands/gh create-repo` and `push` |
| `operator` (`brain/agents/operator.toml`) | pinned `operator`, `40 * * * *` | its desk, `state/workbench/desk/task.json`, laid out by `hands/tasks desk` (`bin/tasks-desk`, every 15 minutes): tasks playbooks and the operator queued | `browser`, `file_read`, `file_write`, `tool_search` | its report in `task-done.json`; screenshots under `state/media/tasks/<id>/` for `hands/x upload`; the outcome in `browser_tasks` |

**How a run may act.** Each definition sets `scheduled_actions = true` and
names its tools (never the wildcard). A cron job naming that agent in
`agent_id` then runs as `TrustedAutomation { CronActions }`: the root run's
tools go without a person's approval; a sub-agent it spawns stays read-only.
Still in force: the autonomy tier and `workspace_only` (§2), TinySecurity's
floor and command analysis, the agent's `sandbox_mode`, the allowed
websites, and the browser's checkpoint before any payment, which ends a
task as `needs_human` whatever `unattended_actions` says. `bin/doctor` and
`bin/lib/cron_policy.py` refuse a definition that loses the opt-in or its
named tools, gives the builder the browser or a wallet tool, or gives the
operator the shell or file writes; the drafting agents (`act`, `judge`,
`teeny-mentions`, `teeny-mail`) keep no tools and may never opt in.

**How they reach the hands: a desk, not a call.** The core's workspace is
`brain/` and its action directory is the checkout, so `workspace_only`
alone would let a file tool reach `brain/` and nothing of `state/`.
`bin/spine` therefore grants exactly `state/workbench/` as a trusted root
(read and write) and forbids Teeny's brain files (agents, playbooks, cron,
storage) and `secrets/`. The agents call no hand. A cron shell job runs the
hand's `desk` verb: it collects the report the agent left in
`state/workbench/desk/<name>-done.json`, passes it to the hand's own `done`
(every rule applies; a refused report is set aside with the reason, which
the next offer carries), then writes the next job or task to
`state/workbench/desk/<name>.json`. The agent reads one file and writes one.
This was the simplest safe choice, and the one that works at this pin:
- the operator has no shell, so a shell wrapper could not serve it;
- the builder's shell runs sandboxed (`sandbox_mode = "sandboxed"`: TinyBox's
  namespace jail, a private mount tree and no network); a shell that could
  run `hands/build` could also run `hands/gh push`;
- the file tools are ones both agents need anyway, scoped by OpenHuman's
  own path policy to the workbench;
- the hand that acts on a report runs outside any model turn;
- MCP (`hands/mcp`, the plan in §5) would be the cleaner call. A serve host
  used to drop every configured MCP server's tools when TinySecurity was on
  (gap G11, fixed in openhuman#8654), so it is possible now but not built.

**Configuration.** `bin/lib/core_config.py` runs every boot (after the
inbound sources) and writes each module in `bin/lib/core_sections/` into
the core's config, each table replaced whole. `automation.py` owns `[cron]`
(`shell_timeout_secs = 1800`), `[autonomy]`, `[browser]`, `[http_request]`
(`allowed_domains` from `deploy/browser-allow.txt`), `[modules]` (the two
linked modules). Another concern, the
wallet's `[wallet_policy]`, is another module beside it.

**What the builder may not do.** It has no token and no network in its
shell; it publishes nothing. `hands/build ship` runs from cron, outside any
model turn, and pushes only the job's folder through hands/gh: the guard on
the commit message and description, the owner rule, the workbench rule and
the secret scan. A refused push is retried twice more, then the job fails
and its request is marked `failed`.

## 7. Two walkthroughs

**A reply** (built: `bin/mentions`, the pinned `mentions-inbound` job, every
5 minutes).
1. `hands/x ingest` reads the account's mentions timeline
   (`/2/users/:id/mentions`, which includes replies to its own posts) from the
   `since_id` cursor in `x_cursor`, so each mention is read and billed once.
   Each new one is a row in `x_mentions`, unique on post id. Its own posts and
   anything already answered are `skipped`; `hands/guard inbound` screens the
   rest, and a held mention is never offered to a model.
2. Each `pending` row is offered to the core with `openhuman.inbound_offer`
   (source `x:mentions`, key `mentions`, the post id as the event id) and
   marked `offered`. A failed offer leaves it `pending` for the next run.
3. The core queues the offers durably and coalesces them: 30s after the last
   one, or 5 minutes after the first, or 20 in a batch
   (`[inbound.sources."x:mentions"]`, written into the core's config by
   `bin/lib/inbound_config.py` at every boot). The batch is **one turn** of
   the tool-less `teeny-mentions` agent, prompted with the body of
   `brain/playbooks/mentions.md` and the mentions as JSON. Its answer is kept
   as a result (`openhuman.inbound_results`), and `InboundTurnCompleted` goes
   out on `/events/domain` to wake anything listening.
4. `bin/inbound-consume` reads the completed results. Each `reply` item goes
   through `hands/x reply --to <id>`, but only to a mention in that batch, so
   a draft cannot choose whom it answers. Inside the hand, in order: `guard
   check`, house style, the 280 and emoji checks, one reply per post, the
   30–60s pacing, then `DRY_RUN`, then X. The outcome is written back with
   `hands/x mark` (`drafted` under `DRY_RUN`, `replied`, `refused` or
   `skipped`), and the result is acknowledged so it is handled once. A
   pacing wait longer than the run's budget leaves the result unacknowledged
   for the next run.

**The morning post.**
1. `morning` at 07:00 runs `hands/cadence claim morning`. It applies the 20h
   re-run guard (recorded at the decision, so a failed run still counts), the
   posting gap (3h) and any hot-post hold, and marks the claim.
2. The script runs the morning skill's stages as separate `teeny-drafter`
   turns: pick (news and timeline through read verbs) → script → video
   (`hands/media gen`, under the media budget) → caption → critique.
3. `hands/x post --media <id> < caption.json` runs the guard and checks, takes
   the posting `flock`, re-checks the gap inside the lock, posts, and records
   to the ledger. A ledger write that fails after the post is logged and
   never retried.

## 8. Deploy

- One image, the root `Dockerfile`, in three stages:
  - build `teeny-spine` from `vendor/openhuman`;
  - stage OpenHuman's pinned, digest-checked module releases with its own
    `stage-modules.mjs` (TinyBox among them, which cron shell jobs need);
  - a slim runtime with `jq`, `sqlite3` and `python3`.
- Nothing of Teeny's is baked in: `deploy/compose.yaml` mounts the checkout at
  `/srv/teeny`, so a job or `cron.toml` change is a pull and a restart, not a
  rebuild.
- The entrypoint is `bin/spine`. It mints `secrets/core.token` and
  `secrets/openhuman.key` on first boot, starts `serve`, configures the model
  and memory from `secrets/spine.env` (read in a subshell, so the keys never
  enter the core's environment, which jobs inherit), writes the mentions
  inbound source into the core's config, and runs `bin/seed-cron`.
- Gone: Mongo (its collections move into `hands.db`; see `hands.md`), the
  axum server and its feeds (`web/` reads `record/`), and the `opencompany`
  and `brain-sync` services.
- The shadow run follows today's pattern (`deploy/README.md`, "Moving to the
  Rust bot"): the new stack runs beside the live bot with `DRY_RUN=1` and its
  own memory root, and its drafts are compared before cutover.

## 9. What was removed

The rewrite removed (see `55c2c75` for the code):
- the Rust bot: `crates/` (11 crates), its `Dockerfile`, `docker/` and
  `scripts/`;
- the bot's fork of OpenHuman, replaced by upstream at `vendor/openhuman`;
- the TypeScript service, `legacy/`;
- the OpenCompany brain, `brain/`;
- the bot's deploy: `compose.prod.yaml`, `deploy.sh` and the
  deploy-on-merge workflow.

Where each crate's job goes:

| Crate | Goes to |
|---|---|
| `teeny` (serve, http, schedule, cmd, telegram, stream) | Spine `serve`, `bin/*`, `cron.toml`. `auth-x` and `unsay` become `hands/x`, telegram becomes `hands/telegram`, the feeds become `hands/record` |
| `teeny-agent` (specs, posture, runtime, prompt, tools) | Brain `agents/*.toml`, `SOUL.md` and skills. The tools become hands, with the read verbs served by `hands/mcp` |
| `teeny-jobs` (reply, morning, afternoon, evening, chart, moon, lore, quote, article, narrative, backrooms, calendar, learnings, chat, heat, ingest, runner) | Brain `jobs/*.md` and skills, plus `bin/job` |
| `teeny-x` | `hands/x` |
| `teeny-safety` | `hands/guard` |
| `teeny-cadence` | `hands/cadence` |
| `teeny-store` | `state/hands.db` (tables in `hands.md`) |
| `teeny-memory` | Spine memory (CortexDB engine). `memory import` is dropped after cutover |
| `teeny-market` | `hands/market` |
| `teeny-media` | `hands/media` and `hands/chart` |
| `teeny-core` | Dropped. Config becomes the hands' env files |

The invariants each hand inherits are listed in `hands.md`.

## 10. Migration (one PR each)

1. **Done.** The spine boots in compose from `brain/`, with
   `bin/spine`, `bin/seed-cron`, `bin/crons`, `bin/oh` and `bin/ask`. Its
   only job is `bin/heartbeat`. `bin/test/spine_live_test.sh` runs all of it
   against a real core and a stub model.
2. `hands/guard`, `hands/cadence` and `hands/x` with `hands.db`, each with the
   tests ported from `crates/teeny-safety`, `teeny-cadence` and
   `teeny-x`/`teeny-store`.
3. `ingest` and `reply` in shadow (`DRY_RUN=1`), compared against the live
   bot.
4. Morning and the two slots.
5. Telegram.
6. Backrooms and narrative.
7. Cutover: retire the old stack still running on the box (its containers
   outlive the code, which is already gone from `main`) and its Mongo, after
   copying the live collections with `bin/migrate-mongo`.
