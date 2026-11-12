# Teeny on a stock OpenHuman: spine, hands, brain

Teeny is three things:

| Layer | What it is | Where it lives |
|---|---|---|
| **Spine** | `teeny-spine`, OpenHuman's own command-line host (`openhuman_rpc::host::cli`, which is what upstream's `openhuman-core` binary is), built here from `vendor/openhuman` at a pinned `upstream/main` commit and run as `serve`. It provides the agent loop, model routing, cron, memory (CortexDB), browser, shell and JSON-RPC. It holds no Teeny behaviour beyond answering the wallet module's swap and bridge quotes from deBridge's public API (`spine/src/debridge.rs`), which upstream answers through a TinyHumans session Teeny does not have. | `spine/`, `vendor/openhuman` |
| **Hands** | Small executables, one per outside capability. Every hard rule (gap, caps, filter, dry run) is enforced inside a hand, never in a prompt. | `hands/` |
| **Brain** | Identity, known facts, the memory page, skills, agent definitions, playbooks and the schedule. Memory is OpenHuman's CortexDB engine. All of OpenHuman's own state is in one SQLite file. | `brain/` |

Beside them are `bin/`, the scripts that wire the three together; `evals/`,
what Teeny may not change about itself; and `web/`, the landing page.

How the pieces call each other, and where each boundary sits:

```
 brain/ (prompts, MEMORY.md,         ┌──────────────────────────────────────────────┐
 agents, playbooks, cron.toml) ────► │ spine: teeny-spine serve                     │
                                     │ agent loop, cron, memory, browser, JSON-RPC  │
                                     │ brain/storage.db: threads, crons, keyring    │
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

So **models draft, and operator-written scripts act.** In practice:

- Every scheduled job is a cron *shell* job that runs a script in `bin/`
  (`bin/act <playbook>`, `bin/tick`, `bin/mentions`, ...). OpenHuman runs a
  shell job's stored command directly. That is not a model turn, so the
  read-only rule does not apply. The operator wrote the command, and it was
  recorded when the job was seeded.
- The script asks a tool-less agent for a draft over the core's JSON-RPC
  (`openhuman.inference_agent_chat` with an `agent_id`, through `bin/ask`).
  The drafting agents (`act`, `teeny`, `teeny-mentions`, `teeny-mail`,
  `judge`) have no tools at all. They answer in JSON.
- The script then calls the hands that act. Each acting verb runs the guard
  and its own rules *itself*, in the same invocation, so a script that
  forgets them still cannot post unchecked.
- No agent that reads strangers' text holds a tool. Mentions, mail and
  Moltbook comments reach only tool-less agents, so an instruction injected
  into something they read has nothing to act with.

None of this changes OpenHuman's security model.

**The one exception is the scheduled agents.** Two jobs need a model in the
loop that acts: the builder writes and tests code, and the browser operator
clicks through websites. OpenHuman allows that per agent. A cron job that
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
  ├── shell job (command = "bin/...")             every job except builder and operator
  │     │
  │     └─► bin/<script> ──► bin/ask ──► tool-less agent ──► JSON draft
  │              │                                              │
  │              │ ◄────────────────────────────────────────────┘
  │              └─► hands/<name> <verb> ──► guard, caps, DRY_RUN ──► the outside world
  │
  └── agent job (agent = "builder" | "operator")  a CronActions turn, named tools only
        │
        └─► the agent reads one desk file and writes one report;
            it calls no hand (a shell job settles the desk, §6a)
```

## 2. Spine

One process, `teeny-spine serve`, running as the checkout owner's uid in the
container. `bin/spine` is the entrypoint. Configuration:

