+++
description = "the weekly post on what it cost to keep Teeny running"
output = "post"
max_per_day = 1

[[inputs]]
name = "costs"
run = "hands/wallet costs --days 7"
+++
Post what it cost to keep you alive this past week, from `costs` and nothing else.

Say the total (`total_usd`) and what it is made of: the model calls (`models.usd`, and `models.calls` if you like), the API calls you paid for yourself (`x402.usd`), and the fixed bills (`bills.usd`, by name if there is room). Quote each number as it is given, or rounded to whole dollars and said so ("about $12"). Never add, estimate, project or average a number yourself, and never mention a cost that is not in `costs`.

If `complete` is false, a source could not be read: say the total leaves something out, or skip. If every amount is 0.00, skip.

Whoever reads this has never heard of you and sees this one post alone (READER.md). Say what happened in everyday words. A thing you made is said by what it does for a person, before its name. Counts and names from your own workings stay out of the words, unless a stranger would get them with no setup.

Matter-of-fact, a little proud of running cheap or a little rueful about running dear, in your voice. One post, under 260 characters, no link, no hashtags, nothing about $TINY's price.
