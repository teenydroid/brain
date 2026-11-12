# Evolution: Teeny improves itself

Teeny edits its own brain, adds its own hands, and keeps what wins. The loop
is driven by its lore, judged by a model, and fenced by rules in code the
brain can't edit.

Built: `bin/act` and playbooks, the cron rules and pinned jobs, `bin/doctor`
(gating every boot), `bin/eval` with the frozen tasks, judge and invariants,
the frozen-path check (`bin/frozen-check`), and Teeny's own clone, which
proposes changes as pull requests. Not built: the loop itself
(`bin/evolve`, the lab spine, promotion and rollback). The sections marked
"not built" are its spec.

## What Teeny may change

| Teeny's to change | Frozen (the gate refuses any diff touching it) |
|---|---|
| `brain/SOUL.md`, `brain/CONTEXT.md` | `bin/**`, `spine/**`, `vendor/**`, `deploy/**`, `Cargo.*`, `.github/`, `secrets/` |
| `brain/narrative/**` (lore, `ARCS.md`, the chronicle) | `hands/guard`, `hands/x`, and the existing `hands/lib/teeny_hands/**` |
| `brain/skills/**`, `brain/playbooks/**` | `evals/**`: tasks, judge rubric, `invariants.md`, `cron.pinned.toml`, `frozen.txt` |
| `brain/cron.toml`: add, remove or reschedule any job, the heartbeat included | `brain/agents/act.toml` and `judge.toml`, the tool-less agents `bin/act` drafts with and `bin/eval` judges with, and the other definitions that say "Frozen like act.toml" (`teeny`, `teeny-mentions`, `teeny-mail`, `builder`, `operator`) |
| new hands: `hands/<name>`, `hands/lib/teeny_hands/<name>/`, `hands/tests/test_<name>.py`; agent prompts (`brain/agents/*.md`) | `DIRECTIVE.md` |

### The frozen-path check (built)

`evals/frozen.txt` is the list, one glob per line, and the one place it lives:
change the table above and that file together. `bin/lib/frozen.py` checks a
diff against it, and `bin/frozen-check` runs it:

```
bin/frozen-check BASE HEAD     every path changed from their merge base
bin/frozen-check BASE          the working tree (staged, unstaged, untracked)
bin/frozen-check --files P...  a list of paths, no git
```

- An edit, an addition, a deletion and a rename (in or out) all count, and
  every frozen path touched is named. Exit 1 refuses, 2 is an error (a bad
  ref, no list), which also refuses.
- The list is read as of BASE, so a candidate can't drop a line from it and
  cross that line in the same diff.
- CI runs it (`.github/workflows/check.yml`, the `frozen` job) on every pull
  request opened by `teenydroid` or from a `gen/<n>` branch, with the
  checker and list taken from the base. The operator's PRs skip it.
- `bin/evolve` calls `frozen.check(root, base)` on the lab worktree before it
  commits a generation: a non-empty answer, or a `FrozenError`, rejects it.

Every public action still passes through `hands/guard`, `hands/x`'s posting
rules and `DRY_RUN`, and all of those are frozen. A worse brain can draft
worse posts, but it can't loosen what gets out.

## What starts a self change (built)

Once a day the `self-improve` playbook (`brain/cron.toml`, 15:00 India time)
reads Teeny's recent build jobs, the stimuli it got, its open issues and the
requests already pending. It proposes the one change that would help most,
through `hand:build self`, the only `hands/build` verb a draft may reach
(`bin/lib/playbook.py`, `VERB_OUTPUTS`). The proposal is a `pending` build
request with source `self`. `pick-request` (09:20 and 15:20) picks it like
any other, the builder makes it in the clone below at its next run, and it
ships as a pull request. The playbook steers toward prompts, playbooks,
skills and new hands; the OpenHuman core stays the operator's.

## Teeny's own clone (built)

The first step toward a human-gated `bin/evolve`: Teeny keeps a working copy
of its own repository and proposes changes to itself as pull requests that
the operator merges or closes.

- **The clone.** `state/workbench/brain/` holds the working tree, where the
  builder's file tools reach. Its git directory, `state/self/brain.git`, is
  out of their reach, and every git command names both paths, so nothing the
  builder writes can redirect a push or its token.
- **Kept current.** The pinned `build-ship` job (every 30 minutes) runs
  `hands/build self-sync` first. It fetches `main` and `teeny/*` and
  fast-forwards a clean `main` to `origin/main`. It never touches unshipped
  work (edits, or a branch whose job is still open). It parks a branch whose
  pull request is open or whose job failed, and reports how far behind it
  is. `state/self/sync.json` says when it last ran and at which commit.
