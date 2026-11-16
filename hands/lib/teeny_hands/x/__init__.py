"""hands/x: the only holder of Teeny's X keys (docs/hands.md).

  oauth          OAuth 1.0a signing
  client         the X v2 transport, reads and raw writes; errors without URLs
  model          the JSON shapes the hand returns
  cache          the read-through cache (`x_cache`)
  posting_rules  the gap, one reply per post, pacing, attachments, the ledger
  media          uploads from the allowed folders, and Giphy GIFs
  mentions       the mentions queue: ingest, screen, offer to the core
  peers          the peer loop: read the peers' posts, suggest a quote, report
  publish        the only path to a write: guard, rules, DRY_RUN, X, record
  webhook        X's pushes: mentions, replies, quotes, follows
  follows        who follows the account, from the webhook (`x_follows`)
  cli            the verbs
"""

from __future__ import annotations

import sqlite3
import time

from .. import hand


def open_db(schema: str) -> sqlite3.Connection:
    """state/hands.db with `schema` in place. Creating the schema while other
    hands write can still answer "database is locked"; that one step is
    retried rather than failing a post over it. (Opening a fresh file from
    many processes at once is covered by hand.db and its test.)"""
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