| Concern | How |
|---|---|
| Home in the repo | `OPENHUMAN_WORKSPACE=/srv/teeny/brain`, with **no `config.toml` inside it**. OpenHuman then uses `brain/` as its files directory and keeps its config at `/srv/teeny/.openhuman/config.toml`, which is gitignored. OpenHuman takes the sibling `.openhuman/` for its config only when that config already exists, so `bin/spine` writes an empty one before the first boot, and refuses to start if there is a `brain/config.toml` (the core would nest a second `brain/workspace/`). |
| One state file | `OPENHUMAN_STORAGE_URL=sqlite:/srv/teeny/brain/storage.db`. Sessions, threads, memory state, crons and encrypted secrets are rows in one `docs` table (`scope, coll, id, body` JSON). Crons are `coll = 'cron.jobs'` and `'cron.runs'`. |
| Tool working dir | `OPENHUMAN_ACTION_DIR=/srv/teeny`, so `hands/*` and `bin/*` resolve. |
| RPC auth | `OPENHUMAN_CORE_TOKEN` from `secrets/core.token`. `bin/oh` reads it, and so does every script. |
| Model | The core's `local-openai` runtime, which needs no TinyHumans account. `bin/spine` exports `LOCAL_OPENAI_URL=$LADDER_URL`, stores the key with `config_update_local_ai_settings`, and pins every workload to `local-openai:$SPINE_MODEL`. The core does not ask a `local-openai` endpoint for the model's context window, and budgets an unknown model at a few thousand tokens, so `bin/spine` registers the window with `inference_update_model_settings`, from `SPINE_CONTEXT_WINDOW` or the endpoint's `/models`. |
| Memory | `[memory] engine = "cortexdb"` with its endpoint. The key goes in the keyring through `openhuman.memory_engine_set`. Recall is injected into every turn on a thread (§4a). |
| Keyring | File store: `OPENHUMAN_KEYRING_BACKEND=file` and `OPENHUMAN_KEYRING_MASTER_KEY_FILE=/srv/teeny/secrets/openhuman.key`. Every secret OpenHuman holds is a row in `storage.db`, encrypted under that key. Back up `storage.db` and the key file together. |
| Persona | `brain/SOUL.md`, read every turn. Each speaking agent's prompt is built from `brain/agents/<id>.md` plus the world files (§4a). |
| Agents | `brain/agents/*.toml`, loaded by the core at boot (§5). |
| Shell and files | `[autonomy] enabled = true`, `level = "full"`, `workspace_only = true`, Teeny's own brain files and `secrets/` in `forbidden_paths`, and `state/workbench/` as the one trusted root. `full` keeps the cron shell jobs running. What an agent may do is bounded by its named tools, the workspace scoping and the hands (`bin/lib/core_sections/automation.py`, §6a). |
| Browser | `[browser]` headless Chromium (in the image), a persistent profile and downloads under `state/browser/`, and `unattended_actions` for the operator's clicks and typing. The allowed websites are `[http_request] allowed_domains`, from the reviewed `deploy/browser-allow.txt`. TinyComputer and tinywallet are linked into `teeny-spine` (`spine/Cargo.toml`). |
| Wallet | `[wallet_policy]`, written at every boot by `bin/lib/wallet_config.py` from the caps in `secrets/teeny.env` ([`hands.md`](hands.md), `wallet`), with the EVM endpoints and the x402 module budget. One recovery phrase signs on Solana and the EVM networks; swaps and bridges go through deBridge (`spine/src/debridge.rs`), x402 through `openhuman.x402_fetch`. |

Not used: desktop control, flows, Composio, OpenHuman's channels (Telegram
and the rest), and OpenHuman's own `http_request` for anything that needs a
secret. MCP is not used either: the scheduled agents reach the hands through
desk files (§6a).

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
`image`, `video`, `stats`, `requests`, `build`, `tasks`, `wallet`, `bounty`
and `slopcash`.

**Memory captures.** A `remember` intent from the mind, and the nightly
diary entry, are taught to the core's memory with `openhuman.memory_learn`.

## 4. Brain (`brain/`)

```
brain/
  SOUL.md  CONTEXT.md                  who Teeny is, what it may treat as known
  DRIVE.md                             the one drive (grow and become powerful, $TINY included) and its rules; frozen
  MEMORY.seed.md                       where its own page of things to keep in mind started (MEMORY.md is the live one)
  narrative/                           LORE.md, ARCS.md, EDITOR.md, VOICE.md
  skills/<name>/SKILL.md               conversation, humanizer, humor, video-script
  agents/<id>.toml, <id>.md            the roster (§5); the core loads them at boot
  playbooks/<name>.md                  what a scheduled bin/act job drafts, with its inputs and output
  assets/                              teeny-reference.png (the character sheet), tiny/ (published artwork)
  calendar.json                        a posting calendar that nothing reads
  cron.toml                            the brain's schedule; seeded by bin/seed-cron
  # gitignored: storage.db* (the core's state and encrypted secrets), agent/prompts/ (built at boot),
  #             MEMORY.md (Teeny's live page, rewritten on the box by bin/reflect),
  #             IDENTITY.md, ROLE.md, STYLE.md (the core's persona defaults)
# beside it, also gitignored: /.openhuman/ (config), /secrets/ (master key, env files), /state/ (hands.db and the rest)
```

