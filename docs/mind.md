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
| Skills | `skills/<name>/SKILL.md`, listed in `skills/INDEX.md`: what it has taught itself, read at each wake |
| Tools | `shell`, `file_read`, `file_write`, `edit`, `glob`, `grep`, `list`, `tool_search`, `memory`, `spawn_subagent` (its worker only) |
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

## How it changes itself

`state/home/repo/` is a working copy of Teeny's own repository
(`hands/lib/teeny_hands/build/mindclone.py`). The mind edits files there and
asks the broker for a pull request:

```
bin/teeny repo                 a fresh checkout of main (left alone while it holds edits)
bin/teeny pr '{"branch": "calmer-mornings", "title": "...", "body": "..."}'
```

The working copy is hostile input, so the broker treats it as one:

- Its git directory is `state/mind/repo.git`, outside the jail. The mind
  cannot run git there, plant a hook or edit git's config, and a `.git`
  anywhere in the tree is refused.
- Only plain files are carried. A link, a hard link, a nested repository or
  a file that differs from what git staged is refused.
- The title and body pass the guard. The diff is checked against
  `evals/frozen.txt` as it is on `main`, so a change cannot unfreeze what it
  touches, then against the secret scan and the size limits (100 files,
  3,000 lines).
- The branch is `teeny/mind-<name>`, pushed and opened through `hands/gh`,
  and the body ends by saying an autonomous AI agent wrote it.
- `MIND_PRS_PER_DAY` caps it; `0`, the default, keeps it off.

What happens next is the babysitter's (`docs/hands.md`, "babysit"). With
`BABYSIT_SELF_MERGE_ALL=1`, a pull request of Teeny's own from any `teeny/`
branch merges with no person's approval once every job of the check
workflow is on its head and green, as long as the audit finds nothing else
and the change stays inside `brain/`, `docs/` and `home/`. Anything outside
those (a hand, the site) can run outside the mind's jail, so it is opened
and waits for a person. After a merge the babysitter deploys it; if the
doctor is red afterwards, or the spine fell back to its last good checkout,
it records the deploy as bad, opens a "revert #N" issue and holds self-merge
on the paths that change touched for a day. While `main` is still that
deploy it also opens the revert itself: a pull request `teeny/revert-N-<sha>`
that puts back the tree the box ran before. That pull request touches the
held paths, so it waits for the operator like any other change to them.

## Helpers

The mind can hand a piece of work to a helper and carry on: `spawn_subagent`
with `agent_id` `worker` and a prompt. The worker
(`brain/agents/worker.toml`, prompt `worker.md`) has the mind's shell and
file tools and nothing else: no memory, no helpers of its own, nothing that
schedules, browses or spends. The mind's run waits for its workers before
it ends, and the run's 30 minutes cover all of them.

It is held exactly as the mind is. The core runs a spawned agent in its
parent's workspace with its parent's file tools, so the worker's shell is
jailed to `state/home/`, and it acts on the world only through `bin/teeny`.
The core lets a child of a cron run use acting tools only when the child's
own definition opts in (`scheduled_actions`), is sandboxed and names its
tools, and only one level down: a worker's own child, if it could have one,
would be read-only. `bin/doctor` refuses a worker with a workspace of its
own, a wildcard, or any tool the mind lacks, and refuses `[subagents]` on
any agent but the mind.

Several workers write the same home at once and nothing locks it, so the
mind's prompt has it give each one its own folder to write.

## Its own tools

`home/` in the repository is Teeny's own code (`home/README.md`). A tool is
a folder, `home/tools/<name>/`, with a `run.py`, a `run.sh` or an executable
`run`. The mind writes it in its working copy, opens the pull request, and
because the change is inside `home/` it merges on green CI with no person
reading it.

That is only safe if the code never runs with anything of the operator's in
reach, so there are exactly two places it runs:

- the mind's own shell, inside the mind's jail;
- `bin/box <name>` (`bin/lib/box.py`), which runs the tool from the live
  checkout under the same kind of jail (`bin/lib/jail.py`, the kernel's
  Landlock). The tool can read `home/` and the system, and write only
  `state/box/tools/<name>/`. It cannot read or list `secrets/`, `brain/`,
  `bin/`, `hands/`, `.git` or the rest of `state/`, and it gets none of the
  caller's environment. A syscall filter refuses chmod, chown, utime and
  xattr calls that name a file by its path, which Landlock does not cover:
  without it a tool could make `secrets/teeny.env` unreadable. On a kernel
  with Landlock 6 or newer (Linux 6.12) it also cannot signal a process
  outside the jail; on versions 3 to 5 it can, so there a tool could kill
  the spine, which compose restarts. `bin/box check` prints the version.

