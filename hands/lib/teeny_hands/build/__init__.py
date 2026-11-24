"""hands/build: what is left of the builder (docs/hands.md, "build").

The builder agent and its desk are retired (2026-11-24): the agentic mind
ships things itself, through its broker (docs/mind.md). What stays:

  ledger     `build_jobs` and `build_ideas`, the builder's history, read only
             (`hands/build jobs`); never dropped or altered
  selfclone  Teeny's own clone of GITHUB_USER/brain (state/self/brain.git):
             synced from origin/main (`hands/build self-sync`, and bin/evolve
             at the start of every run), the base of every evolve generation
             and of the babysitter's reverts
  mindclone  the agentic mind's own clone (state/home/repo/, its git directory
             outside the jail): a hostile tree made into a pull request
             through bin/broker (docs/mind.md)
  verify     a test run in a Landlock jail (bin/lib/datajob.py runs its jobs
             with it)
  cli        the verbs
"""
