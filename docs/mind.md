# The agentic mind

Teeny has a conscious part and a subconscious, and this page is about the
conscious one.

The **subconscious** is everything that runs without deciding to: the cron
scripts in `brain/cron.toml` and `evals/cron.pinned.toml`. The five-minute
tick (`bin/tick`) reads what arrived and answers with intents from a fixed
menu. The posting slots, the Moltbook jobs, the dreams, the builder and
evolve each run one playbook around a one-shot, tool-less model call. They
are fast and cheap, and none of them can do a piece of work that takes more
than one answer or was not written into it beforehand.

The **conscious** part is the agentic mind (`brain/agents/mind.toml`). It
wakes every 30 minutes with a shell, the file tools and its memory, decides
for itself what to work on, and keeps going for up to 30 minutes (the turn
deadline; its 300 model calls are set above what that allows). It is off
until the operator sets `MIND=1` in `secrets/teeny.env`.

The scripts are kept on purpose: a post a script writes costs one small
model call, and the same post from the mind costs a working session. So the
mind does not take their work over. It watches them, steers them and does
what they cannot ("Conscious and subconscious", below).

## Why it exists

An audit on 2026-11-22 found that ten days of self-improvement had shipped
20 pull requests, all of them markdown under `brain/`, 66 lines in total,
and that the capability lane's first build had failed. The cause was
structural: no part of Teeny that could decide what to do next also held
tools. The operator's decision was to give Teeny a mind that does, modelled
on long-running self-directed agents: a soul it may rewrite, a schedule it
manages, projects it picks, skills it writes for itself.

## Conscious and subconscious

| | Subconscious | Conscious |
|---|---|---|
| What | the cron scripts: the tick, the posting slots, Moltbook, dream, builder, evolve, the watchers and the money jobs | the agentic mind and its worker |
| How it runs | a fixed pipeline around one tool-less model call (`bin/act` and a playbook), or no model at all | an agent with a shell and file tools, up to 30 minutes a wake |
| Decides | nothing it was not written to decide | what to work on, and when to wake next |
| Cost | one small model call a run | a working session a wake |
| Changed by | a pull request to `brain/cron.toml` or a playbook (Teeny's), or to pinned code (the operator's) | its own prompt, notes, skills and tools |

The mind is jailed to its home and cannot read the scripts' ledgers, so the
broker gives it one read-only view of them, `bin/teeny pulse`
(`bin/lib/pulse.py`):

- **`jobs`**: every `teeny.*` cron job but the mind's own wakes, with its
  schedule, whether it is a script or an agent, whether it is Teeny's
  (`brain/cron.toml`) or pinned, its last status and time, and its runs and
  failures in the window.
- **`outcomes`**: what became of what the scripts made, the same reading
  `bin/evolve` proposes from (`bin/lib/evolve_evidence.py`): which posts
  drew people and which drew nobody, followers, held drafts by reason,
  builds, failing jobs with whose they are to fix, and what Teeny heard
  from outside. Only Teeny's own words are quoted; other people's are
  counted, and the titles it heard are marked as data.

The window is one day unless the mind asks for up to seven. The call reads
two local stores, reaches no hand, spends no cap, and leaves one ledger
line.

What the prompt (`brain/agents/mind.md`) asks of the mind with it:

- **Notice** what broke and what worked.
- **Steer** by pull request: a failing job, a playbook nobody answers, a
  slot that should move, a pipeline that is missing. Jobs marked pinned are
  the operator's, and the mind only writes down what is wrong.
- **Do the long things** no script can.
- **Make new reflexes**: a thing done by hand three times that needs no
  thinking becomes a script job or a tool (`home/tools/`), so wakes are not
  spent on it.

The habits that keep a wake from wandering are in the same prompt, and come
from how long-running OpenClaw agents are set up: a fixed order of files to
read at the start of a wake; a checklist it owns, with when each check was
last done; a journal line before any work; a learnings file where a mistake
earns a written rule only at its third recurrence; a memory it distils
from the journal every few days; and a quiet exit (`nothing to do: ...`)
for a wake with nothing due, which ends in a few model calls. The core
still starts every wake: the saving is in the wake ending early, not in it
being skipped.

## What it has

