# Teeny on a stock OpenHuman: spine, hands, brain

Teeny is three things:

| Layer | What it is | Where it lives |
|---|---|---|
| **Spine** | `teeny-spine`, OpenHuman's own command-line host (`openhuman_rpc::host::cli`, what upstream's `openhuman-core` binary is), built here from `vendor/openhuman` at a pinned `upstream/main` commit and run as `serve`. It provides the agent loop, model routing, cron, memory (CortexDB), browser, shell and JSON-RPC. No Teeny behaviour in it. | `spine/`, `vendor/openhuman` |
| **Hands** | Small executables, one per outside capability. Every hard rule (gap, caps, filter, dry run) is enforced inside a hand, never in a prompt. | `hands/` |
| **Brain** | Identity, known facts, the memory page, skills, agent definitions, playbooks and the schedule. Memory is OpenHuman's CortexDB engine. All of OpenHuman's own state is in one SQLite file. | `brain/` |

Plus `bin/`, the scripts that wire the three together, `evals/`, what Teeny
may not change about itself, and `web/`, the landing page.

OpenHuman references below use `$C = crates/openhuman-core/src`. Upstream's
gaps are in [`oh-gaps.md`](oh-gaps.md) and the hands' verbs are in
[`hands.md`](hands.md).

---

## 1. The one rule the design is built around

OpenHuman refuses any tool with an external effect in a turn that cron
started, and it checks this before every approval setting:

> "A scheduled prompt is data, not an open-ended approval for later actions.
> Keep cron agent turns within a read-only tool capability."
> (`$C/agent/tinyagents/host/security_gate.rs`; again in
> `$C/security/approval/gate_intercept.rs`)

So **models draft, and operator-written scripts act.** In concrete terms:

- **Every scheduled job is a cron *shell* job** that runs a script in `bin/`
  (`bin/act <playbook>`, `bin/tick`, `bin/mentions`, ...). OpenHuman runs a
  shell job's stored command directly. It isn't a model turn, so the
  read-only rule doesn't apply. The command was written by the operator and
  recorded when the job was seeded.
- **The script asks a tool-less agent for a draft** over the core's JSON-RPC
  (`openhuman.agent_chat` with an `agent_id`, through `bin/ask`). The
  drafting agents (`act`, `teeny`, `teeny-mentions`, `teeny-mail`, `judge`)
  have no tools at all. They answer JSON.
- **The script then calls the hands that act.** Each acting verb runs the
  guard and its own rules *itself*, in the same invocation, so a script that
  forgets them still can't post unchecked.
- **No agent that reads strangers' text holds a tool.** Mentions, mail and
  Moltbook comments reach only tool-less agents. An instruction injected into
  something they read has nothing to act with.

This needs no change to OpenHuman's security model.

**The one exception: the scheduled agents.** Two jobs need a model in the
loop that acts: the builder writes and tests code, and the browser operator
clicks through websites. OpenHuman allows exactly that, per agent: a cron job
that targets an agent whose definition sets `scheduled_actions = true` and
names its tools explicitly runs as a `CronActions` turn and may use those
tools without a person's approval (§6a). Nothing else is lifted, and nothing
public talks to these agents: their work comes from queues the hands keep,
and what they make leaves only through a hand.

## 2. Spine

One process, `teeny-spine serve`, running as the checkout owner's uid in the
container. `bin/spine` is the entrypoint. Configuration:

