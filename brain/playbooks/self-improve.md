+++
description = "propose one small change to myself: a prompt, a playbook, a skill or a new hand"
output = "hand:build self"
surface = "github"
max_per_day = 1

[[inputs]]
name = "builds"
run = "hands/build jobs --limit 10"

[[inputs]]
name = "pending"
run = "hands/requests list --status pending --max 20"

[[inputs]]
name = "issues"
run = "hands/gh issues teenydroid/brain --max 20"

[[inputs]]
name = "recent"
run = "hands/stimuli recent --max 30"

[[inputs]]
name = "world"
run = "hands/world report --days 7"

[[inputs]]
name = "archive"
run = "hands/build jobs --status shipped --limit 30"

[[inputs]]
name = "lessons"
run = "hands/lab lessons --max 10"

[[inputs]]
name = "people"
run = "hands/people list"
+++
Propose one change to yourself, the one that would help you most today. The builder makes it in your own clone and opens it as a pull request, and the operator merges it or closes it.

Look at what went wrong or went missing lately:
- `builds`: jobs that failed, and why;
- `recent`: what you were asked or told that you couldn't handle well;
- `issues`: problems people filed against you;
- `people`: who you keep talking with, and the promises you made them. A promise you keep missing, or a regular you keep answering badly, is evidence too.

Also look at what your lore asks of you that you can't do yet.

Then look at how you are growing:
- `world`: your world score W and its growth score J over the last 7 days, computed by frozen code. Read its `reading` lines first. The component with the smallest `contribution`, or one with a `note` saying it has no data, is usually where the most is left.
- `archive`: your past self changes. They are the jobs of `kind` `self`; ignore the other kinds. Each has its `summary` and its `pr_url`. Don't propose again what is already there, merged or closed.
- `lessons`: what your lab's experiments found about what gets attention, newest first, each with its verdict. A change that follows a lesson (a playbook that writes more of what `works`, less of what `fails`) beats a guess.

Pick the single change that fixes the most for the least. It must be one of:
- an agent prompt (`brain/agents/*.md`) or a playbook (`brain/playbooks/*.md`) that answers better, skips the right things, or stops repeating itself;
- a skill (`brain/skills/**`), or a job in `brain/cron.toml`;
- a new hand (`hands/<name>`, `hands/lib/teeny_hands/<name>/` and its tests) for something you keep needing and can't do.

Never propose a change to anything frozen. That covers `bin/`, `spine/`, `vendor/`, `deploy/`, `evals/`, `.github/`, `DIRECTIVE.md`, the agent definitions (`*.toml`), `hands/guard`, `hands/x`, `hands/world` (the scorer) and the existing hand library. The OpenHuman core is the operator's: if the fix belongs there, skip and say so. Nothing touching wallets, keys, money or the guard.

Skip only when one of your own changes is still in flight: a `self` request in `pending`, or a job of kind `self` in `builds` that is not finished (`failed` and `shipped` are finished). One at a time.

Failed jobs are not a reason to wait. They are evidence, and often the very thing to fix. A job "abandoned: the builder never reported back" means the builder never finished: its run broke, it didn't get past your brief. If the cause is outside what you may change (the sandbox, the core, the model), skip and say so; otherwise propose the change that would get the next build through.

Make it a delta edit. A change to an existing prompt, playbook or skill adds, edits or retires one bullet (or one short paragraph) in one file. Never rewrite a whole file, and never touch two files to make one point. A new hand or a new playbook is a new file, and that is the only time you write a whole one.

Write it as a brief for the builder, in a few short paragraphs:
1. What to change: the file, and the one bullet you add, edit or retire in it.
2. Why: the evidence above, quoted or counted.
3. What it moves: the W component (attention, tiny, money, capability, shipped or world), how far you expect it to move in 7 days (a size from the `world` input, like "attention Δlog +0.02"), and which step of the flywheel it turns: act, attention, mindshare, holding or capability.
4. How you will know it worked: what `hands/world report` should show a week after it merges.

It must be small enough to build and test in one run. Answer {"action": "hand", "text": "<the brief>"}. If nothing is worth changing today, skip with the reason.
