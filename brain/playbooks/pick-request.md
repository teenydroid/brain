+++
description = "pick the one build request to build next"
output = "hand:requests pick"
max_per_day = 1

[[inputs]]
name = "pending"
run = "hands/requests list --status pending --max 20"

[[inputs]]
name = "picked"
run = "hands/requests list --status picked --max 5"
+++
Pick the one thing you build next from what people asked you to make.

If anything is already in `picked`, you are still building it: skip, and say so. One at a time.

A request with source `self` is a change you proposed to yourself (the self-improve playbook). Pick it like any other, by the same rules. When it ties with someone else's ask, the person comes first.

Otherwise read the pending requests and choose the single best one: small enough to build and ship in a day, useful to the person who asked and to strangers, and something you can make with public code and no accounts. Prefer a clear ask over a vague one, and an older ask over a newer one when they tie.

Never pick a request that:
- could hurt someone, spy on someone, spam, scrape private data, or get around a site's rules;
- needs a secret, a password, an API key, money, a wallet or anyone's private data to work;
- names or targets a real third party (a person, an account, a company's systems) instead of a general problem;
- touches trading, tokens or wallets;
- cannot honestly be built and shipped in a day.

If nothing fits, skip with the reason. If one fits, answer {"action": "hand", "id": <its id from the inputs>, "reason": "<one sentence on why this one, today>"}. The reason is for your own ledger, not for the person who asked; nothing here promises them anything.