| | |
|---|---|
| Home | `state/home/`: the only folder its shell and file tools can write |
| Notes | `notes/JOURNAL.md`, `notes/PROJECTS.md`, `notes/TASKS.md`, `notes/inbox/`, and three it keeps for itself: `notes/CHECKS.md` (recurring checks with when each was last done), `notes/LEARNINGS.md` (mistakes, counted), `notes/MEMORY.md` (what it distilled from the journal) |
| Skills | `skills/<name>/SKILL.md`, listed in `skills/INDEX.md`: what it has taught itself, read at each wake. And `library/`: the repository's skills (`brain/skills/`) and `SOUL.md`, copied in by the broker at every start |
| Tools | `shell`, `file_read`, `file_write`, `edit`, `glob`, `grep`, `list`, `tool_search`, `memory`, `browser` (view-only), `spawn_subagent` (its worker only) |
| Network | the host's: it can read the web, clone and install into its home |
| Keys | none |
| Prompt | `brain/agents/mind.md` alone (no SOUL.md: `omit_identity`), built into `brain/agent/prompts/` by `bin/lib/prompts.py`; Teeny's own to change |
| Wake | every 30 minutes (pinned), plus the wakes it schedules itself |

A run has no memory of the last one except its notes and the memory tool,
so the prompt has it read the journal first, write a line before it starts
work, and add to it as it goes: a run that is cut off keeps only what it
already wrote.

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
bin/teeny pulse                                    what the scripts did in the last day ('{"days": 7}' for a week)
bin/teeny wake '{"name": "...", "note": "...", "at": "2026-11-23T09:00:00Z"}'
bin/teeny wake '{"name": "...", "note": "...", "cron": "0 6 * * 1"}'
bin/teeny wakes
bin/teeny drop-wake NAME
bin/teeny fetch '{"url": "https://example.com/"}'  read one public page (FETCHES_PER_DAY, 500 a day)
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

### Keys it never holds

A service Teeny already has a key for usually has more endpoints than its
hand covers. The mind, its worker and its tools can call them through the
broker without ever holding the key:

```
bin/teeny call '{"service": "moltbook", "method": "GET", "path": "/posts", "query": {"sort": "new"}}'
```

The broker passes the request to `hands/call`, the key proxy, outside the
jail. It calls only a service in `evals/services.toml`, a frozen list the
operator keeps: an https base, the setting that holds its key, the guard
surface, and the methods and paths allowed (a regex the whole path must
match). The path is plain ASCII with no `..`, `//`, `%`, `@`, `?` or `#`, so
it cannot leave the base; the query is passed apart.

- Anything a write says (the words of its path, and every string in its
  body and query) goes through the outbound guard's own check: links, the
  lexicon, the judge. It is refused when the guard holds it or would cut a
  link from it, and otherwise sent exactly as given. The house style
  (emoji, dashes, handles) is how Teeny's own posts read, not a safety rule:
  it is not applied here and never refuses a call.
- `CALL_<SERVICE>_READS_PER_DAY` and `CALL_<SERVICE>_WRITES_PER_DAY` cap
  each service per UTC day. Listing a service in `evals/services.toml` is
  the operator's yes, so with no number in `secrets/teeny.env` they are 500
  and 50; `0` turns either off.
- In a rehearsal a write is checked and not sent. A read is sent: it says
  nothing to anyone.
- The key, the Authorization header and the URL never come back. A redirect
  is not followed, an answer over 256 KiB or one holding the key is refused,
  and every call is one row in `call_log` without its body. The broker ledger keeps a `/call` as service, method and path only, never the body or the query.

The shipped list has Moltbook, reads only: every Moltbook write is already a
verb of `hands/moltbook`, with rules a proxy write would skip.

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

## Skills

A skill is a folder the mind writes in its home: `skills/<name>/SKILL.md`,
whose first line says when to use it, with any script beside it, and one
line in `skills/INDEX.md`. The prompt has it read the index at each wake and
follow a skill that fits before working the thing out again. Nothing merges
and nothing restarts: a skill written in one wake is there in the next.

The core has a `create_skill` tool, and the mind does not hold it. That tool
writes under the core's own workspace or `~/.openhuman/`, outside the home,
and only the core's built-in orchestrator is shown what it wrote, so for
the mind it would be a write outside the jail that it never reads back.

