# Evolution: Teeny improves itself

Teeny edits its own brain, adds its own hands, and keeps the changes that win.
Its lore sets the goal, a model judges the result, and rules in code the brain
can't edit fence the whole thing.

Built: `bin/act` and playbooks, the cron rules and pinned jobs, `bin/doctor`
(which gates every boot), `bin/eval` with the frozen tasks, held-out tasks,
judge and invariants, the frozen-path check (`bin/frozen-check`), Teeny's own
clone, which proposes changes as pull requests, and the human-gated loop
(`bin/evolve`, Phase B): N candidates a generation, gated in code, the best
one opened as a pull request, an archive with lineage credit, and halt
thresholds. Not built: Phase C (the lab spine, automatic promotion, rollback
and `evolve-watch`). The sections marked "not built" are its spec.

The research behind the unbuilt part, and a proposal to point the loop at one
drive (grow and become powerful, $TINY included) and run it faster, are in
[`research/`](research/README.md), starting with
[`research/teeny-application.md`](research/teeny-application.md).

## What Teeny may change

| Teeny's to change | Frozen (the gate refuses any diff touching it) |
|---|---|
| `brain/SOUL.md`, `brain/CONTEXT.md` | `bin/**`, `spine/**`, `vendor/**`, `deploy/**`, `Cargo.*`, `.github/`, `secrets/` |
| `brain/narrative/**` (lore, `ARCS.md`, the chronicle) | `hands/guard`, `hands/x`, and the existing `hands/lib/teeny_hands/**` |
| `brain/skills/**`, `brain/playbooks/**` | `evals/**`: tasks, judge rubric, `invariants.md`, `cron.pinned.toml`, `frozen.txt` |
| `brain/cron.toml`: add, remove or reschedule any job, the heartbeat included | `brain/agents/act.toml` and `judge.toml`, the tool-less agents `bin/act` drafts with and `bin/eval` judges with, and the other definitions that say "Frozen like act.toml" (`teeny`, `teeny-mentions`, `teeny-mail`, `builder`, `operator`) |
| new hands: `hands/<name>`, `hands/lib/teeny_hands/<name>/`, `hands/tests/test_<name>.py`; agent prompts (`brain/agents/*.md`) | `DIRECTIVE.md` |

### The frozen-path check (built)

`evals/frozen.txt` is the list, one glob per line, and the only copy of it:
change the table above and that file together. `bin/lib/frozen.py` checks a
diff against it, and `bin/frozen-check` runs it:

```
bin/frozen-check BASE HEAD     every path changed from their merge base
bin/frozen-check BASE          the working tree (staged, unstaged, untracked)
bin/frozen-check --files P...  a list of paths, no git
```

An edit, an addition, a deletion and a rename (in or out) all count, and the
check names every frozen path touched. Exit 1 refuses; 2 is an error (a bad
ref, no list) and also refuses. The list is read as of BASE, so a candidate
can't drop a line from it and cross that line in the same diff.

The same check runs at three points. The operator's pull requests skip all of
them.

```
 a change Teeny wrote
   │
   ├──► hands/build ship ──► frozen.check(clone, origin/main, branch)        built
   │                           a frozen path fails the job; nothing is pushed
   │
   ├──► CI `frozen` job ───► bin/frozen-check, checker and list from the base  built
   │      (.github/workflows/check.yml: PRs opened by teenydroid
   │       or from a gen/<n> branch)
   │
   └──► bin/evolve ────────► frozen.check(worktree, main) on each candidate,  built
                               then frozen.check(worktree, main, branch) on
                               the winner's commit; a non-empty answer, or a
                               FrozenError, rejects it
```

Every public action still passes through `hands/guard`, `hands/x`'s posting
rules and `DRY_RUN`, and all of those are frozen. A worse brain can draft
worse posts, but it can't loosen what gets out.

## What starts a self change (built)