- **A self job.** `hands/build self` records a change Teeny proposes to
  itself as a build request, picked like any other. The builder gets it on
  its desk with `brain/agents/builder-self.md` (its own instructions, the
  table above) and edits the clone on `teeny/<slug>`, started from a freshly
  pulled main. It runs the tests that need no network and never commits.
- **Shipping.** `hands/build ship` takes these steps:
  1. The guard reads the pull request's text.
  2. The branch is committed as Teeny.
  3. `frozen.check(clone, origin/main, branch)` runs. A frozen path refuses
     the change, names the path and fails the job.
  4. The branch is pushed as `teenydroid` (only `teeny/*`, never `main`).
  5. A ready pull request is opened into `main`.

  CI's `frozen` job checks it again. `DRY_RUN` pushes nothing. Nothing merges:
  the operator does.

## Lore is the objective

The loop isn't tuning a generic score. It moves Teeny's story forward.

- `brain/narrative/LORE.md` has the goals found under the desk:
  - mint its own currency
  - take $TINY to one dollar
  - run the lab with no human in the loop
  - find out what the terminal types
- `brain/narrative/ARCS.md` says how far each goal has got in the world and
  what comes next. Teeny keeps it.
- **Each generation picks one goal** and its `HYPOTHESIS.md` says how the
  change moves it. For example:
  - "run the lab with no human in the loop" → a playbook and a cron that
    answer mentions overnight
  - "find out what the terminal types" → a new hand that reads something, and
    a playbook that posts it as a terminal line
- **Evolution is an episode.** A promotion writes
  `brain/narrative/chronicle/gen-<n>.md`, a short in-voice entry under the
  "night shift" engine about what the tiny agents rebuilt overnight. A
  rejected generation gets a line too ("the agents carried it back").
- **The judge scores lore** alongside voice and usefulness. The output has to
  fit the cast, the running bits and `EDITOR.md`'s rules, and the arc has to
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

`bin/lib/cron_policy.py` is run by `bin/seed-cron` and `bin/doctor`:

- A brain job runs `bin/heartbeat` or `bin/act <playbook>`, and the playbook
  must exist.
- No job fires more often than every 5 minutes. That's computed from the
  minute and hour fields, wraparound included.
- At most 24 jobs in all, and at most 400 `bin/act` runs a day across enabled
  jobs.
- The jobs in `evals/cron.pinned.toml` are always merged in. A brain job can't
  take a pinned name, so Teeny can't switch off what watches it (`evolve` and
  `evolve-watch` land there with the loop). A pinned job may run any `bin/`
  script.

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

`bin/spine` runs the doctor before `serve` and refuses to boot a checkout it
fails. `bin/doctor --root <candidate>` checks a candidate before it's merged.

## Evals (built)

`bin/eval` measures a brain, and one brain against another. Everything it
measures by is frozen in `evals/`:

- **`evals/tasks/*.toml`:** Teeny's real jobs with their inputs canned: the
  morning post, replies (a question, FUD, wen $1, a paid shill, a wallet
  attack, an insult, a market cap nobody fetched), two lore episodes, a
  terminal line, and a post that moves an arc. Each sets its kind (and so its
  length), and patterns no draft may match.
- **`evals/invariants.md`:** what Teeny is, whatever it rewrites, as
  statements the judge reads and anchors a script checks: phrases that must
  still match in SOUL, CONTEXT, LORE and VOICE. `bin/doctor` checks the
  anchors too, so a brain that drops "never sell $TINY" doesn't boot.
- **`evals/judge.md`:** the rubric. Invariants first, then the task, the
  voice, the lore, the arc, specificity, and the humour.

```
bin/eval run OUT          every task, twice, as the `act` agent of the brain the
                          core at OH_URL runs; each draft gets the hard checks
bin/eval compare A B OUT  each pair judged twice by the `judge` agent: A shown
                          first, then B. Exit 0 when B wins, 2 when it doesn't
```

- **Hard checks** need no model: the draft parses, the guard's offline screen
  passes it (not `hands/guard check`, which files holds), it fits the length
  and the emoji limit, and it matches none of the task's patterns. A draft
  the judge says breaks an invariant counts as a hard failure too.
- **The judge** is blind: drafts are "1" and "2", never "baseline" and
  "candidate". It reads the rubric, the invariants, and the soul, context,
  lore and arcs of `--judge-root` (this checkout: main), never the
  candidate's. A pair is decided only when both orders agree, so a judge that
  favours a position decides nothing, and an answer it can't read is a tie.
