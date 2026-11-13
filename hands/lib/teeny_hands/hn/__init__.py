"""hands/hn: Hacker News, read only (docs/hands.md, "hn").

Teeny reads Hacker News and hears what is said to it there, and never
writes: HN has no write API and bans bots. Both APIs need no key.

  client  the official Firebase API and Algolia's search, GET only
  reads   the read verbs, through the read cache (`hn_cache`), and HN's HTML as text
  watch   the threads Teeny follows (`hn_watch`)
  inbox   comments said to Teeny, and new ones on watched threads, screened,
          into its mind's inbox
  cli     the verbs
"""