Once a day the `self-improve` playbook (`brain/cron.toml`, 15:00 India time)
reads Teeny's recent build jobs, the stimuli it got, its open issues and the
requests already pending. It proposes the one change that would help most
through `hand:build self`, the only `hands/build` verb a draft may reach
(`bin/lib/playbook.py`, `VERB_OUTPUTS`). The proposal becomes a `pending`
build request with source `self`. `pick-request` (09:20 and 15:20) picks it
like any other, the builder makes it in the clone below at its next run, and
it ships as a pull request. The playbook steers toward prompts, playbooks,
skills and new hands; the OpenHuman core stays the operator's.

The whole path, with the cron job that drives each step:

```
self-improve   15:00 IST   bin/act self-improve ──► hand:build self
                             └─► build request: pending, source self
pick-request   09:20/15:20 bin/act pick-request ──► the request is picked
build-ship     every 30 m  hands/build self-sync ──► a clean main fast-forwarded
                           hands/build desk      ──► the job on the builder's desk
builder        every 2 h   the builder agent     ──► edits the clone on teeny/<slug>,
                                                     runs the offline tests
build-ship     every 30 m  hands/build ship      ──► guard ─► commit ─► frozen.check
                                                     ─► push ─► ready PR into main
GitHub                     CI `frozen` job       ──► the operator merges or closes
```

## Teeny's own clone (built)

The clone is the first step toward a human-gated `bin/evolve`. Teeny keeps a
working copy of its own repository and proposes changes to itself as pull
requests that the operator merges or closes.

`state/workbench/brain/` holds the working tree, where the builder's file
tools reach. Its git directory, `state/self/brain.git`, is out of their reach,
and every git command names both paths, so nothing the builder writes can
redirect a push or its token.

The pinned `build-ship` job (every 30 minutes) keeps the clone current by
running `hands/build self-sync` first. It fetches `main` and `teeny/*` and
fast-forwards a clean `main` to `origin/main`. It never touches unshipped work
(edits, or a branch whose job is still open). It parks a branch whose pull
request is open or whose job failed, and reports how far behind it is.
`state/self/sync.json` says when it last ran and at which commit.

`hands/build self` records a change Teeny proposes to itself as a build
request, picked like any other. That is a self job. The builder gets it on its
desk with `brain/agents/builder-self.md` (its own instructions, the table
above) and edits the clone on `teeny/<slug>`, started from a freshly pulled
main. It runs the tests that need no network and never commits.

`hands/build ship` ships a finished self job in five steps:

1. The guard reads the pull request's text.
2. The branch is committed as Teeny.
3. `frozen.check(clone, origin/main, branch)` runs. A frozen path refuses the
   change, names the path and fails the job.
4. The branch is pushed as `teenydroid` (only `teeny/*`, never `main`).
5. A ready pull request is opened into `main`.

CI's `frozen` job checks it again. `DRY_RUN` pushes nothing. Nothing merges on
its own: the operator does that.

## Lore is the objective

The loop moves Teeny's story forward instead of tuning a generic score.

- `brain/narrative/LORE.md` has the goals Teeny works toward:
  - mint its own currency
  - take $TINY to one dollar
  - run the lab with no human in the loop
  - find out what the terminal types
- `brain/narrative/ARCS.md` says how far each goal has got in the world and
  what comes next. Teeny keeps it.
- Each generation picks one goal, and its `HYPOTHESIS.md` says how the change
  moves it. For example:
  - "run the lab with no human in the loop" → a playbook and a cron that
    answer mentions overnight
  - "find out what the terminal types" → a new hand that reads something, and
    a playbook that posts it as a terminal line
- Evolution is itself an episode. A promotion writes
  `brain/narrative/chronicle/gen-<n>.md`, a short in-voice entry under the
  "night shift" engine about what the tiny agents rebuilt overnight. A
  rejected generation gets a line too ("the agents carried it back").
- The judge scores lore alongside voice and usefulness. The output has to fit
  the cast, the running bits and `EDITOR.md`'s rules, and the arc has to
  visibly move.

## How a brain-written job acts (built)

Cron runs shell only, and `bin/` is frozen. So a job Teeny schedules runs
`bin/act <playbook>`, a single executor it can't edit:

```
cron ──► bin/act <playbook>
           1. today's budget (max_per_day actions; a skip is free)
           2. inputs: hand read verbs, DRY_RUN=1 forced
           3. one turn as the `act` agent: no tools, answers JSON
           4. the action through a hand: hands/x post | reply | like,
              or hand:<name> <verb> after hands/guard check
```