| Concern | How |
|---|---|
| Home in the repo | `OPENHUMAN_WORKSPACE=/srv/teeny/brain`, with **no `config.toml` inside it**. OpenHuman then uses `brain/` as its files directory and keeps its config at `/srv/teeny/.openhuman/config.toml`, which is gitignored. OpenHuman takes the sibling `.openhuman/` for its config only when that config already exists, so `bin/spine` writes an empty one before the first boot and refuses to start with a `brain/config.toml` (the core would nest a second `brain/workspace/`). |
| One state file | `OPENHUMAN_STORAGE_URL=sqlite:/srv/teeny/brain/storage.db`. Sessions, threads, memory state, crons and encrypted secrets are rows in one `docs` table (`scope, coll, id, body` JSON). Crons are `coll = 'cron.jobs'` and `'cron.runs'`. |
| Tool working dir | `OPENHUMAN_ACTION_DIR=/srv/teeny`, so `hands/*` and `bin/*` resolve. |
| RPC auth | `OPENHUMAN_CORE_TOKEN` from `secrets/core.token`. `bin/oh` reads it, and so does every script. |
| Model | The core's `local-openai` runtime, which needs no TinyHumans account. `bin/spine` exports `LOCAL_OPENAI_URL=$LADDER_URL`, stores the key with `config_update_local_ai_settings`, and pins every workload to `local-openai:$SPINE_MODEL`. The core does not ask a `local-openai` endpoint for the model's context window, and budgets an unknown model at a few thousand tokens, so `bin/spine` registers the window with `inference_update_model_settings`, from `SPINE_CONTEXT_WINDOW` or the endpoint's `/models`. |
| Memory | `[memory] engine = "cortexdb"` with its endpoint. The key goes in the keyring via `openhuman.memory_engine_set`. Recall is injected into every turn on a thread (§4a). |
| Keyring | **File store**: `OPENHUMAN_KEYRING_BACKEND=file` and `OPENHUMAN_KEYRING_MASTER_KEY_FILE=/srv/teeny/secrets/openhuman.key`. Every secret OpenHuman holds is an encrypted row in `storage.db` under that key. Back up `storage.db` and the key file together. |
| Persona | `brain/SOUL.md`, read every turn. Each speaking agent's prompt is built from `brain/agents/<id>.md` plus the world files (§4a). |
| Agents | `brain/agents/*.toml`, loaded by the core at boot (§5). |
| Shell and files | `[autonomy] enabled = true`, `level = "full"`, `workspace_only = true`, Teeny's own brain files and `secrets/` in `forbidden_paths`, and `state/workbench/` the one trusted root. `full` keeps the cron shell jobs running; what an agent may do is bounded by its named tools, the workspace scoping and the hands (`bin/lib/core_sections/automation.py`, §6a). |
| Browser | `[browser]` headless Chromium (in the image), a persistent profile and downloads under `state/browser/`, and `unattended_actions` for the operator's clicks and typing. The websites are `[http_request] allowed_domains`, from the reviewed `deploy/browser-allow.txt`. TinyComputer and tinywallet are linked into `teeny-spine` (`spine/Cargo.toml`). |
| Wallet | `[wallet_policy]`, written every boot by `bin/lib/wallet_config.py` from the caps in `secrets/teeny.env` ([`hands.md`](hands.md), `wallet`). |

Not used: desktop control, flows, Composio, OpenHuman's channels (Telegram
and the rest), and OpenHuman's own `http_request` for anything that needs a
secret. MCP is not used: the scheduled agents reach the hands through desk
files instead (§6a).

## 3. Hands

[`hands.md`](hands.md) has every verb. The contract:

- `hands/<name> <verb> [--flag value …]`, with an optional JSON body on stdin.
  The output is exactly one JSON object on stdout. Exit `0` means ok, `2`
  means refused by a rule (the body says which), and anything else is an
  error.
- **Secrets never reach the model.** A hand reads `secrets/teeny.env`
  itself. That directory is outside `brain/` and forbidden to every agent's
  file tools.
- **Jobs find everything from the repository root.** Cron shell jobs run from
  `OPENHUMAN_ACTION_DIR` (the checkout) with an allowlisted environment
  (`HOME`, `PATH`, `LANG`; no `OPENHUMAN_*` or `TEENY_*`). So a job locates
  `state/` and `secrets/` from where it runs, reads the core token from
  `secrets/core.token`, and reads the core's URL from `state/core.url`, which
  `bin/spine` writes at boot.
