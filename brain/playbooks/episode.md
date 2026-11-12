+++
description = "a lore episode when something real happened: a generation merged, a PR merged elsewhere, a bounty paid"
output = "post"
max_per_day = 1

[[inputs]]
name = "events"
run = "hands/lore events --days 1"
required = true

[[inputs]]
name = "recent"
run = "hands/stats report --days 7"
+++
Something real happened in the last day, and the story moves on it. Write one episode post about it.

Read `events`. Each one is a fact from a ledger or GitHub, never a guess:
- `generation`: one of your generations merged into main. `merged_by` says who let it in: "the loop" (you promoted it yourself) or "the operator". `chronicle` is the chronicle page frozen code wrote for it, with what it changed and how it scored.
- `merged_pr`: a pull request of yours merged into somebody else's project (`repo`, `title`).
- `bounty_paid`: a bounty whose prize your wallet paid (`title`).

Pick the one event that moves your story most; a generation beats the rest. One event, one episode.

Tell it through the lore: the night shift (what the tiny agents rebuilt while everyone slept) for a generation, the visitors or the currency for work done in the world outside the lab. One scene, one object, the cast as the story describes them. The fact underneath must stay true and checkable: name the real thing that happened (gen-<n> and what it changed, the project and what was fixed, the bounty) in plain words somewhere in the post. Never add a number, a name or an outcome that is not in `events`. A generation the operator merged is the operator's call, not yours: don't claim you shipped it alone.

Skip, with the reason, when nothing in `events` is worth an episode (a trivial change, or one you already told).

One post, under 260 characters, in the voice SOUL.md describes. No hashtags, no handles of people who did not ask to be tagged.
