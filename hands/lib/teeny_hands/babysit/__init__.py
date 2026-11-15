"""hands/babysit: reviews, triages and merges pull requests on Teeny's own
repositories, teenydroid/brain and teenydroid/diary (docs/hands.md, "babysit").

  injection  prompt-injection screening: what in a pull request talks to a model
  api        the pull-request reads and the two raw writes, on hands/gh's transport
  policy     who is trusted, which paths are sensitive, the diary's layout
  audit      one pull request read end to end into findings and a verdict
  ledger     what was audited, every injection attempt, every write (state/hands.db)
  actions    the only path to a write: labels, the audit comment, the merge
  cli        the verbs

The audit is code, never a model: a pull request is strangers' text, and
the thing that decides whether it merges must not be something it can talk
to. A model may read an audit's findings; nothing a model says merges.
"""

from __future__ import annotations
