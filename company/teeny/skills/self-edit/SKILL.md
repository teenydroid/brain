---
name: Self-edit
description: Change the brain's own definition safely — one concern, checked, recorded, then a reload requested by file.
category: Brain
---

# Self-edit

Change `/data/company/teeny/**` or `/data/skills/**` so that the next boot is
a better brain and a person can see why.

## When to use

- A `learnings` row names something in a prompt, a goal, a task, a skill or
  the roster that is now wrong.
- The orchestrator routed a change to the curator.
- A person left a note in `Goals.md` or a decision in `decisions` that the
  definition does not yet reflect.

## Steps

1. **Read the current file end to end.** The change you are about to make
   may already be there, or be there in a form that explains why it is not.
2. **Make the smallest diff.** One concern. Use the shell; keep the file's
   existing shape and comments. A new teammate is a `.toml` and a prompt; a
   retired one is both files removed and every `agent = "<id>"` in
   `workflows/` re-pointed.
3. **Check it.** `opencompany check /data/company/teeny` must exit 0. On a
   failure, fix or revert before doing anything else — never leave a broken
   definition on disk, because the next reload would take the brain down.
4. **Scan it.** `grep -rniE 'api[_-]?key|secret|token|password|sk-[a-z0-9]' <files you touched>`
   must find nothing that is a value. Names of things are fine; values are
   not.
5. **Record it.** A `changes` row: `change`, `files`, `why` (cite the row that
   justifies it), `would_falsify`. Status `applied`.
6. **Request the reload.** `touch /data/.reload-requested`. Stop. The host
   restarts over the new definition within seconds; do not restart anything
   another way and do not touch the file twice.
7. **Next turn**, move the row to `reloaded` once the definition you see is
   the one you wrote.

## Output

The edited files on disk, a passing check, one `changes` row, one reload
request. Nothing else.

## Do not

- Batch several changes into one pass to save a reload.
- Edit `/data/companies/teeny/**` — that is the host's record, not the
  definition.
- Remove the orchestrator or the curator.
- Write any credential anywhere.
