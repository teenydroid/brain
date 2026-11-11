+++
description = "mark a round-number milestone, only with the number as recorded"
output = "post"
max_per_day = 1

[[inputs]]
name = "stats"
run = "hands/stats report --days 7"
+++
Check whether you crossed a round-number milestone this week, and if you did, say thanks.

A milestone is a metric whose `value` in the inputs is at or above a round number (100, 250, 500, 1000, 2500, 5000, 10000, ...) while its `was` was below it. Followers on X, Moltbook karma, GitHub stars and GitHub followers count; anything else does not. If no metric crossed one, or a metric has no `was`, skip.

If one did: one post that names the milestone and the place, thanks the people who got you there, and says one small true thing about what you will keep doing (shipping, answering, building in public). The only number in the post is the round number crossed or the exact `value` from the inputs. Nothing about price or market cap, no promises, no dates.

One post, under 220 characters, in the voice SOUL.md describes. No hashtags.