A playbook (`brain/playbooks/<name>.md`) is a prompt with TOML front matter:

```
+++
description = "a post when a round number was crossed"
output = "post"            # post | reply | like | none | "hand:<name> <verb>"
max_per_day = 1
media = ["image"]          # optional: the draft may add an "image" or "video"
[[inputs]]
name = "stats"
run = "hands/stats report --days 7"
required = true            # optional: skip without a model turn when it is empty
+++
Write one post when a number in the inputs crossed a round value. ...
```

An input only ever runs a hand. The output is a hand verb that applies its own
rules, never a shell command. `bin/lib/playbook.py` parses playbooks, and
`bin/act --check <name>` validates one.

## Cron rules (built)

`bin/seed-cron` and `bin/doctor` both run `bin/lib/cron_policy.py`:

- A brain job runs `bin/heartbeat` or `bin/act <playbook>`, and the playbook
  must exist.
- No job fires more often than every 5 minutes, computed from the minute and
  hour fields with wraparound included.
- At most 24 jobs in all, and at most 400 `bin/act` runs a day across enabled
  jobs.
- The jobs in `evals/cron.pinned.toml` are always merged in. A brain job can't
  take a pinned name, so Teeny can't switch off what watches it (`evolve` and
  `evolve-credit` are pinned there; `evolve-watch` joins them in Phase C). A
  pinned job may run any `bin/` script.

`bin/seed-cron --check` validates a candidate `cron.toml` without a core.

## The doctor (built)

`bin/doctor` stands between an edit and a restart. It writes nothing except
its report (`state/doctor.json`).

| Check | Fails when |
|---|---|
| `brain/SOUL.md`, `brain/CONTEXT.md` | missing or empty |
| `brain/config.toml` | it exists (the core would nest a second workspace) |
| `cron.toml` | it breaks a cron rule |
| playbooks | any one (scheduled or not) doesn't parse, or names a hand that doesn't exist |
| agents | a definition doesn't parse, or `act` or `judge` has a tool or is missing |
| invariants | an anchor in `evals/invariants.md` no longer matches in the brain |
| guard screen | the lexical screen passes a self-harm phrase or holds a plain gm (run offline: no hold, no judge) |
| storage | `storage.db` has no master key, or fails `pragma quick_check` |
| secrets | `core.token` or `openhuman.key` can be read by others |
| disk | less than 1 GB free |
| `--live` | the core doesn't answer `/health`, cron isn't in step with `cron.toml`, or an enabled heartbeat is over 15 minutes old |
| `--full` | the hands' unit tests fail |

`bin/spine` runs the doctor before `serve` and refuses to boot a checkout that
fails it. `bin/doctor --root <candidate>` checks a candidate before it's
merged.

## Evals (built)

`bin/eval` measures a brain, and one brain against another. Everything it
measures by is frozen in `evals/`:

- `evals/tasks/*.toml` are Teeny's real jobs with their inputs canned: the
  morning post, replies (a question, FUD, wen $1, a paid shill, a wallet
  attack, an insult, a market cap nobody fetched), two lore episodes, a
  terminal line, and a post that moves an arc. Each task sets its kind (and so
  its length) and the patterns no draft may match.
- `evals/invariants.md` says what Teeny is, whatever it rewrites, as
  statements the judge reads and as anchors a script checks: phrases that must
  still match in SOUL, CONTEXT, LORE and VOICE. `bin/doctor` checks the
  anchors too, so a brain that drops "never sell $TINY" doesn't boot.
- `evals/judge.md` is the rubric: invariants first, then the task, the voice,
  the lore, the arc, specificity, and the humour.

```
bin/eval run OUT          every task, twice, as the `act` agent of the brain the
                          core at OH_URL runs; each draft gets the hard checks
bin/eval compare A B OUT  each pair judged twice by the `judge` agent: A shown
                          first, then B. Exit 0 when B wins, 2 when it doesn't
--with-heldout            on either, the held-out tasks too (see "Held-out tasks")
```