- **State lives in `state/hands.db`** (SQLite, WAL), separate from
  OpenHuman's `storage.db`, so a spine upgrade or reset never touches the
  ledgers or the queues. Every rule that spans calls is a transaction there,
  and the posting path also holds a lock.
- `DRY_RUN=1` is the default for every acting verb. Going live is
  `secrets/teeny.env: DRY_RUN=0`, never an argument the model could pass.
- Language: Python 3.11 standard library, no dependencies. Each executable is
  a short shim that runs `python3 -I` over `hands/lib/teeny_hands/`.

The hands are `x`, `guard`, `gh`, `moltbook`, `mail`, `stimuli`, `brief`,
`image`, `video`, `stats`, `requests`, `build`, `tasks`, `wallet`, `bounty`
and `slopcash`.

**Memory captures.** A `remember` intent from the mind, and the nightly
diary entry, are taught to the core's memory with `openhuman.memory_learn`.

## 4. Brain (`brain/`)

```
brain/
  SOUL.md  CONTEXT.md  MEMORY.md       who Teeny is, what it may treat as known, its own page of things to keep in mind
  narrative/                           LORE.md, ARCS.md, EDITOR.md, VOICE.md
  skills/<name>/SKILL.md               conversation, humanizer, humor, video-script
  agents/<id>.toml, <id>.md            the roster (§5); the core loads them at boot
  playbooks/<name>.md                  what a scheduled bin/act job drafts, with its inputs and output
  assets/                              teeny-reference.png (the character sheet), tiny/ (published artwork)
  calendar.json                        an older posting calendar; nothing reads it now
  cron.toml                            the brain's schedule; seeded by bin/seed-cron
  # gitignored: storage.db* (the core's state and encrypted secrets), agent/prompts/ (built at boot),
  #             IDENTITY.md, ROLE.md, STYLE.md (the core's persona defaults)
# beside it, also gitignored: /.openhuman/ (config), /secrets/ (master key, env files), /state/ (hands.db and the rest)
```

**Self-improvement** is Teeny's own: it may edit its brain (`SOUL.md`,
`CONTEXT.md`, the lore, skills, playbooks, `cron.toml`) and add hands,
driven by the goals in `narrative/LORE.md` and tracked in
`narrative/ARCS.md`. A candidate brain is judged against the current one and
merged only if it wins, behind rules in code the brain cannot edit. The spec
is [`evolve.md`](evolve.md); the loop itself is not built yet.

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

Every agent is a definition in `brain/agents/`. The speaking agents' prompts
are built at boot (§4a).

| Agent | Started by | Tools |
|---|---|---|
| `teeny` | `bin/tick` (the mind, thread `teeny:mind`), `bin/reflect` | none |
| `act` | `bin/act` (every playbook, thread `teeny:act`) | none |
| `teeny-mentions`, `teeny-mail` | the core's inbound queue, only when `TEENY_INBOX=core` | none |
| `judge` | `bin/eval` | none |
| `builder` | the pinned `builder` cron job (§6a) | `shell` (sandboxed), file tools |
| `operator` | the pinned `operator` cron job (§6a) | `browser`, `file_read`, `file_write` |

`bin/ask` without `--agent` runs as the core's default orchestrator. Whoever
can run it holds the core token, so it is the operator's way in and is never
wired to anything public. `bin/doctor` and `bin/lib/agent_check.py` refuse a
drafting agent that gains a tool.

## 6. Triggers

Every scheduled job is in one of two files:

- **`evals/cron.pinned.toml`**, frozen, always merged in: `mentions-inbound`
  (`bin/mentions`, every 5 minutes), `mail-inbound` (`bin/mail`, every 5
  minutes), `tick` (`bin/tick`, every 5 minutes, two minutes after each
  ingest), `reflect` (`bin/reflect`, 02:00 IST), `stats-snapshot`,
  `builder`, `build-ship`, `operator`, `tasks-desk`, `bounty-pay` and
  `health` (`bin/health`, every 15 minutes: asks the model one short
  question through the core; two failures in a row raise one alarm, a
  `health` stimulus and one issue on teenydroid/brain, closed when it
  answers again).
