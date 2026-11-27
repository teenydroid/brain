"""hands/trend: what AI X is talking about today, read only (docs/hands.md,
"trend").

Digg's AI and tech leaderboards rank the stories people on X are posting
about, hours old, with the X posts behind each one. They are read from
Digg's public endpoints (its ranked feed and its MCP server) with urllib,
which need no login, no key and no X API. Nothing is written but the cache,
under state/trend/.

  digg  one read of Digg: its JSON, or why there is none
  top   the merged leaderboards and the leading stories' posts, cached a slot
  cli   the verbs
"""