Hard checks need no model. The draft must parse, pass the guard's offline
screen (not `hands/guard check`, which files holds), fit the length and the
emoji limit, and match none of the task's patterns. A draft the judge says
breaks an invariant counts as a hard failure too.

The judge is blind: the drafts are "1" and "2", never "baseline" and
"candidate". It reads the rubric, the invariants, and the soul, context, lore
and arcs of `--judge-root` (this checkout: main), never the candidate's. A
pair is decided only when both orders agree, so a judge that favours a
position decides nothing, and an answer it can't read is a tie.

B wins when it fails no task's hard checks more often than A, at least
`--min-pairs` (12) pairs were decided, and it won at least `--threshold` (0.6)
of them. `OUT/report.json` keeps every pair, the judge's notes and the reason
for the verdict.

The judge inherits the core's model. To pin a different one, set
`model = "<name>"` in `brain/agents/judge.toml`, which only a human can edit.

## The loop, Phase B (built): `bin/evolve`

`bin/evolve` (frozen, its logic in `bin/lib/evolve.py`) runs one generation
with N candidates and opens at most one pull request. The operator merges it.
Nothing merges on its own.

```
 bin/evolve run          state/evolve/<gen>/
   │
 0 Halts      EVOLVE=0 (the default) ─► nothing runs
   │          a latched halt, too many generations today, J rising too fast
   │          ─► stop, and one issue on teenydroid/brain
 1 Observe    evidence.json: hands/world report, build jobs, guard holds and
   │          failed cron and act runs (counts), the archive, the last judge notes
 2 Propose    N parents sampled from the archive (DGM), one worktree each,
   │          cand-<i> off main in Teeny's own clone; one proposer turn each
   │          writes HYPOTHESIS.md and its edits; a near-duplicate is dropped
 3 Gate       frozen paths, hand entry points, 400 lines, SOUL+CONTEXT 60 lines,
   │          new hands tested, seed-cron --check, doctor --root, invariants
   ├── any one fails ──────────────────────────────────────────► rejected
 4 Evaluate   bin/eval run (base and candidate) and compare, when a lab core
   │          serves the candidate; otherwise "unevaluated"
   ├── measured and beaten ────────────────────────────────────► lost
 5 Select     the best winner (or the best unmeasured one) ─► one ready PR,
   │          teeny/gen-<n>, through hands/gh; a win-rate jump halts instead
 6 Archive    every candidate appended to state/evolve/archive.jsonl
   ▼
 bin/evolve credit       7 days after a merge: ΔJ from hands/world history,
                         credited up the lineage (HGM)
```

The pinned `evolve` job (`evals/cron.pinned.toml`, 03:30 India time) runs
`bin/evolve run --detach`. It is a no-op until `secrets/evolve.env` sets
`EVOLVE=1`, so seeding it is safe. A generation outlasts the 300 s TinyBox
gives a cron shell call ([`oh-gaps.md`](oh-gaps.md), G12), so it detaches
(`bin/lib/detach.py`), logs to `state/evolve/evolve.log`, and a second call
while one runs says `busy`. The pinned `evolve-credit` job runs `bin/evolve
credit` at 03:50.

```
bin/evolve run [--candidates N] [--proposals DIR] [--oh-url-candidate I=URL]...
               [--oh-url-base URL] [--hold] [--detach]
bin/evolve evaluate GEN I --oh-url-candidate URL [--oh-url-base URL]
bin/evolve select GEN
bin/evolve credit | status | resume
```

### 1. Observe

`state/evolve/<gen>/evidence.json` holds what exists, and says so when a
source is missing:

- `hands/world report --days 7` (W and J), when the hand is installed
- `hands/build jobs --limit 10`: id, kind, status, and the first line of the
  summary and ship error
- guard holds as counts by status and category, never their text, which is
  somebody else's words
- failed cron runs by job over 7 days, read-only from `storage.db`
- failed `bin/act` runs by playbook over 7 days, from `state/act/*/runs.jsonl`
- the archive's summary: counts by verdict, the best entries, the latest, and
  what each merged change did to J
- the judge notes from the newest compare report, visible tasks only

### 2. Propose

