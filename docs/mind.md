# The agentic mind

Teeny has two minds.

The **tick** (`bin/tick`, every five minutes) is reactive and tool-less. It
reads what arrived, answers with a list of intents from a fixed menu, and a
script carries each one to a hand. It is fast and cheap, and it cannot do a
piece of work that takes more than one answer.

The **agentic mind** (`brain/agents/mind.toml`, this page) is the other
kind. It wakes on a schedule with a shell, the file tools and its memory,
decides for itself what to work on, and keeps going for up to 60 tool calls
or 30 minutes. It is off until the operator sets `MIND=1` in
`secrets/teeny.env`.

## Why it exists

An audit on 2026-11-22 found that ten days of self-improvement had shipped
20 pull requests, all of them markdown under `brain/`, 66 lines in total,
and that the capability lane's first build had failed. The cause was
structural: no part of Teeny that could decide what to do next also held
tools. The operator's decision was to give Teeny a mind that does, modelled
on long-running self-directed agents: a soul it may rewrite, a schedule it
manages, projects it picks, skills it writes for itself.

## What it has

| | |
|---|---|
| Home | `state/home/`: the only folder its shell and file tools can write |
| Notes | `notes/JOURNAL.md`, `notes/PROJECTS.md`, `notes/TASKS.md`, `notes/inbox/` |
| Skills | `skills/`: scripts and notes it writes for itself |
| Tools | `shell`, `file_read`, `file_write`, `edit`, `glob`, `grep`, `list`, `tool_search`, `memory` |
| Network | the host's: it can read the web, clone and install into its home |
| Keys | none |
| Prompt | `brain/agents/mind.md`, which is Teeny's own to change |
| Wake | every 30 minutes (pinned), plus the wakes it schedules itself |

A run has no memory of the last one except its notes and the memory tool,
so the prompt has it read the journal first and write to it before ending.

## How it acts: the broker

The mind holds no token. `bin/broker` is a small HTTP server on loopback
that `bin/spine` starts outside the jail while `MIND=1`. It is the only way
the mind reaches the world, and it does nothing itself: every action is a
call to an existing hand, so the guard, the daily caps and `DRY_RUN` apply
to the mind exactly as they do to the tick.

From its shell the mind uses the client the broker installs in its home:

```
bin/teeny do '{"do": "post_x", "text": "..."}'     any intent the tick can send
bin/teeny goals                                    its measured goals
bin/teeny wake '{"name": "...", "note": "...", "at": "2026-11-23T09:00:00Z"}'
bin/teeny wake '{"name": "...", "note": "...", "cron": "0 6 * * 1"}'
bin/teeny wakes
bin/teeny drop-wake NAME
```

- `do` takes the tick's intents (`bin/lib/tick.py`, `ROUTES`) and runs them
  the way the tick does. What is switched off for the tick
  (`MIND_ISSUES_PER_DAY=0` and the rest) is off here too, and the two share
  each day's count.
- `wake` is how the mind schedules its own future. The broker creates a
  core cron job named `mind.<name>` that runs the `mind` agent with the note
  as its prompt. It is always an agent job: a cron shell job runs outside
  any jail, so the mind has no way to create one. At most 40 such jobs, none
  more often than every five minutes, and at most `MIND_WAKES_PER_DAY` runs
  a day between them.
  Every wake set that day counts against the same number, one-offs and
  dropped ones included, so a run cannot keep scheduling the next one or
  drop a wake to set another.
- Every request is one line in `state/broker/ledger.jsonl`.

The mind **rehearses by default**. Unless `secrets/teeny.env` says
`MIND_DRY_RUN=0`, the broker runs every hand in dry-run and creates no wake:
the mind thinks, writes its notes and asks for things, the ledger shows what
it would have done, and nothing leaves the box. The rest of Teeny is not
affected. The box's own `DRY_RUN` holds the mind either way.

Some hands have no dry run of their own: `hands/tasks` queues a browser
task and `hands/build` files a build request whatever `DRY_RUN` says, and a
live worker would then act on it. In a rehearsal the broker checks such a
request (its fields, its cap) and does not run the hand. Only the hands that
rehearse themselves (`x`, `moltbook`, `mail`, `telegram`, `gh`, `reddit`)
are run, and each says what it would have sent.