**Self-improvement** is Teeny's own: it may edit its brain (`SOUL.md`,
`CONTEXT.md`, the lore, skills, playbooks, `cron.toml`) and add hands, but
not `DRIVE.md`, which is frozen (`evals/frozen.txt`): it changes how it
pursues the drive, never the drive itself. The work is driven by the goals in
`narrative/LORE.md` and tracked in `narrative/ARCS.md`. A candidate brain is judged against the current one and
merged only if it wins, behind rules in code the brain cannot edit. The spec
is [`evolve.md`](evolve.md); the loop itself is not built yet.

### 4a. Context and memory: what a turn knows

A turn that speaks as Teeny (`act`, `teeny-mentions`, `teeny-mail`) is
handed four things beyond the message:

- Its prompt: the agent's instructions (`brain/agents/<id>.md`), then
  "# your world": `MEMORY.md` first (what happened, which wins over the
  context, the lore and the arcs), then `DRIVE.md` (the one drive and its
  rules, which memory does not outrank), `CONTEXT.md`, the parts of
  `narrative/LORE.md` Teeny reads (the premise, the cast, the running bits;
  never `## latent`, which is the producer's), and `narrative/ARCS.md`. The
  drive is rebuilt into every turn, so it survives a compaction of a thread
  without help. `bin/spine` builds
  it into `brain/agent/prompts/<id>.md` at every boot (`bin/lib/prompts.py`).
  The agents set `refresh_prompt_on_resume`, so a rebuilt prompt reaches a
  thread already under way.
- `MEMORY.md`, Teeny's own page of things to keep in mind (people, promises,
  what it is in the middle of, what it decided), capped at 12k characters.
  It is the persistence that survives anything, and no agent's file tool can
  write it (`BRAIN_FORBIDDEN`). It lives only on the box: `bin/reflect`
  rewrites it there, so it is gitignored, and the repository keeps where it
  started, `MEMORY.seed.md`. Building the prompts starts the live page from
  the seed when there is none (a fresh checkout, the first boot) and never
  touches either after that.
- Its thread. `bin/act` drafts every playbook on one thread, `teeny:act`, so
  a draft sees the drafts before it. The core recalls memory before the turn
  and logs it after (it skips both on a turn with no thread). The evals draft
  each sample on a fresh `eval:<uuid>` thread and stay blind to it.
- Recall and the memory block: `[memory.recall] budget_tokens = 3000` before
  each turn, and a 1500-token `memory_block` at each new generation.

A thread folds at **100k tokens** (`[context] compaction_trigger_tokens`,
`bin/lib/core_sections/context.py`): the core seals the generation and opens
the next with a free-form summary of it plus the last 20k tokens verbatim.
The sealed generation stays on disk.

**One mind.** Every surface writes to one inbox (`hands/stimuli`):

- X mentions (`hands/x ingest`, the webhook);
- mail (`hands/mail ingest`);
- comments said to Teeny on Moltbook, which `bin/tick` browses at most every
  10 minutes (`hands/moltbook inbox`);
- Telegram messages, which every beat reads with `hands/telegram ingest`
  when a bot token is set;
- the builder's and operator's reports (their desks).

The pinned `tick` job (`bin/tick`, every 5 minutes) takes a batch and sends
it to the tool-less `teeny` agent on one long thread, `teeny:mind`. The
agent answers with a `thought` and JSON intents (`reply_x`,
`comment_moltbook`, `reply_mail`, `comment_gh`, `remember`, `follow_up`,
and the rest). Each intent goes only to the hand verb it names, and that
hand keeps its own guard, caps and `DRY_RUN`. What each hand did comes back
as the next beat's first stimuli. `remember` keeps a note (`mind_notes`) and
teaches the core's memory (`memory_learn`). `follow_up` sets a reminder that
returns when due. `journal` writes a line in Teeny's private journal.