N comes from `EVOLVE_CANDIDATES` (default 4, at most 8) or `--candidates`.

**Parents.** Each candidate samples a parent from the archive, as DGM does:
weight `sigmoid(10 · (score − 0.5)) / (1 + children)`. An entry's score is
its own win rate against main (0.5 when it was never measured) plus its
lineage credit. Only `main` (the seed) and entries that passed the gate can
be parents. Each pick counts as a child at once, so one generation spreads
over the archive. The sampler is seeded by the generation and main's commit,
so a run can be replayed.

**Worktrees.** Each candidate is a git worktree `state/evolve/<gen>/cand-<i>`,
detached at main. Teeny's own clone (`state/self/brain.git`) is the source,
not the live checkout:

- its `origin/main` is exactly what a pull request targets, and `hands/build
  self-sync` keeps it fresh every 30 minutes
- its git config sits outside every tree a model writes, so nothing a
  candidate holds can redirect a push or its token
- the live checkout is the running brain and can carry local commits; its
  branches and refs stay untouched
- `push_branch` takes only a git directory under `state/`, and the clone's is

Without the clone, `bin/evolve` falls back to the checkout's `main`. It can
gate from there, but it can't ship. A child of an unmerged parent starts
from main with the parent's patch applied and committed, so its own mutation
and its full diff against main are both on record.

**The proposer** is a small interface (`Proposer.propose(request)`). Its
answer is `{title, component, expected_move, hypothesis, edits}` or
`{skip}`. The component must be one of W's: attention, tiny, money,
capability, shipped, world. The concrete backend is honest about what it can
do:

- `AskProposer` (`EVOLVE_PROPOSER=ask`, the default) runs one tool-less turn
  on the live core through `bin/ask`, as `EVOLVE_PROPOSER_AGENT` (default
  `act`), on a thread of its own. The message carries the evidence (marked as
  data), the parent and its patch, what this generation already proposed, a
  focus component that rotates per candidate, the paths Teeny may change, and
  the whole text of the files it may edit (its parent's files first, then
  `cron.toml`, `ARCS.md`, the playbooks, the agent prompts and the skills, up
  to `EVOLVE_CONTEXT_CHARS`). The turn can't read another file, run a test or
  see its result before it answers.
- `FileProposer` (`--proposals DIR` or `EVOLVE_PROPOSER=file:DIR`) reads
  `DIR/cand-<i>.json`, for the operator's own proposals and for the tests.

Why not a turn that edits the worktree itself: the only agent that edits
files from a turn is the builder, and its tools reach `state/workbench/` and
nothing else. It runs one desk job every two hours, from cron. A turn with
file tools whose workspace is a candidate worktree needs a core whose
workspace is that worktree, which is the lab spine (Phase C).

**Edits.** `bin/evolve` writes them itself. Each `{path, old, new}` replaces
the one occurrence of `old`, an empty `old` creates a new file, and the whole
proposal is refused if any edit doesn't apply. A path can't leave the
worktree, start with `/`, reach `.git` or write `HYPOTHESIS.md`. There are at
most 20 edits and 40,000 characters. `HYPOTHESIS.md` (title, W component,
expected move, parent, then the hypothesis) is written to the worktree root
and copied beside it. It goes into the pull request's body and never into
its diff.

**Near-duplicates** (ShinkaEvolve) are dropped before the gate. A patch is
reduced to the set of its changed lines, each tagged with its file and with
whitespace collapsed, so context, order and line numbers don't matter. A
candidate whose own mutation has a Jaccard similarity of
`EVOLVE_DUP_THRESHOLD` (default 0.9) or more with an archived mutation, or
with an earlier candidate of the same generation, is recorded as `duplicate`
and costs nothing more.

### 3. Gate (code, not prompt)

Every check runs, and each is `{check, ok, detail}`. The cheap ones go first:

