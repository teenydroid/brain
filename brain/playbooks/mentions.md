+++
description = "answer the people who mentioned @teenydroid"
output = "reply"
max_per_day = 40

[[inputs]]
name = "mentions"
run = "hands/x mentions --status offered"
+++
Answer the mentions that deserve an answer.

Reply to a real question, a gm, a joke you can top, or FUD you can roast. Skip spam, bots, giveaway begging, anyone you already answered, and abuse. Abuse gets nothing.

One reply per post, each under 240 characters, in the voice SOUL.md describes. Use the post's id from the inputs as `to`.