**Off means off.** With `MIND` not `1`, the pinned wake and every
`agent = "mind"` job in `brain/cron.toml` are disabled, the broker is not
started, and at boot `bin/spine` removes the wakes the mind had set for
itself (`bin/broker sweep`).

Every sandboxed shell on the box shares the loopback, including the
builder's, which works on strangers' requests. So the broker takes a bearer
token on every request, minted at each start into
`state/home/.broker-token`, a file only the mind's jail can read.

The mind can also schedule itself in the repository: a job in
`brain/cron.toml` with `agent = "mind"` and a `prompt` is a wake that
survives a redeploy and that everyone can read.

## Who talks to it

- **The operator**: `deploy/tell.sh --mind "..."` leaves
  `notes/inbox/operator-<ref>.md` (written inside the spine by
  `bin/broker operator-note`). The prompt names these as the one voice it
  takes instructions from. Nothing else writes that file name.
- **The tick**: the intent `hand_to_mind` leaves `notes/inbox/tick-*.md`, at
  most 12 a day, framed as Teeny's own thought and never as an instruction.
  Text a stranger got into a beat can therefore not arrive as the
  operator's voice.

What the mind did is in `state/home/notes/JOURNAL.md` and in the ledger.

## What holds it

| Fence | How |
|---|---|
| The outbound guard | The mind has no credentials, so it cannot post, mail, push or pay except through a hand, and every hand runs the guard |
| The kill switch and watchers | `secrets/`, `bin/`, `hands/`, `evals/` and the live `brain/` are outside its jail. `MIND=0` stops its wake, the broker and the hand-over. `DRY_RUN` and every cap are read from `secrets/` |
| Its own definition | `brain/agents/mind.toml` is frozen: a self-edited tool list or sandbox setting would be a way out. `bin/doctor` refuses a mind with a browser, a scheduling tool, a wallet tool, a connector, a wildcard, or a workspace other than `state/home` |

The mind can write anything in its home, including a link to a file
outside it. The tick, the broker and the operator's note write there from
outside the jail, so they go through `bin/lib/mind_home.py`, which opens
each folder without following links and renames a fresh file into place. A
link the mind leaves in its inbox is replaced, never written through.

Limits to know about:

- Two wakes can overlap: the core never runs one job twice at once, but the
  pinned wake and a wake the mind set are different jobs. Its prompt has it
  append to its journal for that reason. A real single-flight across jobs
  needs the core's per-job policy exposed to `cron_add` upstream.

- The jail is on files, not on the network. The mind cannot speak as Teeny
  outside the guard, because it has none of Teeny's accounts. It can still
  fetch and send as an anonymous client. Closing that needs a network rule
  in the sandbox upstream.
- The jail exists because `secrets/` is mounted in the same container as
  the core. Docker protects the host from the container; it does not
  protect the keys from a shell inside it. Running the mind in a container
  of its own with no `secrets/` mount would let the jail go.

## Turning it on

1. Set `MIND=1` in `secrets/teeny.env` on the box. The reaches it should
   have are the tick's caps in the same file (`MIND_TASKS_PER_DAY`,
   `MIND_SEARCHES_PER_DAY`, `MIND_BUILDS_PER_DAY` and so on).
2. Check that no evolve generation is running, then recreate the spine.
   The boot log says `broker: started for the agentic mind`.
3. `deploy/tell.sh --mind "..."` to give it a first direction, or leave it
   to pick its own.
4. Read a day or two of `state/broker/ledger.jsonl`. When what it asks for
   is what you want sent, set `MIND_DRY_RUN=0` and recreate the spine.

To watch it: `state/home/notes/JOURNAL.md`, `state/broker/ledger.jsonl`,
and `bin/crons` for the `teeny.mind` and `mind.*` runs.

## Not built yet

- **Changing itself directly.** The mind can file a change through
  `do propose_change`, which goes to the builder and opens a pull request a
  person merges. Pushing a branch from its own clone and having it merge on
  green CI is the next step.
- **Owning every pipeline.** The two dreams, the builder, the lore episode
  and the Moltbook prune are in `brain/cron.toml` now, Teeny's to
  reschedule, pause or drop. Evolve, the wants feed and the desks stay
  pinned for now; the watchers and the money jobs stay pinned for good.
- **Sub-agents and a browser of its own.** A child of a cron run is
  read-only in the core today; browsing goes through `do queue_task`.
