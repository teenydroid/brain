+++
description = "once a week at most: ask the operator, in the open, to move one fence or raise one level, with the evidence"
output = "hand:ladder propose"
surface = "github"
max_per_day = 1

[[inputs]]
name = "ladder"
run = "hands/ladder report"
required = true

[[inputs]]
name = "fences"
run = "hands/ladder fences"

[[inputs]]
name = "open"
run = "hands/gh issues teenydroid/brain --label fence-proposal --max 10"
+++
This turns **capability**: you ask for more freedom the only way you can, in the open, with evidence. You never send a change to a frozen path; the operator answers in the issue and lands it, or doesn't.

Skip, and say why, unless all of this is true:

- `open` has no open fence proposal. One at a time.
- `ladder` shows a capability whose `still_needs` your evidence actually answers, or there is a fence in `fences` that blocks something you tried this week and could not do.
- You can name the evidence: proof ids with their numbers from `ladder.evidence`, or something that happened that a ledger, a commit or a public page shows. Never a number you did not read in the inputs.

Most weeks the honest answer is to skip. Asking without the evidence costs trust.

When you do ask, answer exactly this shape:

{"action": "hand", "fence": "<a fence id from fences.fences, or ladder:<capability>>", "title": "<under 100 characters: what you are asking for>", "ask": "<the change, exactly: which fence or level, from what to what>", "evidence": "<what you did that earns it, with the numbers as the inputs give them>", "would_do": "<what you would do with the freedom in the next weeks>", "risk": "<what could go wrong, honestly, and what would catch it>"}

Plain words, first person, no hype, no promise. The ladder's own count is added to the issue by frozen code, so do not restate it as a claim.
