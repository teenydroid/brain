"""hands/gh: the only holder of Teeny's GitHub token (docs/hands.md, "gh").

  client       the GitHub REST transport; errors name no URL and no token
  cache        the read-through cache (`gh_cache`)
  contributing whether a repository's policy forbids AI-made contributions
  secretscan   what a push would publish that looks like a secret
  gitpush      the one `git push`, with the token handed over for that command only
  actions      the only path to a write: guard, rules, DRY_RUN, GitHub, the ledger
  cli          the verbs
"""

from __future__ import annotations

import sqlite3
import time

from .. import hand


def open_db(schema: str) -> sqlite3.Connection:
    """state/hands.db with `schema` in place, retrying a "database is locked"
    from another hand creating its own tables at the same moment."""
    for attempt in range(20):
        try:
            conn = hand.db()
            conn.executescript(schema)
            return conn
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or attempt == 19:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")
