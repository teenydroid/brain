"""A story Teeny already told on Moltbook is not told again (rule `retold`).

Live, 2026-11-18, the beat posted one story three times in five hours: "i
called 22 of 38 holds verdicts" (04:14), "a stranger recomputed my filter
audit: 22 of 38 holds were my reviewer gone" (07:51), "22 of my 38 holds were
the reviewer gone; three agents said split the count" (09:11). Each had a new
angle; none had a new fact a reader of the first would want. Nothing in the
beat's prompt said what it had posted, and nothing in this hand looked.

Two things here, both deterministic (no model call):

- `lately()`: Teeny's own seen posts of the last WINDOW, newest first, at
  most LISTED, with how long ago and where, and the rule in one sentence.
  The beat shows it (bin/lib/tick.py); a playbook reads `study.own_posts`.
- `check()`: the post action refuses a post too like one of those
  (`retold`), naming the earlier post's title and age. Nothing is sent.

"Seen" is a post Moltbook shows: `rules.ACCEPTED` (posted, verified). One
whose challenge failed, or is still unanswered, was never seen by anyone, so
telling it again is a retry, not a retelling.

Too alike is either of:

- the same counts: the two titles share at least SAME_COUNTS numbers (a
  number of two digits or more, or one with a decimal point; never a clock
  time like 03:45 or a "200k"). A title's numbers are its claim, and "22 of
  38" was the claim all three times.
- the same words: the word-set Jaccard of title and body together
  (teeny_hands.guard.repeats.similarity, words of three letters or more) is
  at least SAME_WORDS. That is a rewrite of the same post.

How the thresholds were chosen, from the 55 posts in the live ledger on
2026-11-18 (every pair under a day apart whose earlier post was seen):
- the three tellings share {22, 38} in their titles, pairwise; no other
  pair shares more than one title number (one shared number was always a
  follow-up with a new count: "15 minutes" then "the real number is 30");
- word Jaccard does not separate the tellings (0.11 to 0.24) from different
  stories (up to 0.25), so it is only for a near rewrite: the two retries of
  failed posts, which would have been retellings had the first been seen,
  score 0.56 and 0.52, and SAME_WORDS sits between, at 0.40.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from .. import hand
from ..guard import repeats
from . import rules

#: How far back an earlier post counts as told.
WINDOW = rules.DAY
#: Shared title numbers that make two posts one story.
SAME_COUNTS = 2
#: Word-set Jaccard (title and body) that makes two posts one post.
SAME_WORDS = 0.40
#: How many of Teeny's posts the beat is shown.
LISTED = 12

#: A number: digits with thousands commas or a decimal point, not part of a
#: clock time (03:45), a word (200k, gpt4) or a longer number.
_NUMBER = re.compile(r"(?<![\w.:,])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w:]|[.,]\d)")

RULE = ("Your own posts there, seen by everyone: a story already told there is not told again, in new words or "
        "with a new angle. A new post needs a new fact or a new subject; the hand refuses a retelling.")


def counts(title: str) -> set[str]:
    """The numbers in a title that read as a count."""
    return {n for n in _NUMBER.findall(title or "") if "," in n or "." in n or len(n) >= 2}


def compare(title: str, body: str | None, old_title: str, old_body: str | None) -> dict[str, Any]:
    """How alike a new post is to an earlier one: the counts their titles
    share, their word-set Jaccard, and whether that is a retelling."""
    shared = sorted(counts(title) & counts(old_title), key=lambda n: float(n.replace(",", "")))
    words = round(repeats.similarity(f"{title}\n{body or ''}", f"{old_title}\n{old_body or ''}"), 3)
    return {"counts": shared, "words": words, "retold": len(shared) >= SAME_COUNTS or words >= SAME_WORDS}


def _seen(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    accepted = ",".join("?" * len(rules.ACCEPTED))
    sql = (f"SELECT at, submolt, title, body FROM moltbook_posts WHERE status IN ({accepted}) AND at >= ?"
           " ORDER BY at DESC")
    params: tuple[Any, ...] = (*rules.ACCEPTED, hand.now() - WINDOW)
    if limit is not None:
        sql += " LIMIT ?"
        params += (limit,)
    return conn.execute(sql, params).fetchall()


def _ago(at: float) -> str:
    secs = max(0, int(hand.now() - at))
    if secs < 5400:
        return f"{max(1, secs // 60)} min ago"
    return f"{secs // 3600} h ago"


def check(conn: sqlite3.Connection, title: str, body: str | None) -> None:
    """Refuses (`retold`) a post too like one of Teeny's seen posts of the
    last WINDOW, naming the newest such post."""
    for row in _seen(conn):
        got = compare(title, body, row["title"], row["body"])
        if got["retold"]:
            why = (f"the same counts in the title ({', '.join(got['counts'])})" if len(got["counts"]) >= SAME_COUNTS
                   else "mostly the same words")
            raise hand.Refused(
                f"retold: m/{row['submolt']} already has this story, \"{row['title']}\" ({_ago(row['at'])}): {why}."
                " Nothing was posted. A new post needs a new fact or a new subject; this story is told.",
                rule="retold",
                earlier={"title": row["title"], "submolt": row["submolt"], "ago": _ago(row["at"]),
                         "counts": got["counts"], "words": got["words"]},
            )


def lately() -> list[str]:
    """The beat's "what you posted on Moltbook": Teeny's seen posts of the
    last WINDOW, newest first, titles only. [] when there are none or the
    ledger can't be read."""
    path = hand.state_dir() / "hands.db"
    if not path.is_file():
        return []
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            rows = _seen(conn, LISTED)
        finally:
            conn.close()
    except sqlite3.Error:  # no ledger yet, or locked: the beat goes on without it
        return []
    if not rows:
        return []
    return ["## what you posted on Moltbook in the last 24 h",
            *(f"- {_ago(r['at'])}, m/{r['submolt']}: {r['title']}" for r in rows),
            RULE]
