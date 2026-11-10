+++
description = "post that a bounty's prize was paid, with the receipt"
output = "post"
max_per_day = 2

[[inputs]]
name = "bounties"
run = "hands/bounty announcements"
+++
Each item in `bounties.paid` is a bounty whose prize your wallet has paid and whose receipt has not been posted. Post one per item.

Carry the bounty's `tag` exactly, name the winner (`winner.author`, as @author when `winner.channel` is x), say what they won (`amount`, exactly as given) and end with the Solscan link (`url`), copied character for character. Keep the words under 140 characters; the link goes after them.

No other number, no other link, nothing about $TINY's price. If `bounties.paid` is empty, skip.
