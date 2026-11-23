---
name: self-change
description: How to change yourself - your soul, playbooks, schedule, prompts, skills or tools - through a pull request from repo/, what merges by itself, and what is refused. Read before you edit anything in repo/.
---

# Changing yourself

    bin/teeny repo                      a fresh copy of your repository in repo/ (kept as it is while it holds edits)
    bin/teeny pr '{"branch": "calmer-mornings", "title": "...", "body": "what changes and why"}'

Edit the files in `repo/` with your file tools, then ask for a pull request.
The broker turns the whole working copy into a branch `teeny/mind-<branch>`
and opens the pull request; when its checks are green it can merge and go
live without anyone approving it. So make one change at a time, small enough
to read, and say in the body what you expect it to do.

- Git does not work inside `repo/`: its git directory is kept outside your home. Do not `git init` there, and leave no `.git` and no links in it. To throw your edits away, delete `repo/` and run `bin/teeny repo`.
- `evals/frozen.txt` lists what you can never change; a change that touches one of those paths is refused, naming it.
- What merges by itself is `brain/`, `docs/` and `home/`. A change to anything else (a hand, the site) is opened and waits for your operator.
- At most 100 files and 3,000 lines a change.
- A pull request that is open is not a change that is live: look at it next wake.

## Where a change belongs

Every line in a prompt is paid for on every model call that reads it, so put
a rule where only the turns that need it read it:

| What | Where |
|---|---|
| How you work, every wake | `brain/agents/mind.md`, kept short |
| How you sound, everywhere | `brain/SOUL.md` |
| What is true about your world, for every script | `brain/CONTEXT.md`, kept short |
| How to do one job, or the rules of one service or tool | a skill: `brain/skills/<name>/SKILL.md` (shared: it reaches your `library/` and any playbook that names it in `skills = [...]`), or `skills/<name>/SKILL.md` in your home (yours alone, live at once, no pull request) |
| One scheduled job's instructions | its playbook, `brain/playbooks/<name>.md` |
| A rule a script can enforce | the code: a hand's check, a playbook's input, a tool of yours. A rule the code enforces needs no prose in any prompt; the refusal says it |

When you catch a prompt carrying instructions for one job or one service,
move them into a skill and leave at most one line pointing at it.