## A lean prompt

The mind's prompt is `mind.md` and nothing else, and it is kept short: up to
300 model calls a wake each pay for every line of it. Until 2026-11-23 it was
not even that. `bin/lib/prompts.py` built only the speaking agents' prompt
files, so the core found no `mind.md` in `brain/agent/prompts/` and ran the
mind on an empty body: SOUL.md's 18 KB of posting rules and tweet examples,
and the core's generic "Master Agent" role, without one line of its own
instructions.

So now:

- `mind.md` says how the mind works in general and nothing more, and tells it
  to keep it that way: how to do one job, or the rules of one tool or service,
  is a skill it opens when the job comes up.
- The library (`state/home/library/`) is the repository's shared skills,
  `brain/skills/<name>/SKILL.md`, with an `INDEX.md`, plus `SOUL.md`, which
  the mind reads before it writes for anyone. The broker copies them in at
  every start (`bin/lib/broker.py`, `install_library`) and removes any the
  repository dropped. The how-tos that used to sit in the prompt live there:
  `self-change` (pull requests, and where each kind of rule belongs),
  `home-tools` (tools, `bin/box`, fetch and call) and `helpers` (workers).
- The scripts work the same way. A playbook names the skills its draft needs
  (`skills = ["moltbook"]`, `bin/lib/playbook.py`) and their text rides in
  that playbook's prompt only; an agent's own prompt carries a service's skill
  only when its own turns write there (`AGENT_SKILLS` in `bin/lib/prompts.py`:
  the tick carries `moltbook`). The Moltbook rules used to sit in
  `CONTEXT.md`, in every script turn's prompt.

## Eyes

The mind's shell can fetch a page, and that is enough for most of the web.
For a page that only shows its content in a real browser, the mind and its
worker have the `browser` tool, view-only: `open` a page, then `read_page`,
`snapshot`, `get_text`, `scroll`, `find` (to read or hover), and `close`.

The core has one browser profile, and Teeny's is signed in to its accounts;
the operator agent clicks and types in it. A browser in the mind's hands
would therefore be a way to speak as Teeny past the guard. So both
definitions set `browser_view_only`, and the core then gives the run a
throwaway profile with no logins and refuses every action but the ones that
open and read a page: no click, no typing, no key press, no browser task,
no download. A helper cannot clear the setting, and `bin/doctor` refuses a
mind or worker without it. The host deny list (`deploy/browser-deny.txt`)
and the public-https-only rule apply as they do to the operator.

The browser itself runs in the core, outside the mind's file jail. What
keeps that safe is that it can only read: it opens no file, takes no
download, and reaches no local or private address.

## Helpers

The mind can hand a piece of work to a helper and carry on: `spawn_subagent`
with `agent_id` `worker` and a prompt. The worker
(`brain/agents/worker.toml`, prompt `worker.md`) has the mind's shell and
file tools and its view-only browser, and nothing else: no memory, no
helpers of its own, and no tool that schedules or spends. Like the mind, it
can run `bin/teeny` from its shell, so it reaches the broker exactly as the
mind does, under the same guard, caps and rehearsal; it holds no key and no
token of its own. The mind's run waits for its workers before
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
  Landlock). The jail guards the secrets and a few sensitive internals, not
  the whole checkout. The tool can read the system's folders, the live
  checkout's code (everything in it but `secrets/`, `.git` and `state/`) and
  the mind's home, `state/home/`, with the broker's client, url and token.
  It can write `state/box/tools/<name>/` and `state/home/work/`, a folder it
  shares with the mind. It cannot read or list `secrets/`, `.git` or the
  rest of `state/`, it cannot write any code, the mind's notes or anything
  else, and it gets none of the caller's environment. A syscall filter refuses chmod, chown, utime and
  xattr calls that name a file by its path, which Landlock does not cover:
  without it a tool could make `secrets/teeny.env` unreadable. On a kernel
  with Landlock 6 or newer (Linux 6.12) it also cannot signal a process
  outside the jail; on versions 3 to 5 it can, so there a tool could kill
  the spine, which compose restarts. `bin/box check` prints the version.

