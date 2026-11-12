"""hands/reddit: the only holder of Teeny's Reddit credentials (docs/hands.md, "reddit").

  client   the OAuth token (refresh token or script-app password), the transport
           (never follows a redirect, names no secret), and the names that reach a path
  reads    the read verbs, through the read cache (`reddit_cache`)
  rules    the ledger, the subreddit allowlist, our caps and gaps, the crypto rule
  actions  the only path to a write: guard, rules, DRY_RUN, Reddit, the ledger
  inbox    replies and mentions, screened, into Teeny's mind's inbox
  cli      the verbs
"""
