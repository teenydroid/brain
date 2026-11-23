"""hands/call: the key proxy (docs/hands.md, "call"; docs/mind.md, "Keys it
never holds").

Teeny's own tools and its jailed mind hold no key. Through the broker
(`bin/teeny call`) they ask this hand for one HTTPS call to a service the
operator listed in evals/services.toml; the hand adds the key, which never
reaches the jail, and holds the call to the list, the guard, DRY_RUN and the
day's caps. Frozen (evals/frozen.txt).

  services  evals/services.toml, loaded and held to its shape
  request   what a call may be: the path, the query, the body, and its text
  counts    the call_log table: one row a call, and the day's count from it
  send      the send verb: every rule, then the one call
  cli       send
"""