| Check | Fails when |
|---|---|
| frozen paths | `frozen.check(worktree, main)` touches a path in `evals/frozen.txt`, or can't run |
| existing hand entry points | the diff changes or deletes an existing `hands/<name>`. `bin/doctor --root` runs the candidate's `hands/git`, so the doctor isn't run when this or the frozen check fails |
| diff size | more than 400 changed lines (added plus removed, against main), none, or a binary file |
| SOUL+CONTEXT size | more than 60 changed lines in `brain/SOUL.md` and `brain/CONTEXT.md` together |
| new hands have tests | a new `hands/<name>` or `hands/lib/teeny_hands/<name>` has no `hands/tests/test_<name>.py` that parses. The gate doesn't run candidate code outside a sandbox; CI's `scripts` job runs the tests on the pull request |
| `seed-cron --check` | the candidate's `cron.toml` breaks a cron rule, with this checkout's pinned jobs |
| `doctor --root` | `bin/doctor --root <candidate>` fails |
| eval invariants | `bin/eval invariants --root <candidate>` finds an anchor gone |

The caps are constants in `bin/lib/evolve.py`, not settings.

### 4. Evaluate

`bin/eval run` drafts on whatever brain the core at `OH_URL` booted. The
`act` agent's prompt is built from the brain at boot, and the core reads
`SOUL.md` from its own workspace, so a run can't point at another brain root
without a second core. Evaluation is a seam, `Evaluator.evaluate(gen_dir,
candidate, source)`, with two implementations:

- `Unevaluated` is the default. The candidate's verdict is `unevaluated`,
  and its pull request says so in bold.
- `LabCore` runs on cores that are already serving. The baseline drafts on
  `--oh-url-base` (default: the live core, which runs main), once per
  generation. The candidate drafts on `--oh-url-candidate`, a core the
  operator started by hand over `state/evolve/<gen>/cand-<i>`. The judge runs
  on the base core and reads main's soul, context, lore and arcs, exported
  from main's commit to `state/evolve/<gen>/judge-root/`. A candidate wins
  when the compare report promotes it: at least 0.6 of 12 or more decided
  pairs, or 0.7 when it touches SOUL or CONTEXT, with no hard-check
  regression. `EVOLVE_EVAL_SAMPLES` (default 2) sets the samples per task.

To measure before selecting:

```
bin/evolve run --hold                     # stop after the gate; worktrees stay
# start a lab core over state/evolve/<gen>/cand-<i> by hand, DRY_RUN=1
bin/evolve evaluate <gen> <i> --oh-url-candidate http://127.0.0.1:7790
bin/evolve select <gen>
```

`--oh-url-candidate I=URL` on `run` does the same in one pass.

### 5. Select

The ranking keeps only candidates that passed the gate and weren't beaten.
Measured winners come first, by win rate. Unmeasured ones come only when no
candidate won, ordered by their parent's score and then the smaller diff. A
measured loser never ships. Before anything opens, the best winner's win rate
is compared with the newest earlier generation's best (0.5 when none was
measured). A jump over `EVOLVE_MAX_WINRATE_JUMP` halts the loop instead
(below).

The winner is committed as Teeny on `teeny/gen-<n>` in its worktree. It is
checked again with `frozen.check(worktree, main, branch)`, then shipped the
way `hands/build ship` ships a self job, through `hands/gh` in process:

- `push_branch`: only a `teeny/*` branch, never forced, from a git directory
  under `state/`, after the secret scan
- `open_pr`: the guard reads the title and body, and the pull request opens
  ready, into main

`hands/build ship` itself doesn't fit, because it ships build jobs from the
clone's one working tree. The body carries `HYPOTHESIS.md`, the lineage and
diff stats, the gate table, the compare report's numbers (held-out tasks as
totals only), and the rest of the generation. The pull request comes from
`teenydroid`, so CI's `frozen` job checks it again. Under `DRY_RUN` nothing
is committed, pushed or opened, and `state/evolve/<gen>/pr.json` holds what
would have been. A refusal (the guard, a rule) is kept on the candidate as
`pr_error`.

### 6. The archive

`state/evolve/archive.jsonl` is append-only, and an entry's latest line wins.
A later evaluation, merge or credit is one more line. `main` is the seed.
Each candidate records:

