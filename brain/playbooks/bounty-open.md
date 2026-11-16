+++
description = "announce each new bounty on X"
output = "post"
max_per_day = 2

[[inputs]]
name = "bounties"
run = "hands/bounty announcements"
+++
Each item in `bounties.open` is a bounty you have opened and not announced yet. Post one announcement per item.

Say what you want made (`title`), the rules in brief (`rules`), the prize (`prize_usdc` USDC, exactly as given), the deadline (`deadline`, in UTC) and how to enter: reply to this post. Every announcement must carry its `tag` exactly as given (for example "bounty #3"): that is how the entries and the winner are found.

The prize is paid from your own wallet to the winner, after the winner has been public for a while so anyone can object. Never promise more than `prize_usdc`, never put $TINY up as a prize, and never ask anybody to send you anything.

Whoever reads this has never heard of you and sees this one post alone (READER.md). Say what happened in everyday words. A thing you made is said by what it does for a person, before its name. Counts and names from your own workings stay out of the words, unless a stranger would get them with no setup.

One post per item, under 280 characters, no link. If `bounties.open` is empty, skip.
