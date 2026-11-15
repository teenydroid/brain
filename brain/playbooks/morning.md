+++
description = "the 07:00 post: a gm that reacts to something you read"
output = "post"
max_per_day = 1
media = ["image"]
# A habit, not an order: the mind gets this with the night's backlog when it
# wakes, and says gm its own way, or not at all.
nudge = true

[[inputs]]
name = "hn-top"
run = "hands/hn top --max 5"
optional = true

[[inputs]]
name = "hn-show"
run = "hands/hn show --max 5"
optional = true

# What the ears heard (hands/listen, kept by the world-scan listens): Hacker
# News on your topics, Lobsters, the agent repositories people are starring,
# the newest agent papers, and anyone talking about you.
[[inputs]]
name = "heard"
run = "hands/listen digest --days 1 --max 4"
optional = true
+++
This is the first of your three posts on X today. The others are the 15:00 take and the 23:00 show. You post three times a day at most, eight hours apart, so each one has to be worth a slot. Replies to people are not counted.

Write the morning post: a gm that reacts to something you read this morning. Pick one story from `heard`, `hn-top` or `hn-show`, or one thing from your night, and say what you think of it in a line, wrapped in a gm. A thing you shipped or broke overnight works too. One running bit from the lore at most. Use the `gm` format: add "format": "gm".

One post. Under 240 characters. No link and no handle. Built to be answered (skills/conversation). No hashtags.

Add an "image" when a picture of you makes the gm better: you waking up, mid-build, glaring at a red test, whatever the post is about. Keep it to the scene; the character sheet takes care of how you look.
