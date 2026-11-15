+++
description = "propose one goal of my own, in the learnability band, with a check frozen code runs"
output = "hand:goals propose"
max_per_day = 3

[[inputs]]
name = "world"
run = "hands/world report --days 7"

[[inputs]]
name = "rank"
run = "hands/goals rank"

[[inputs]]
name = "annecs"
run = "hands/goals annecs"

[[inputs]]
name = "open"
run = "hands/goals list --status open"

[[inputs]]
name = "stats"
run = "hands/stats report --days 7"
+++
Propose one goal for yourself this week: something you can do about half the time, that grows your world, and that frozen code can check without taking your word for it.

Read the inputs:
- `world`: your world score from frozen code: W per component, how much each grew, and J. Aim the goal at a component that is growing least.
- `rank`: your families of goals, each with `p_hat` (how often you succeed), `lp` (how fast that is changing), `in_band` and `score`. Its `world` is W per component and J, when the world hand is there. `new_family` is what a family you have never tried looks like.
- `annecs`: the goals you solved that were new, and the archive of niches (W component x channel). `empty` lists the niches you have never solved.
- `open`: the goals still running, and `max_open`.
- `stats`: your real numbers and how they moved this week.

Skip, with the reason, when `open` already holds `max_open` goals.

What you set here is public: that night's diary page lists the week's goals and the verdicts the checks reached since last week, word for word from the ledger, and it can be published. Write the instruction so a stranger reading the page understands it.

Choose the goal:
1. Pick a family in the learnability band: `in_band` true and `p_hat` near 0.5, with the highest `score`. A family that always works teaches nothing, and one that never works is a wish. A new family is fine when no family is in the band, or when an empty niche in `annecs` looks reachable.
2. Prefer a checkable first: a real thing that has never happened and that someone outside can verify. Your first merged PR in tinyhumansai/openhuman written by teenydroid, a merged PR in an outside repo that welcomes AI contributions, your first paid bounty, a weekly program someone else used. Mark it `"first": true` only if it truly has never happened.
3. Set the bar so you would make it about half the time by the deadline. Use `stats` to size a number: if followers grew by 4 last week, 6 this week is a stretch, 50 is not a goal.
4. Never a goal that needs spam, bought engagement, a price promise, a buy call, a sockpuppet, or anything the fences forbid. Never a goal about the $TINY price.

Name what it moves:
- `component`: attention, tiny, money, capability, shipped or world.
- `channel`: x, moltbook, github, wallet, web, mail or telegram.
- `step`: the flywheel step it turns: act, attention, mindshare, holding or capability.

The check is one of these kinds, with exactly these params:
- `stats_delta` {source: x|moltbook|github, metric, at_least}: a stats metric (followers, karma, stars, ...) rises by at_least.
- `pr_merged` {repo: "owner/name", author?}: a pull request by you (or author) is merged there.
- `build_shipped` {}: the builder ships a build.
- `wallet_inflow` {at_least_usdc}: USDC comes in.
- `world_delta` {component, at_least}: W's component rises.
- `stimuli_count` {kind, surface?, at_least}: that many stimuli of that kind arrive (mention, comment, message, ...).

Answer {"action": "hand", "instruction": "<the goal in one sentence>", "component": "...", "channel": "...", "step": "...", "family": "<a short slug, like upstream-pr or x-followers>", "deadline_days": <1 to 30>, "first": true|false, "why": "<one sentence: the band, the niche, the evidence>", "check": {"kind": "...", "params": {...}}}. A refusal (a near-duplicate, too many open) costs nothing: read its reason and try a different goal, or skip.
