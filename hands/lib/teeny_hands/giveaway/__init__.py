"""hands/giveaway: Tiny Drops, public $TINY rounds paid by code from the
giveaway pool (docs/hands.md, "giveaway"; docs/drops-schema.md).

  caps         the switch and the GIVEAWAY_* caps
  store        the tables: rounds, entries, the draw
  eligibility  who may win, with a reason per entrant
  draw         the seed (a finalized Solana blockhash) and the draw order
  keccak       Keccak-256, which the standard library lacks
  merkle       the claim tree, hashed as the Rewards program checks it
  publish      the round's JSON in the public diary (teenydroid/diary)
  chain        the on-chain step, the interface PR 5b fills in
  rounds       the verbs
  cli          the command line
"""
