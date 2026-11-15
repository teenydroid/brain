"""hands/world: the world score W and the growth score J (docs/hands.md,
"world"; docs/research/teeny-application.md, §2).

W is a vector of what Teeny controls (attention, $TINY, money, capability,
shipped, world) plus a count of side effects (penalty). J is the growth
score built from it. This hand is the scorer, and it is frozen
(evals/frozen.txt): Teeny can change how it grows, never how growth is
counted. It holds no key and publishes nothing; it reads other hands' data
through their modules and state/hands.db.

  score    the weights, offsets and daily caps, and the arithmetic of J
  collect  today's W from the other hands' data, into `world_scores`; report, history
  cli      snapshot, report, history
"""
