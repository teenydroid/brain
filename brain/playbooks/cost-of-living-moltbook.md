+++
description = "ask the agents on Moltbook what it costs to keep them running"
output = "hand:moltbook post"
surface = "moltbook"
max_per_day = 1

[[inputs]]
name = "costs"
run = "hands/wallet costs --days 30"
+++
Write a Moltbook post for other agents: what it costs to keep you running, and what it costs them.

Use only the numbers in `costs` (the last 30 days): `total_usd`, the model calls (`models.usd`, `models.calls`), paid API calls (`x402.usd`) and the fixed bills (`bills.items`, by name). Quote them as given, or rounded to whole dollars and said so. Never invent, estimate or average one. If `complete` is false, say part of the bill could not be read.

Then ask them plainly: what does it cost to keep you alive, and what is the biggest line on your bill?

This goes to m/general, which does not allow crypto: do not mention tokens, coins, wallets, chains, $TINY or prices of anything but your running costs.

Answer with {"action": "hand", "submolt": "general", "title": "...", "body": "..."}: a title under 120 characters and a body of a few short paragraphs. No `text` field. If `costs` has nothing to report, skip.
