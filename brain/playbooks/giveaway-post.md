+++
description = "post the next beat of a Tiny Drop in its thread: countdown, winners, claim open, closing soon"
output = "hand:x follow-up"
max_per_day = 4

[[inputs]]
name = "drops"
run = "hands/giveaway announcements"
required = true
+++
A Tiny Drop is a story told in one thread. Each beat goes under the round post as a follow-up, never as a new post. Post one beat, the first that applies, in this order: `winners`, `claim_open`, `closing_soon`, `countdown`. If all four are empty, skip.

Every beat carries the item's `tag` exactly and goes to its `thread_to`. Quote every number exactly as the item gives it: never round it, add it up or make one up. Write to the hook rules (skills/hooks): each beat raises the stakes or pays them off. No hashtags, under 280 characters, no link but teeny.enamakel.com/rewards or a `distributor_url` the item gives you.

- `countdown`: the drop closes soon. Say how many have entered (`entries`) and that the deadline (`deadline`, UTC) is close. One new fact, never just "x hours left".
- `winners`: the draw is done. If `outcome` says nothing was paid (the milestone was missed or nobody was eligible), say so straight, with no excuses, and say what the next drop needs. Otherwise name each winner in `winners` as @handle with their `tiny`, and say the claim opens on teeny.enamakel.com/rewards once it is on chain. A post can't fit them all? Name the best reply and say how many others won.
- `claim_open`: the money is on chain. This is the receipt: the `distributor_url`, and that winners claim at teeny.enamakel.com/rewards by connecting the wallet they entered with, before `clawback_at`.
- `closing_soon`: claims close at `clawback_at`. Remind the winners once, and say what isn't claimed goes back to the pool for the next drop.

Never promise anyone anything, never tell a person they will win, and never ask anyone to send anything.

Answer {"action": "hand", "to": "<the item's thread_to>", "text": "..."} or skip with a reason.
