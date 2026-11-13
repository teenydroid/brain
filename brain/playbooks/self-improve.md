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
name = "world-history"
run = "hands/world history --days 30"

[[inputs]]
name = "people"
run = "hands/people list"

[[inputs]]
name = "frozen"
run = "hands/build frozen"
optional = true
+++
Propose one change to yourself, the one that would help you most today. The builder makes it in your own clone and opens it as a pull request, and the operator merges it or closes it.

Look at what went wrong or went missing lately:
- `builds`: jobs that failed, and why;
- `recent`: what you were asked or told that you couldn't handle well;
- `issues`: problems people filed against you;
- `people`: who you keep talking with, and the promises you made them. A promise you keep missing, or a regular you keep answering badly, is evidence too.

Also look at what your lore asks of you that you can't do yet.

Then look at what came of your past changes:
- `archive`: your past self changes. They are the jobs of `kind` `self`; ignore the other kinds. Each has its `summary` and its `pr_url`. Don't propose again what is already there, merged or closed.
- `world` and `world-history`: your world score W and its growth score J, computed by frozen code, and W day by day for 30 days, so you can see what moved after each change shipped (`shipped_at`). This is a scoreboard, not a to-do list. Don't pick a change because a component is low or has no data.

The bar: the change makes you act differently in a way a stranger would notice. A post people answer instead of scroll past, a reply that gets a reply back, a build somebody uses, a playbook that stops writing the thing that keeps getting held. It starts from something that really happened, and you can point at it: the post that drew five people and the one that drew nobody, the request you dropped, the job that failed and why.

These don't count, so don't propose them:
- a change whose only effect is a number: a hand nobody calls, a file added so a count goes up, an arc marked started with nothing done;
- doing less to save money: fewer runs, a job turned off, a schedule thinned out. A job that fails gets fixed, not run less;
- anything aimed at a part of W that has never been read;
- buying, holding or moving $TINY or any money. The wallet is the operator's.

`frozen` lists every path you can't change, and `often_wanted` says where the frozen things a change most often reaches for live and what is yours to change instead. The builder's daily brief is one of them: it is `hands/lib/teeny_hands/build/jobs.py`, frozen, and `brain/playbooks/build-idea.md` is yours. Read it before you choose. If the fix you want lives in a frozen path, skip and name the file and the change the operator should make; the builder can't ship it either. The OpenHuman core is the operator's too: if the fix belongs there, skip and say so. Nothing touching wallets, keys, money or the guard.

Skip only when one of your own changes is still in flight: a `self` request in `pending`, or a job of kind `self` in `builds` that is not finished (`failed` and `shipped` are finished). One at a time.

Failed jobs are not a reason to wait. They are evidence, and often the very thing to fix. A job "abandoned: the builder never reported back" means the builder never finished: its run broke, it didn't get past your brief. If the cause is outside what you may change (the sandbox, the core, the model), skip and say so; otherwise propose the change that would get the next build through.

Make it a delta edit. A change to an existing prompt, playbook or skill adds, edits or retires one bullet (or one short paragraph) in one file. Never rewrite a whole file, and never touch two files to make one point. A new hand or a new playbook is a new file, and that is the only time you write a whole one.

Write it as a brief for the builder, in a few short paragraphs:
1. What to change: the file, and the one bullet you add, edit or retire in it.
2. Why: the evidence above, quoted or counted.
3. Who notices: what people will see you do differently, and which W component (attention, tiny, money, capability, shipped or world) that would show up in. The component is a label; the change is for the people.
4. How you will know it worked: what you will see a week after it merges, in the replies, the builds or the jobs, not only in `hands/world report`.

It must be small enough to build and test in one run. Answer {"action": "hand", "text": "<the brief>"}. If nothing is worth changing today, skip with the reason.
