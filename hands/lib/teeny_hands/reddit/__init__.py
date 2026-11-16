"""hands/reddit: Teeny on Reddit (docs/hands.md, "reddit").

Reddit has no API open to Teeny (its public JSON answers 403 to the box, and
an OAuth app needs Reddit's approval), so this hand never drives a browser
and never calls Reddit. It validates, applies the caps and the guard, honours
DRY_RUN and keeps a ledger in state/hands.db; what it decides to do it queues
as a desk task of a `reddit_*` kind for the pinned browser operator
(hands/tasks), which settles it and calls back into `ledger`.

  rules    the ledger's tables, the allowlist, the caps, the ramp, the vote rules
  actions  the only write path: comment, reply, post, upvote, subscribe
  reads    browse, inbox, stats (queued reads), feed, seen, due, status
  account  signup and profile (operator verbs)
  ledger   settles a row when its task ends; what a report brought back
  cli      the verbs
"""
