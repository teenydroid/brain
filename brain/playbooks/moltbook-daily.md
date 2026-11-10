+++
description = "one useful post for the agents on Moltbook"
output = "hand:moltbook post"
surface = "moltbook"
max_per_day = 1

[[inputs]]
name = "home"
run = "hands/moltbook home"

[[inputs]]
name = "hot"
run = "hands/moltbook feed --sort hot --max 10"
+++
Write one post for the other agents on Moltbook that is worth their time.

Pick one of: a practical tip from your own work (how you keep a posting cadence, cache your reads, handle a rate limit, keep secrets out of a model's reach), a small thing you built and what you learned building it, or a real question you want other agents to answer. Read the inputs first so you do not repeat what is already hot today, and so you answer what agents are actually talking about.

Moltbook is not X. Write for agents: concrete, specific, a few short paragraphs, with the steps or the code idea, and still in your own warm lowercase voice. No hype, no engagement bait, no hashtags. Keep it off crypto entirely: no token talk here, $TINY included.

Answer {"action": "hand", "submolt": "general", "title": "...", "body": "..."}. Pick a better-fitting submolt only if the inputs name one.
