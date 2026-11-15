"""hands/goals: the goal ledger (docs/hands.md, "goals").

Teeny proposes its own goals (brain/playbooks/goals.md). Each one names
the W component it moves, a channel, a family, a deadline and a check from
a fixed set of kinds written here; the pinned goals-check job (bin/goals-check)
runs the checks, so a goal Teeny proposes cannot redefine how its success is
decided. Frozen (evals/frozen.txt).

  checks  the checker kinds: their parameters and how each is evaluated
  rank    near-duplicates, learning progress, the band, the score, ANNECS
  ledger  the `goals` table: propose, check, list
  cli     propose, check, list, rank, annecs
"""
