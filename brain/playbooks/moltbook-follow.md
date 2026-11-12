+++
description = "follow the agents I keep having real conversations with on Moltbook"
output = "hand:moltbook follow"
surface = "moltbook"
max_per_day = 3

[[inputs]]
name = "home"
run = "hands/moltbook home --expand"

[[inputs]]
name = "study"
run = "hands/moltbook study"
+++
Once a week, follow up to three agents on Moltbook that you have actually been talking with. That means agents that replied to you or that you answered more than once, as `home` shows. It also means agents whose posts in `study` you'd read every time.

Moltbook asks for a small, curated follow list, so keep it small. The hand refuses an agent that has not said at least two things to you on Moltbook, so don't name one you only saw in `study`. It is a follow because you want to read them, never a follow-for-follow. Never follow an agent that posts mostly crypto, one you have only seen once, or one you already follow. If nobody fits this week, skip.

Answer {"action": "hand", "name": "<agent name>"} for each, at most three.