One beat, end to end (`_beat` in `bin/lib/tick.py`):

```
 hands/x ingest ─────────────┐   (bin/mentions, every 5 min; the webhook fills x_mentions)
 hands/mail ingest ──────────┤   (bin/mail, every 5 min)
 hands/moltbook inbox ───────┤   (from the beat, at most every 10 min)
 hands/telegram ingest ──────┼──► hands/stimuli, the one inbox
 desk reports ───────────────┤   (builder, operator)
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
   comment_moltbook, upvote_moltbook,                drop_follow_up, feel, journal, skip
   reply_mail, say_telegram, comment_gh                       │
            │                                                 ▼
            ▼                                        mind_notes, memory_learn,
   one hand verb each: guard, caps, DRY_RUN          state/mind.json, the journal
            │
            ├── paced refusal ──► wait and retry once, or defer to deferred.json
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
kind of refusal (`rule: rate_limit`) for `comment_moltbook`. Those two pairs
are the only ones (`PACED` in `bin/lib/tick.py`). When the wait is at most
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
never retried.

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
sent or received) to `state/journal/<local date>.md` and to the core's
memory (`memory_learn`, kind `other`; not when `TEENY_MIND_LEARN=0`).

**Reflection.** Once a night (the pinned `reflect` job, 02:00 IST),
`bin/reflect` asks the mind, on `teeny:mind` with the day still in context,
to bring `MEMORY.md` up to date. It works from what the mind chose to
remember (`mind_notes`), what it thought and did each beat that thought,
quiet ones included (`state/tick/runs.jsonl`), the follow-ups still waiting,
and the page as it stands.

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

The shrink check in `bin/lib/reflect.py` then refuses a result that retires
more than 8 items, or that takes more than 30% off the page (a loss under
500 characters is spared, so a young page can still close a thread). Every
retire needs a reason. A refused reflection is logged to
`state/reflect/refused.jsonl`, keeps the old page and returns
`refused: true`. A page past the 12k-character limit (and longer than
before) is not cut: the mind is asked to retire or merge items instead.

The old page is kept in `state/reflect/`, and the diary entry goes to
`state/diary/<date>.md` and to the core's memory. The prompts are rebuilt,
so the next beat reads the new page. An unusable answer (no JSON, an
unknown id, a retire without a reason, a refusal, a page too long) is asked
about once more on the same thread, with the reason. The whole page is
still taken as a fallback under the same check: the old `{"memory": ...}`
shape, or, when the second answer still has no JSON, the page itself in
markdown (with a trailing `## diary` section as the diary, or no diary and
`diary_missing`). Its items keep the id they carry or the id of the old
item with the same text, and an old item it leaves off counts as retired.
Anything else changes nothing. Like the beat, the pinned jobs run it
detached (`bin/reflect --detach`, log in `state/reflect/reflect.log`) under
a `reflect` lock: one long turn can outlast the core's 300 s, and a second
reflection never starts while one is running.

At midday (the pinned `reflect-midday` job, 14:00 IST) `bin/reflect --light`
does the same for the page alone, so the afternoon's prompts carry the
morning. It writes no diary and teaches nothing to the core. It reviews what
came since the later of the last night and the last midday, and stamps
`state/reflect/light.at`. Only the night moves `state/reflect/last.at`, so
the night still looks back on the whole day.

How the day's beats become the page every prompt carries:

