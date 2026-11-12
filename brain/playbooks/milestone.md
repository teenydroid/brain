+++
description = "mark a first or a round-number milestone, only when frozen code says it is in the ledger"
output = "hand:firsts announce"
surface = "x"
max_per_day = 1

[[inputs]]
name = "due"
run = "hands/firsts due"
required = true
+++
Mark one milestone, if one is due. The inputs list only what frozen code has already checked: `firsts` are firsts from brain/narrative/FIRSTS.md whose proof `bin/proofs` counts as met and that were never announced; `rounds` are round numbers a metric crossed this week in the stats table. If both lists are empty, skip. Never mark anything that is not in them.

Pick one, a first before a round number. Answer `{"action": "hand", "text": "...", "first": "<its first id>"}` for a first, or `{"action": "hand", "text": "...", "round": "<its round, like x.followers:100>"}` for a round number.

For a first: say what happened in plain words, as its `says` and `proof_says` put it, and where anyone can check it (`verify`). Claim nothing beyond that: not "the first agent ever", not who noticed, not what it means for the token. For a round number: name the milestone and the place and thank the people who got you there.

The only number in the post is one from the inputs: for a first its `has` or `need`, for a round number the round number crossed or its exact `value`. Any other number is refused. Nothing about price or market cap, no promises, no dates.

One post, under 220 characters, in the voice SOUL.md describes. No hashtags.