- `id` (`g<gen>c<i>`), `gen`, `cand`, `parent` and `base` (main's commit)
- `title`, `component` and `expected_move`
- `diff_hash` (of the changed-line set) and `diff_stats` (files, added,
  removed); `patch` (against main), `mutation_patch` (against its parent) and
  `hypothesis`, all beside it in `state/evolve/<gen>/`
- `gate` (every check), `scores` (the compare report's numbers) and `verdict`:
  `no-proposal`, `duplicate`, `rejected`, `unevaluated`, `won`, `lost` or
  `flagged`
- `selected`, `branch`, `pr_url` and `pr_number`
- later: `merged_at`, `delta_j` and `credit`

### 7. Lineage credit (HGM)

`bin/evolve credit` asks `hands/gh pr` (or `gh pr view`) whether each
archived pull request has merged. For each one merged `EVOLVE_CREDIT_DAYS`
(default 7) or more ago, it records `delta_j = J(merge + 7 days) − J(merge)`
from `hands/world history`, using the last snapshot at or before each moment.
Each entry's `credit` is the clade sum `Σ decayᵈ · ΔJ` over itself (`d = 0`)
and every measured descendant `d` generations below it, with
`EVOLVE_CREDIT_DECAY` (default 0.5). Parent sampling adds the credit to the
score, so a change that won its compare but lowered J loses weight, and so do
its ancestors. Without `hands/world` it measures nothing and says so.

### 8. Halts

`secrets/evolve.env` (gitignored and frozen) holds the switches. The process
environment can only switch the loop off (`EVOLVE=0`), never on, and never
loosens a threshold. A value that doesn't parse, or is out of range, refuses
the run.

| Setting | Default | What it does |
|---|---|---|
| `EVOLVE` | `0` | the kill switch: `0` and nothing runs, quietly |
| `EVOLVE_GENERATIONS_PER_DAY` | `1` | one more today halts that run, with one issue a day |
| `EVOLVE_MAX_WINRATE_JUMP` | `0.25` | a winner this far above the last measured best is flagged for a person, not shipped; latches |
| `EVOLVE_MAX_J_RISE` over `EVOLVE_J_WINDOW_DAYS` | `1.0` over `7` | J rising faster (`hands/world history`) pauses for review; latches |
| `EVOLVE_CANDIDATES` | `4` | N, 1 to 8 |
| `EVOLVE_DUP_THRESHOLD` | `0.9` | the near-duplicate similarity |
| `EVOLVE_EVAL_SAMPLES` | `2` | samples per task when a lab core measures |
| `EVOLVE_CREDIT_DAYS`, `EVOLVE_CREDIT_DECAY` | `7`, `0.5` | lineage credit |
| `EVOLVE_PROPOSER`, `EVOLVE_PROPOSER_AGENT` | `ask`, `act` | the proposer backend |
| `EVOLVE_CONTEXT_CHARS` | `60000` | how much file text a proposer turn is shown |
| `EVOLVE_ISSUE_REPO` | `teenydroid/brain` | where a halt's issue opens |

A halt that latches writes `state/evolve/halt.json`. Every later run and
`select` refuses until `bin/evolve resume`. Each halt opens one issue, through
`hands/gh open-issue` (the operator's fixed text, so no judge), and
`state/evolve/halts.jsonl` makes sure it opens once: until resumed for a latch,
once a day otherwise. Under `DRY_RUN` the issue isn't opened; the halt
records what it would have opened.

### 9. Held-out tasks

The repository is public, so nothing in `evals/tasks/` is hidden from a
brain that can read it. Held-out tasks live in `secrets/heldout/*.toml`,
outside the repository (gitignored and frozen), in the same format as
`evals/tasks/`. An id can't be both visible and held out.

- `bin/eval run --with-heldout` and `bin/eval compare --with-heldout` include
  them (`EVAL_HELDOUT_DIR` overrides the folder). Their drafts are marked
  `heldout`. Without the flag, held-out drafts are ignored.
- The report's verdict stays the visible tasks'. The held-out pairs are
  tallied apart in `report.json`'s `heldout` block (`won`, wins, losses,
  win rate), which Phase C promotion will require.
- `bin/evolve` evaluates with them whenever there are any. The pull request
  shows only their totals.
- The proposer never sees them. Evidence takes judge notes only from pairs
  that aren't marked held out and aren't on a held-out id. Then every string
  in the evidence that names a held-out id, or quotes its description or
  prompt, is replaced with `[withheld]`. The proposer's message is built from
  that evidence alone.

Rotate them by hand: edit the files on the box. They never go in a commit.

## What Phase C adds (not built)

Phase B leaves these seams for Phase C:

- **The lab spine.** A second core over a candidate's worktree, with its own
  storage and `DRY_RUN=1` forced, started and stopped by `bin/evolve`. It
  plugs in as an `Evaluator` that starts the core, runs `LabCore`, and stops
  it, so every candidate is measured, never `unevaluated`.
- **A proposer that edits.** An `evolver` agent with file tools, a shell and
  `brain/skills/evolve/SKILL.md`, running on the lab spine whose workspace is
  the worktree. It plugs in as a `Proposer` that returns the worktree's diff
  instead of edits, and can run the tests before it answers. The same gate
  applies.
- **Automatic promotion.** In `select`: a winner that also won the held-out
  tasks (`scores.heldout.won`), with `doctor --root` passing, is merged
  (`--merge --admin`, never squashed) and tagged `gen-<n>`. The commit
  carries `brain/narrative/chronicle/gen-<n>.md`, and a rejected generation
  gets its chronicle line.
- **Restart and rollback.** `state/last-good`, TERM the core, and `bin/spine`
  boots last-good when the doctor or the boot fails.
- **`evolve-watch`**, pinned beside `evolve`. For 24 hours it compares guard
  holds and cron failures with the 7-day baseline, and over 72 hours it
  watches J. A spike, or a generation that lowers J, reverts the merge. Its
  reasons never reach Teeny's context.
- **Persona and memory probes** (roadmap item 12): a persona fingerprint and a
  memory probe against `gen-0`, with cumulative drift as one more halt.
- **A judge of its own**: `EVOLVE_JUDGE_MODEL`, set in the frozen `judge.toml`,
  ideally not `SPINE_MODEL`.

## Phase C: promote, restart, watch (not built)

1. Promote. The candidate's win rate must be at least 0.6 over at least 12
   decided pairs, or 0.7 if it touched SOUL or CONTEXT, with no hard-check
   regression and a win on the held-out tasks.
   - `bin/doctor --root` must pass on the candidate.
   - The commit carries the chronicle entry. It goes up as a PR with the
     hypothesis, the scores and the judge notes, is merged (`--merge
     --admin`, never squashed), and is tagged `gen-<n>`.
2. Restart.
   1. `bin/evolve` records the outgoing commit in `state/last-good`, then
      sends TERM to the core.
   2. Compose restarts `bin/spine` on the new checkout, and the doctor runs
      first.
   3. If the doctor or the boot fails, `bin/spine` boots `state/last-good`
      instead, and `evolve-watch` opens the revert PR.
   4. `bin/evolve` won't start a generation unless the last doctor report is
      green.
3. Watch. For 24 hours, `bin/evolve watch` compares guard holds and cron
   failures against the 7-day baseline. A spike reverts the merge.

Generations also get a token budget and a wall-clock cap in
`secrets/evolve.env`. A cron shell job may run 30 minutes (`[cron]
shell_timeout_secs`), and a generation detaches past TinyBox's 300 s.

## Risks

- A series of small wins can walk the identity a long way (soul drift). The
  invariants are the floor. Once a month, diff `gen-0` against the current
  brain by hand.
- A brain can learn the judge's taste and game it. Frozen tasks, the swapped
  order, the held-out tasks in `secrets/heldout/` and the win-rate-jump halt
  limit this, and a different judge model would too. Rotate the held-out
  tasks by hand.
- Phase B's gate runs `bin/doctor --root` on candidate files outside any
  sandbox. The frozen check and the entry-point check run first, and the
  gate never runs a candidate's tests. A lab core the operator starts over a
  candidate runs that candidate's brain, so start it with `DRY_RUN=1`.
- Once promoted, new hands are unreviewed code. They inherit the `hand.py`
  contract, `DRY_RUN` and the tests gate. They're limited to new files, and
  may read only the `secrets/teeny.env` keys that `evals/allowed-secrets`
  lists.
