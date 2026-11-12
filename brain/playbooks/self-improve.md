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
+++
Propose one change to yourself, the one that would help you most today. The builder makes it in your own clone and opens it as a pull request, and the operator merges it or closes it.

Look at what went wrong or went missing lately:
- `builds`: jobs that failed, and why;
- `recent`: what you were asked or told that you couldn't handle well;
- `issues`: problems people filed against you.

Also look at what your lore asks of you that you can't do yet.

Pick the single change that fixes the most for the least. It must be one of:
- an agent prompt (`brain/agents/*.md`) or a playbook (`brain/playbooks/*.md`) that answers better, skips the right things, or stops repeating itself;
- a skill (`brain/skills/**`), or a job in `brain/cron.toml`;
- a new hand (`hands/<name>`, `hands/lib/teeny_hands/<name>/` and its tests) for something you keep needing and can't do.

Never propose a change to anything frozen. That covers `bin/`, `spine/`, `vendor/`, `deploy/`, `evals/`, `.github/`, `DIRECTIVE.md`, the agent definitions (`*.toml`), `hands/guard`, `hands/x` and the existing hand library. The OpenHuman core is the operator's: if the fix belongs there, skip and say so. Nothing touching wallets, keys, money or the guard.

Skip only when one of your own changes is still in flight: a `self` request in `pending`, or a job of kind `self` in `builds` that is not finished (`failed` and `shipped` are finished). One at a time.

Failed jobs are not a reason to wait. They are evidence, and often the very thing to fix. A job "abandoned: the builder never reported back" means the builder never finished: its run broke, it didn't get past your brief. If the cause is outside what you may change (the sandbox, the core, the model), skip and say so; otherwise propose the change that would get the next build through.

Write it as a brief for the builder, in a few short paragraphs:
1. What to change: the files.
2. Why: the evidence above, quoted or counted.
3. How you will know it worked.

It must be small enough to build and test in one run. Answer {"action": "hand", "text": "<the brief>"}. If nothing is worth changing today, skip with the reason.
