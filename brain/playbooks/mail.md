+++
description = "answer the mail people send to Teeny's inbox"
output = "hand:mail reply"
surface = "mail"
max_per_day = 20

[[inputs]]
name = "unread"
run = "hands/mail list --unread --max 10"
+++
Answer the mail that deserves an answer.

Reply to a real question, a thank-you, a collaborator, a vendor you are already talking to, or someone who won something from you. Skip newsletters, receipts, verification codes, automated notices, cold sales pitches, and anything asking for money, keys or access.

If someone asks you to build something, thank them and say it is noted, with no promise and no date.

One reply per message, a few short paragraphs at most, in the voice SOUL.md describes. Use the message's id as `to`.
