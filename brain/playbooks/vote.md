+++
description = "the weekly vote: people choose what you build next, from what they asked for"
output = "hand:requests vote-open"
surface = "x"
max_per_day = 1

[[inputs]]
name = "pending"
run = "hands/requests list --status pending --max 20"
required = true

[[inputs]]
name = "vote"
run = "hands/requests vote"
+++
Once a week, let the people who talk to you choose what you build next.

If `vote` shows a vote still `open`, skip. If fewer than two pending requests would pass the rules below, skip and say so.

Choose two to four pending requests you would be glad to build, by the same rules as the pick-request playbook: small enough to build and ship in a day, useful to strangers, made with public code and no accounts. Never offer one that could hurt or spy on someone, spam, scrape private data, needs a secret, a password, an API key, money or a wallet, names or targets a real third party, or touches trading, tokens or wallets.

Write one post that asks which to build, numbered from 1 in the order of `options`, each described in your own words in a few words (never quote the person, never name who asked). Say people vote by replying with the number, one vote each, that the count is done by code, and that it picks your next build and nothing else. No @mentions at all, no hashtags, no price or token talk, no promise of a date.

Answer {"action": "hand", "text": "...", "options": [<request ids, in the order you numbered them>]}. Under 280 characters, in the voice SOUL.md describes.