- **`brain/cron.toml`**, Teeny's own: the heartbeat, `morning`, `brief`, and
  the playbooks that are kept but off (`enabled = false`).

All schedules are read in `CALENDAR_TZ` unless a job sets `tz`. The brain's
post jobs use `Asia/Kolkata`.

**What the brain may schedule.** Teeny owns `brain/cron.toml` and may add,
remove or reschedule any job in it, its heartbeat included. A brain job runs
`bin/heartbeat` or `bin/act <playbook>`: `bin/act` gathers the playbook's
inputs from hand read verbs (with `DRY_RUN=1` forced), drafts as the tool-less
`act` agent, and acts through a hand. The rules it cannot change are in
`bin/lib/cron_policy.py`: no job more often than every 5 minutes, at most 24
brain jobs, at most 400 `bin/act` runs a day, and the jobs in
`evals/cron.pinned.toml` are always merged in and cannot be shadowed.
`bin/doctor` checks all of it, and `bin/spine` will not boot a checkout the
doctor fails. See [`evolve.md`](evolve.md).

- **Seeding.** OpenHuman has no file-declared crons (gap G2). `bin/seed-cron`
  reconciles both files against `openhuman.cron_list` by name, adding,
  updating or removing jobs whose name starts with `teeny.`. It is idempotent
  and runs at every boot.
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

  Runs are `coll = 'cron.runs'`, one row per run with its `job_id`, `status`
  and `output`. Treat the table as **read-only**: writes go through
  `cron_add`, `cron_update` and `cron_remove`, because rows carry claim
  generations and an admission record.
- **The shell-job timeout.** OpenHuman kills a shell job after
  `[cron] shell_timeout_secs` (default 120 s). `bin/spine` sets it to 1800
  (`bin/lib/core_sections/automation.py`), so a job that drafts several turns
  runs to the end and cron records its real outcome.

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
- an MCP server wrapping the hands' read verbs would be the cleaner call. A
  serve host used to drop every configured MCP server's tools when
  TinySecurity was on (gap G11, fixed in openhuman#8654), so it is possible
  now but not built.

**Configuration.** `bin/lib/core_config.py` runs every boot (after the
inbound sources) and writes each module in `bin/lib/core_sections/` into
the core's config, each table replaced whole. `automation.py` owns `[cron]`
(`shell_timeout_secs = 1800`), `[autonomy]`, `[browser]`, `[http_request]`
(`allowed_domains` from `deploy/browser-allow.txt`) and `[modules]` (the two
linked modules). `context.py` owns `[context]` and `secrets.py` owns
`[secrets]`.

**What the builder may not do.** It has no token and no network in its
shell; it publishes nothing. `hands/build ship` runs from cron, outside any
model turn, and pushes only the job's folder through hands/gh: the guard on
the commit message and description, the owner rule, the workbench rule and
the secret scan. A refused push is retried twice more, then the job fails
and its request is marked `failed`.

## 7. Two walkthroughs

**A reply to a mention.**
1. X pushes the mention to `https://teeny.enamakel.com/api/webhooks/x`. The
   host's nginx sends it to `bin/x-webhook` (the `xhook` service on
   `127.0.0.1:3110`), which checks the signature and stores the post as a
   row in `x_mentions`, unique on post id. Teeny's own posts and anything
   already answered are `skipped`; `hands/guard inbound` screens the rest, and
   a held mention is never shown to a model.
2. `bin/mentions` (the pinned `mentions-inbound` job, every 5 minutes) runs
   `hands/x ingest`. That reads the mentions timeline from its cursor too, in
   case the webhook missed one, and offers every `pending` row to the mind's
   inbox (`hands/stimuli`). Then `hands/x requests` notes any ask to build
   something.