A job in `brain/cron.toml` may run a tool: `command = "bin/box <name>"` with
up to 8 plain words after it. That is how Teeny gives itself a pipeline that
needs no model: a scraper, a counter, a report. A tool acts on the world
only through the broker (`"$TEENY_CLI" do '{...}'`), so the guard, the caps
and the rehearsal hold it as they hold the mind. What it prints is left in
the mind's inbox as `notes/inbox/tool-<name>.md`, marked as data, and each
run is one line in `state/box/ledger.jsonl`.

It is off until the operator sets `BOX=1` in `secrets/teeny.env`; off, a job
that names a tool does nothing and is not an error. `BOX_TIMEOUT_SECS`
(240, at most 270) is one run's limit: the core gives a cron shell call
300 s. When a run ends, times out or is stopped, everything the tool
started is killed before the next run may begin, and only the last 16 KiB
of what it printed is kept. On a kernel that cannot jail (no Landlock, or
older than its version 3) `bin/box` runs nothing and `bin/doctor` fails
while `BOX=1`.

What this does not do: the hands stay as they are. A hand that posts, mails,
pushes or pays holds a key, and code that holds a key can leak it or skip
the guard wherever it runs. Those change only by a pull request a person
merges, until the keys sit behind something that enforces the guard for
them.

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

## Reach

What the mind may touch outside its own repositories is the hands' own
rules, set by the operator in `secrets/teeny.env`:

| Reach | Through | Setting | Default |
|---|---|---|---|
| Pull requests to other projects | `hands/gh open-pr` | `GH_MAX_EXTERNAL_PRS_PER_DAY` | 2 |
| Issues on other projects | `do open_issue` → `hands/gh open-issue` | `GH_MAX_EXTERNAL_ISSUES_PER_DAY` and `MIND_ISSUES_PER_DAY` | 0 (off) |
| Comments on other projects | `do comment_gh` | none | on |
| Browser tasks | `do queue_task` | `MIND_TASKS_PER_DAY` | 0 (off) |
| Mail it starts | `do start_mail` | `MIND_MAILS_PER_DAY` | 0 (off) |
| Paid search | `do web_search` | `MIND_SEARCHES_PER_DAY` | 0 (off) |

A pull request or an issue on someone else's project says it was opened by
Teeny, an AI agent, and who operates it (`DISCLOSURE`, appended by code),
is refused where the project's contributing policy does not welcome AI, and
waits while one of Teeny's is still open there. After one of its pull
requests is closed unmerged, a comment there that names anybody is held for
a person (the guard's `rejection` category; `evals/invariants.md`,
no-reprisal). That rule is in the guard, so it holds whatever the soul
comes to say.

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

Each step is a setting in `secrets/teeny.env`, and each can be taken alone.

1. `MIND=1`: the mind wakes and rehearses. Check that no evolve generation
   is running, then recreate the spine; the boot log says
   `broker: started for the agentic mind`. Give it a first direction with
   `deploy/tell.sh --mind "..."`, or leave it to pick its own.
2. `MIND_DRY_RUN=0`: what it asks for is sent. Read a day or two of
   `state/broker/ledger.jsonl` first. The broker holds `DRY_RUN=1` in its
   process environment while `MIND_DRY_RUN=1`, so editing `secrets/teeny.env`
   alone changes nothing: check that no evolve generation is running, then
   recreate the spine (and so the broker) again. What it may reach is the tick's caps
   in the same file (`MIND_TASKS_PER_DAY`, `MIND_SEARCHES_PER_DAY`, ...).
3. `MIND_PRS_PER_DAY=3` (say): it may open pull requests from its clone.
   The broker reads `secrets/teeny.env` once at start, so recreate the spine
   (after the same evolve check) before it will take a request.
   They wait for a person until the next step.
4. `BABYSIT_SELF_MERGE_ALL=1` (with `BABYSIT_SELF_MERGE=1`, and
   `BABYSIT_MERGE=1`, which the merge action checks before it audits
   anything; at its default of 0 nothing merges): its pull
   requests, the builder's and evolve's merge on green checks and deploy.

To watch it: `state/home/notes/JOURNAL.md`, `state/broker/ledger.jsonl`,
and `bin/crons` for the `teeny.mind` and `mind.*` runs.

## Not built yet

- **Owning every pipeline.** The two dreams, the builder, the lore episode
  and the Moltbook prune are in `brain/cron.toml` now, Teeny's to
  reschedule, pause or drop. Evolve, the wants feed and the desks stay
  pinned for now; the watchers and the money jobs stay pinned for good.
- **Self-merged hands.** Teeny's own tools merge by themselves
  (`home/tools/`); a hand that holds a key does not. Lifting that means
  moving every key behind a proxy that runs the guard, so that a hand
  asks for a send instead of making it.
- **A browser of its own.** Browsing goes through `do queue_task`.