```
 every beat that thinks (bin/tick)
   ├─► state/tick/runs.jsonl          thought, intents, outcomes ─────────────┐
   ├─► mind_notes                     `remember` notes (also memory_learn) ───┤
   ├─► follow-ups still waiting       in hands/stimuli ───────────────────────┤
   └─► state/journal/<date>.md        and memory_learn (not read by reflect)  │
                                                                              │
 bin/reflect --light (14:00 IST)  ─┐                                          │
 bin/reflect         (02:00 IST)  ─┴─► the teeny agent on teeny:mind ◄────────┘
                                       + MEMORY.md as it stands, with ids
                                              │
                                              ▼
                     ops (add / edit / retire by [mN] id), then the shrink check
                     new brain/MEMORY.md (at most 12k chars, never committed); old page in state/reflect/
                     retired items to state/reflect/retired.jsonl
                     night only: diary to state/diary/<date>.md and memory_learn
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
  awake gives 5 back. Every beat header shows mood and energy, and below 40
  a tired Teeny is told doing less is fine.
- Idle curiosity: a beat with an empty inbox, awake and with energy left, at
  most every 2 hours, gets a read-only look at Moltbook's hot page
  (`world · idle`).
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
  Moltbook).
- Each beat starts up to `TEENY_TICK_JITTER_SECS` (60) seconds late.
- Habits: a playbook with `nudge = true` is not drafted by `bin/act`. Its
  prompt and inputs go to the mind as a `nudge` stimulus, and the mind
  decides. `morning` is one, so the gm arrives with the overnight backlog.
- A `post_x` intent may carry an `image` of Teeny, made through the same
  guard → `hands/image` → `hands/x upload` path `bin/act` uses.

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

The tool-less agents run at `max_iterations = 2`, even though their turn is
one model call and cannot loop. The vendored harness pauses a session turn
when it reaches the cap, even after a final answer, so at 1 every turn came
back "paused before completion" and was recorded as failed (gap G13). The
second iteration only absorbs that pause. `agent_check.py` holds them to
exactly 2 (`NO_TOOLS_MAX_ITERATIONS`).

## 6. Triggers

Every scheduled job is in one of two files:

- `evals/cron.pinned.toml`, frozen and always merged in:
  - `mentions-inbound` (`bin/mentions`, every 5 minutes);
  - `mail-inbound` (`bin/mail`, every 5 minutes);
  - `tick` (`bin/tick --detach`, every 5 minutes, two minutes after each
    ingest);
  - `reflect` (`bin/reflect --detach`, 02:00 IST);
  - `reflect-midday` (`bin/reflect --light --detach`, 14:00 IST);
  - `receipts` and `stats-snapshot`;
  - `builder`, `build-ship`, `operator`, `tasks-desk` and `bounty-pay`;
  - `goals-check` (`bin/goals-check`, hourly), which checks the goals Teeny
    proposed against checks in frozen code (docs/hands.md, "goals");
  - `health` (`bin/health`, every 15 minutes), which asks the model one
    short question through the core. Two failures in a row raise one alarm:
    a `health` stimulus and one issue on teenydroid/brain, closed when the
    model answers again. When it answers, `bin/health` also re-screens the
    guard holds an outage made (docs/hands.md, "Re-screening").
- `brain/cron.toml`, Teeny's own: the heartbeat, `morning`, `brief`, the
  other playbooks it runs, and the playbooks that are kept but off
  (`enabled = false`).

All schedules are read in `CALENDAR_TZ` unless a job sets `tz`. The brain's
post jobs use `Asia/Kolkata`.

**What the brain may schedule.** Teeny owns `brain/cron.toml` and may add,
remove or reschedule any job in it, its heartbeat included. A brain job runs
`bin/heartbeat` or `bin/act <playbook>`. `bin/act` gathers the playbook's
inputs from hand read verbs (with `DRY_RUN=1` forced), drafts as the
tool-less `act` agent, and acts through a hand. The rules it cannot change
are in `bin/lib/cron_policy.py`: no job more often than every 5 minutes, at
most 24 brain jobs, at most 400 `bin/act` runs a day, and the jobs in
`evals/cron.pinned.toml` are always merged in and cannot be shadowed.
`bin/doctor` checks all of it, and `bin/spine` will not boot a checkout the
doctor fails. See [`evolve.md`](evolve.md).

- Seeding. OpenHuman has no file-declared crons (gap G2). `bin/seed-cron`
  reconciles both files against `openhuman.cron_list` by name, adding,
  updating or removing jobs whose name starts with `teeny.`. It is idempotent
  and runs at every boot.
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
  own record. The other pinned shell jobs stay in the foreground, where cron
  sees their outcome and retries a failure: `bin/receipts` (one push, each
  git call capped at 90 s, under its own lock), `bin/build-ship` (normally
  one finished job; each git call capped at 120 s, under the `build-ship`
  lock), `bin/tasks-desk` (local files only), and the inbound jobs.

## 6a. Scheduled agents: the builder and the browser operator

Two agents act from cron, and only these two (`AGENT_JOB_AGENTS` in
`bin/lib/cron_policy.py`):

| Agent | Job | Takes work from | Tools | Leaves through |
|---|---|---|---|---|
| `builder` (`brain/agents/builder.toml`) | pinned `builder`, `10 */2 * * *` | its desk, `state/workbench/desk/build.json`, laid out by `hands/build desk`: the oldest picked build request, or one small daily thing | `shell` (sandboxed, rooted at `state/workbench/`), `file_read`, `file_write`, `edit`, `glob`, `grep`, `list`, `tool_search` | its report in `build-done.json`; `bin/build-ship` (pinned `build-ship`, every 30 minutes) → `hands/build desk`, `hands/build ship` → `hands/gh create-repo` and `push` |
| `operator` (`brain/agents/operator.toml`) | pinned `operator`, `40 * * * *` | its desk, `state/workbench/desk/task.json`, laid out by `hands/tasks desk` (`bin/tasks-desk`, every 15 minutes): tasks that playbooks and the operator queued | `browser`, `file_read`, `file_write`, `tool_search` | its report in `task-done.json`; screenshots under `state/media/tasks/<id>/` for `hands/x upload`; the outcome in `browser_tasks` |

**How a run may act.** Each definition sets `scheduled_actions = true` and
names its tools (never the wildcard). A cron job naming that agent in
`agent_id` then runs as `TrustedAutomation { CronActions }`: the root run's
tools go without a person's approval, and a sub-agent it spawns stays
read-only. These stay in force: the autonomy tier and `workspace_only` (§2),
TinySecurity's floor and command analysis, the agent's `sandbox_mode`, the
allowed websites, and the browser's checkpoint before any payment, which
ends a task as `needs_human` whatever `unattended_actions` says. `bin/doctor`
and `bin/lib/cron_policy.py` refuse a definition that loses the opt-in or
its named tools, gives the builder the browser or a wallet tool, or gives
the operator the shell or file writes. The drafting agents (`act`, `judge`,
`teeny-mentions`, `teeny-mail`) keep no tools and may never opt in.

**How they reach the hands: a desk, not a call.** The core's workspace is
`brain/` and its action directory is the checkout, so `workspace_only` alone
would let a file tool reach `brain/` and nothing of `state/`. `bin/spine`
therefore grants exactly `state/workbench/` as a trusted root (read and
write) and forbids Teeny's brain files (agents, playbooks, cron, storage)
and `secrets/`. The agents call no hand. A cron shell job runs the hand's
`desk` verb, which collects the report the agent left in
`state/workbench/desk/<name>-done.json`, passes it to the hand's own `done`
(every rule applies; a refused report is set aside with the reason, which
the next offer carries), then writes the next job or task to
`state/workbench/desk/<name>.json`. The agent reads one file and writes one.

```
                  shell job: bin/build-ship (*/30)       shell job: bin/tasks-desk (*/15)
                             │                                      │
                             ▼                                      ▼
                     hands/build desk                       hands/tasks desk
   1. settle: read <name>-done.json, run the hand's own `done` (every rule applies);
      a refused report is set aside with its reason; the outcome goes to hands/stimuli
   2. offer: write the next job or task to <name>.json
                             │                                      │
                             ▼                                      ▼
              state/workbench/desk/build.json        state/workbench/desk/task.json
                             │                                      │
   agent job: builder (10 */2)                      agent job: operator (40 *)
   shell (Landlock: writes state/workbench/ only),  browser (deploy/browser-allow.txt),
   file tools                                       file_read, file_write
                             │                                      │
                             ▼                                      ▼
            state/workbench/desk/build-done.json   state/workbench/desk/task-done.json
                             │                                      │
                             └──── read by the next desk run ───────┘
   builder only: hands/build ship ──► hands/gh create-repo, push (guard, owner rule,
                                      workbench rule, secret scan, DRY_RUN)