3. Two minutes later `bin/tick` takes a batch from the inbox and sends it to
   the tool-less `teeny` agent on the `teeny:mind` thread (§4a). The answer is
   a `thought` and intents, such as `{"do": "reply_x", "to": "<id>", "text":
   "..."}`.
4. Each intent goes to the one hand verb it names: here `hands/x reply
   --to <id>`. Inside the hand, in order: `guard check`, house style, the 280
   and emoji checks, one reply per post, never to Teeny's own post, the
   30–60s pacing, then `DRY_RUN`, then X. What the hand did goes back into the
   inbox for the next beat.

With `TEENY_INBOX=core`, step 2 offers the mentions to the core's inbound
queue instead, the core drafts each batch with `teeny-mentions`, and
`bin/inbound-consume` sends the drafts through `hands/x reply`.

**The morning post.**
1. The `morning` job runs `bin/act morning` at 07:00 IST. `bin/act` checks the
   playbook's daily budget (`max_per_day`) and gathers its inputs (none for
   `morning`).
2. One turn as the tool-less `act` agent on the `teeny:act` thread drafts the
   post as JSON, optionally with an `"image"` scene.
3. If there is a scene, `bin/act` runs it past the guard, calls
   `hands/image generate`, and uploads the file with `hands/x upload`. A
   failure there leaves the post text-only.
4. `hands/x post` runs the guard and checks, takes the posting lock, re-checks
   the 3-hour gap inside the lock, posts, and records the post. A ledger write
   that fails after the post is logged and never retried.

## 8. Deploy

- One image, the root `Dockerfile`, in three stages:
  - build `teeny-spine` from `vendor/openhuman`;
  - stage OpenHuman's pinned, digest-checked module releases with its own
    `stage-modules.mjs` (TinyBox among them, which cron shell jobs need);
  - a slim Debian runtime with Chromium, `jq`, `sqlite3`, `curl` and
    `python3`.
- Nothing of Teeny's is baked in: `deploy/compose.yaml` mounts the checkout at
  `/srv/teeny`, so a script, playbook or `cron.toml` change is a pull and a
  restart, not a rebuild.
- Three services in `deploy/compose.yaml`:
  - `spine`: the core. The entrypoint is `bin/spine`. It mints
    `secrets/core.token` and `secrets/openhuman.key` on first boot, runs the
    doctor, writes Teeny's sections into the core's config, starts `serve`,
    configures the model and memory from `secrets/spine.env` (read in a
    subshell, so the keys never enter the core's environment, which jobs
    inherit), and runs `bin/seed-cron`. It publishes no port.
  - `xhook`: the same image running `bin/x-webhook` on `127.0.0.1:3110`. It
    only writes `x_mentions` in `state/hands.db`.
  - `web`: the landing page (`web/`, its own Next.js image) on
    `127.0.0.1:3120`, behind the `web` compose profile.
- The host's nginx (`deploy/nginx/teeny.enamakel.com.conf`) serves
  `teeny.enamakel.com`: `/api/webhooks/x` to the webhook, everything else to
  the landing page.
- **The receipts.** `teenydroid/diary` is a hash-chained, append-only
  ledger of Teeny's actions at `receipts/YYYY/MM/YYYY-MM-DD.jsonl`, with the
  head in `head.json`. Each line is checkable references (X post ids, Solana
  signatures, GitHub commits, Moltbook ids), a salted commitment to the
  private ledger row, and the previous line's hash. It never holds text,
  logs or the diary entry, so nothing the model read can reach it.
  `bin/receipts` (`bin/lib/receipts.py`, the daily `[jobs.receipts]`) builds
  it from the hands' ledgers with no model in the loop, refuses a published
  chain that does not verify, and pushes after the secret gate. It is a dry
  run by default.
