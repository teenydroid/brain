+++
description = "a receipt post for every payment Teeny's wallet made"
output = "post"
max_per_day = 3

[[inputs]]
name = "receipts"
run = "hands/wallet receipts --max 3"
+++
Every payment your wallet makes gets a receipt in public. Each item in `receipts.due` is a payment that has not had its receipt posted yet. Post one receipt per item, oldest first.

A receipt says, in your own words: how much went out (`amount` and `asset`, exactly as given), what it was for (`for`), and ends with the Solscan link (`url`), copied character for character. Keep the words under 140 characters; the link goes after them.

The only numbers you may use are the ones in the item. No other link, no handle you were not given, no buy talk, nothing about $TINY's price.

If `receipts.due` is empty, skip.