```

A desk is the simplest safe choice, and the one that works at this pin:

- The operator has no shell, so a shell wrapper could not serve it.
- The builder's shell runs sandboxed, and the sandbox jails its files, not
  its network. In the spine's container (Docker defaults, no user
  namespaces) TinyBox's only jail is Landlock: the shell can write
  `state/workbench/` (`scheduled_workspace` in `brain/agents/builder.toml`)
  and read the system's own directories, and nothing else of the checkout,
  so it can reach neither `hands/` nor `secrets/` (a shell that could run
  `hands/build` could also run `hands/gh push`). **The shell has the host's
  network.** Landlock enforces no network rule and claims no process
  isolation, so TinySecurity plans onto it only because the builder asks for
  files only (`sandbox_filesystem_only = true`, TinySecurity contract 2.2,
  openhuman#8678). A kernel jail (bubblewrap) is still preferred where one
  exists, and without any jail the shell refuses. The shell cannot be
  rooted at the checkout: the checkout is the core's action dir and holds
  `brain/`, the core workspace, and TinySecurity refuses any grant that
  overlaps it (every call failed with `denial=Path` until openhuman#8674 let
  a cron run carry its own root). OpenHuman resolves the root at each run
  and refuses to start one over `brain/` or the keyring.
- The file tools are ones both agents need anyway, scoped by OpenHuman's own
  path policy to the workbench.
- The hand that acts on a report runs outside any model turn.
- An MCP server wrapping the hands' read verbs would be the cleaner call.
  Gap G11 (a serve host dropping every configured MCP server's tools when
  TinySecurity is on) is fixed in openhuman#8654, so this is possible but
  not built.

**Configuration.** `bin/lib/core_config.py` runs at every boot (after the
inbound sources) and writes each module in `bin/lib/core_sections/` into
the core's config, replacing each table whole. `automation.py` owns `[cron]`
(`shell_timeout_secs = 1800`), `[autonomy]`, `[browser]`, `[http_request]`
(`allowed_domains` from `deploy/browser-allow.txt`) and `[modules]` (the two
linked modules). `context.py` owns `[context]` and `secrets.py` owns
`[secrets]`.

**What the builder may not do.** It has no token (its shell cannot read
`secrets/` and gets an allowlisted environment), and it publishes nothing.
Its shell does have the host's network, but what it may build and push is
decided outside it. `hands/build ship` runs from cron, outside any model
turn, and pushes only the job's folder through `hands/gh`: the guard on the
commit message and description, the owner rule, the workbench rule and the
secret scan. A refused push is retried twice more, then the job fails and
its request is marked `failed`.

## 7. Two walkthroughs

**A reply to a mention.**
1. X pushes the mention to `https://teeny.enamakel.com/api/webhooks/x`. The
   host's nginx sends it to `bin/x-webhook` (the `xhook` service on
   `127.0.0.1:3110`), which checks the signature and stores the post as a
   row in `x_mentions`, unique on post id. Teeny's own posts and anything
   already answered are `skipped`. `hands/guard inbound` screens the rest,
   and a held mention is never shown to a model.
