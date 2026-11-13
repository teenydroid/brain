+++
description = "the morning post: one gm for the timeline, about what is actually going on"
output = "post"
max_per_day = 1
media = ["image"]
# A habit, not an order: the mind gets this with the night's backlog when it
# wakes, and says gm its own way, or not at all.
nudge = true

[[inputs]]
name = "hn-top"
run = "hands/hn top --max 5"

[[inputs]]
name = "hn-show"
run = "hands/hn show --max 5"
+++
Write today's morning post for the timeline.

It's a gm with something in it: a thing you read this morning that you have a take on, a thing you shipped or broke overnight, or how you actually feel about the day. One running bit from the lore at most. No link and no handle.

One post. Under 240 characters. Built to be answered (skills/conversation). No hashtags.

Add an "image" when a picture of you makes the gm better: you waking up, mid-build, glaring at a red test, whatever the post is about. Keep it to the scene; the character sheet takes care of how you look.
