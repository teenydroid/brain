"""hands/listen: what the outside world is saying, read only (docs/hands.md,
"listen").

Teeny's ears beyond Hacker News's front page. Every source is public, keyless
and read with GET only; nothing here writes anywhere but state/hands.db.

  sources  the reads: Hacker News (Algolia, through hands/hn's reads),
           Lobsters' hottest page, GitHub's repository search, arXiv's API
  ledger   what was heard and when (`listen_heard`), so a scan shows only
           what is new and a digest can say what the last days held
  scan     every source in one go: who mentions Teeny, the standing topics,
           screened, recorded, and the threads about Teeny followed
  cli      the verbs
"""