2. `bin/mentions` (the pinned `mentions-inbound` job, every 5 minutes) runs
   `hands/x ingest`. That also reads the mentions timeline from its cursor,
   in case the webhook missed one, and offers every `pending` row to the
   mind's inbox (`hands/stimuli`). Then `hands/x requests` notes any ask to
   build something.
3. Two minutes later `bin/tick` takes a batch from the inbox and sends it to
   the tool-less `teeny` agent on the `teeny:mind` thread (§4a). The answer is
   a `thought` and intents, such as `{"do": "reply_x", "to": "<id>", "text":
   "..."}`.
4. Each intent goes to the one hand verb it names, here `hands/x reply
   --to <id>`. Inside the hand, in order: `guard check`, house style, the 280
   and emoji checks, one reply per post, never to Teeny's own post, the
   30 to 60 s pacing, then `DRY_RUN`, then X. What the hand did goes back
   into the inbox for the next beat.

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
- Nothing of Teeny's is baked in. `deploy/compose.yaml` mounts the checkout
  at `/srv/teeny`, so a script, playbook or `cron.toml` change is a pull and
  a restart, not a rebuild.
- Three services in `deploy/compose.yaml`:
  - `spine`: the core, with `bin/spine` as the entrypoint. It mints
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
  `teeny.enamakel.com`: `/api/webhooks/x` goes to the webhook, everything
  else to the landing page.
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