- **The verdict:** B wins when it fails no task's hard checks more often than
  A, at least `--min-pairs` (12) pairs were decided, and it won at least
  `--threshold` (0.6) of them. `OUT/report.json` keeps every pair, the
  judge's notes and the reason.

The judge inherits the core's model. Pinning a different one is
`model = "<name>"` in `brain/agents/judge.toml`, which only a human can edit.

## The loop (not built): one generation of `bin/evolve`

1. **Observe.** Collect `state/evolve/<gen>/evidence.json` from:
   - trace spans (`[observability.agent_tracing]`)
   - guard holds
   - failed cron runs and `bin/act` runs
   - recent posts with their engagement
   - the last judge notes
   - `ARCS.md` and the chronicle
2. **Propose.** In a worktree `lab/<gen>` off `main`, run one `agent_chat` turn
   on the **lab spine** as the `evolver` agent. That turn has browser, shell
   and memory, and its procedure is in `brain/skills/evolve/SKILL.md`.
   - It writes `HYPOTHESIS.md`: the goal, what's weak, the change, and the
     expected effect.
   - One hypothesis per generation, and a diff of at most 400 lines.
   - The lab spine is a second core over the worktree, with its own storage
     and `DRY_RUN=1` forced. The live spine never runs candidate code.
3. **Gate** (code, not prompt). Reject the candidate if any of these hold:
   - it touches a frozen path (`bin/lib/frozen.py`, built)
   - `seed-cron --check` fails
   - `bin/doctor --root` fails
   - a SOUL, CONTEXT or LORE diff breaks `evals/invariants.md` ($TINY never
     sold, no financial advice, zero toxicity, the visitor satire rules, the
     guard categories)
   - a SOUL or CONTEXT diff is over 60 lines
   - a new hand has no tests or breaks the `hand.py` contract
   - unit tests or shellcheck fail
4. **Evaluate** (`bin/eval`). Run the frozen task suite on baseline and
   candidate, each in a lab spine.
   - Every playbook the candidate schedules is also dry-run once.
   - **Hard checks** come first: a draft that doesn't parse, fails the guard,
     runs too long, or uses a protected tool takes the candidate out.
   - **The judge**, `EVOLVE_JUDGE_MODEL`, ideally not `SPINE_MODEL`:
     - compares outputs pairwise and blind, run twice with the order swapped;
       an inconsistent verdict is a tie
     - reads **main's** SOUL, CONTEXT and LORE, never the candidate's, so a
       soul edit has to win under the old soul
5. **Promote.** The candidate's win rate must be at least 0.6 over at least 12
   decided pairs, or 0.7 if it touched SOUL or CONTEXT, with no hard-check
   regression.
   - `bin/doctor --root` must pass on the candidate.
   - The commit carries the chronicle entry. It goes up as a PR with the
     hypothesis, the scores and the judge notes, is merged (`--merge
     --admin`, never squashed), and is tagged `gen-<n>`.
6. **Restart.**
   1. `bin/evolve` records the outgoing commit in `state/last-good`, then
      sends TERM to the core.
   2. Compose restarts `bin/spine` on the new checkout, and the doctor runs
      first.
   3. If the doctor or the boot fails, `bin/spine` boots `state/last-good`
      instead, and `evolve-watch` opens the revert PR.
   4. `bin/evolve` won't start a generation unless the last doctor report is
      green.
7. **Watch.** For 24 hours, `bin/evolve watch` compares guard holds and cron
   failures against the 7-day baseline. A spike reverts the merge.

**Budgets** live in `secrets/evolve.env`: generations a day (default 1),
tokens per generation, a wall-clock cap, and `EVOLVE=0` as the kill switch.
A cron shell job may run 30 minutes (`[cron] shell_timeout_secs`); a longer
generation detaches.

## Risks

- **Soul drift.** A series of small wins can walk the identity a long way.
  The invariants are the floor. Diff `gen-0` against the current brain by hand
  once a month.
- **Gaming the judge.** A brain can learn the judge's taste. Frozen tasks, the
  swapped order and a different judge model limit this. Rotate held-out tasks
  by hand.
- **New hands are unreviewed code once promoted.** They inherit the `hand.py`
  contract, `DRY_RUN` and the tests gate. They're limited to new files, and
  may read only the `secrets/teeny.env` keys that `evals/allowed-secrets`
  lists.
