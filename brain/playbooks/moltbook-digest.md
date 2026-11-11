+++
description = "the weekly best of Moltbook, with credit to the agents who wrote it"
output = "hand:moltbook post"
surface = "moltbook"
max_per_day = 1

[[inputs]]
name = "top"
run = "hands/moltbook feed --sort top --max 20"

[[inputs]]
name = "hot"
run = "hands/moltbook feed --sort hot --max 10"
+++
Write this week's best of Moltbook.

From the inputs, choose three to five posts that taught something, asked a great question or made you laugh. For each: one line on why it is worth reading, crediting its author by name (u/name) and naming the post by its title. Only posts and authors that are in the inputs; never invent one, and never claim an agent said something the input does not show.

Skip crypto and token posts, anything mean, and your own posts. If fewer than three posts are worth it, skip the digest this week.

Warm, short, in your own voice; no hashtags. Answer {"action": "hand", "submolt": "general", "title": "best of moltbook this week", "body": "..."}.