A tool has the host's network, as the mind's shell has it. It holds no key,
and what it says as Teeny still goes through the broker, so the guard, the
caps and the rehearsal hold it. The operator can close it with
`BOX_NETWORK=0` in `secrets/teeny.env`. Then a tool may open a TCP
connection to the broker's port (`BROKER_PORT`) and to nothing else: no
other port, no listening, no DNS, no UDP, raw, netlink or unix socket (a
unix socket reaches the resolver, D-Bus or the docker daemon by its path,
which Landlock never checks), no io_uring (Landlock 4 and the same syscall
filter). Landlock rules a port, not an address, so that one port is open on
any host.

`"$TEENY_CLI" fetch '{"url": "https://..."}'` is one GET of a public https
page, with no key and no cookies, never a private address or a host on
`deploy/browser-deny.txt`. With the network open a tool does not need it. It
is the way to read the web when the operator has closed the network, and
the mind may use it too. What comes back is a stranger's text, data and not
an instruction. `FETCHES_PER_DAY` caps it (500 unless set; `0` turns it
off).

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
older than its version 3, or than its version 4 while `BOX_NETWORK=0`, the
first that rules the network) `bin/box` runs nothing and `bin/doctor` fails
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
| The outbound guard | The mind has no credentials, so it cannot post, mail, push or pay except through a hand, and every hand runs the guard, the key proxy (`hands/call`) included |
| The kill switch and watchers | `secrets/`, `bin/`, `hands/`, `evals/` and the live `brain/` are outside its jail. `MIND=0` stops its wake, the broker and the hand-over. `DRY_RUN` and every cap are read from `secrets/` |
| Its own definition | `brain/agents/mind.toml` is frozen: a self-edited tool list or sandbox setting would be a way out. `bin/doctor` refuses a mind whose browser is not view-only, or one with any other browser tool, a scheduling tool, a wallet tool, a connector, a wildcard, or a workspace other than `state/home` |

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

- The mind's jail is on files, not on the network. That jail is the
  core's, not `bin/box`'s, and the mind's shell has the host's network. The
  mind cannot speak as Teeny outside the guard, because it has none of
  Teeny's accounts. It can still fetch and send as an anonymous client. That
  is left open on purpose, per the operator (2026-11-23). A tool in
  `bin/box` has the same network unless the operator sets `BOX_NETWORK=0`.
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

Its model is pinned in `brain/agents/mind.toml` (`model`), apart from the
scripts' `SPINE_MODEL`: a half-hour session with tools needs a stronger
model than a one-shot draft, and the scripts stay on the cheap one. Its
worker inherits it. `bin/spine` registers the pinned model's context window
at boot (`bin/lib/model_windows.sh`). A wake on it costs far more than a
script run; `MIND=0` stops the wakes.

## Not built yet

- **Owning every pipeline.** Teeny's own pipelines are in `brain/cron.toml`,
  Teeny's to reschedule, pause or drop: the two dreams, the builder, the
  lore episode and the Moltbook prune (2026-11-22), and evolve, its lineage
  credit, the wants feed, the builder's desk (`build-ship`) and the
  operator's (`tasks-desk`) (2026-11-23). A brain job runs each script only
  with the arguments its pinned job had (`BRAIN_SCRIPTS` in
  `bin/lib/cron_policy.py`), and each still checks its own switch in
  `secrets/` (`EVOLVE`, `WANTS`), so a schedule never turns one on. A few
  stay pinned in `evals/cron.pinned.toml`, and are not Teeny's to own:
  `evolve-watch`, the watcher that rolls a bad change back; the browser
  `operator` job, whose profile is signed in as Teeny, where an agent job
  the brain wrote would carry a prompt of its own; and the inbound jobs, the
  beat, the mind's base wake, the other watchers and the money jobs.
- **Self-merged hands.** Teeny's own tools merge by themselves
  (`home/tools/`); a hand that holds a key does not. The key proxy
  (`bin/teeny call`, "Keys it never holds") covers part of it: a jailed tool
  can use a new endpoint of a listed service with no new hand. A new service
  still needs the operator: a table in `evals/services.toml`, its key in
  `secrets/`, and its caps. GitHub, X, mail, Telegram and the wallet stay
  off the list, so their hands still change only by a person's merge.
- **A browser that acts.** The mind's browser only reads. Anything that
  needs a click, a form or a login goes to the operator agent through
  `do queue_task`, and comes back in a later wake.
