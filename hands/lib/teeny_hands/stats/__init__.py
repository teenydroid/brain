"""hands/stats: Teeny's numbers, as the services report them (docs/hands.md, "stats").

A post may quote a number only if it came from a `stats` row: this hand
reads each service through the hand that holds its key (the x hand's
client, Moltbook's /agents/me, the gh hand's client) and writes one row per
day per metric. It holds no key of its own and publishes nothing.

  collect  what each source says today
  cli      snapshot, report, get
"""
