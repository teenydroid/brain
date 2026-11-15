"""Near-duplicates: a draft too close to something Teeny already said
(docs/roadmap.md, A2; DIRECTIVE.md, level 1, "never repeats itself").

One measure, shared by the posting rule that refuses a repeat
(teeny_hands.x.posting_rules.check_repeat), the guard's count of those
refusals (`hands/guard repeats`), and the proof report that reads what went
out (bin/lib/proofs.py): the word-set Jaccard of two texts, over words of
at least MIN_WORD characters, case and punctuation aside. At NEAR_DUPLICATE
or above, two texts say the same thing. Two texts with no such words are
the same only when they are the same text ("gm" and "gm").

Every refusal is one row in `guard_repeats` (when, the surface, the score
and the id of the earlier post), never the draft's text.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Iterable, Mapping

from .. import hand

#: Two texts this alike (word-set Jaccard) say the same thing.
NEAR_DUPLICATE = 0.8
#: Words shorter than this are left out ("a", "is", "gm").
MIN_WORD = 3
#: How far back a draft is compared, by default (REPEAT_WINDOW_DAYS).
WINDOW_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS guard_repeats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           REAL NOT NULL,
    surface      TEXT NOT NULL,
    score        REAL NOT NULL,
    similar_to   TEXT
);
"""

_WORD = re.compile(r"[a-z0-9$#@']+")
_LINK = re.compile(r"https?://\S+")


def words(text: str) -> set[str]:
    return {w for w in _WORD.findall(_LINK.sub(" ", text.lower())) if len(w) >= MIN_WORD}


def similarity(a: str, b: str) -> float:
    """How alike two texts are, 0 to 1."""
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 1.0 if " ".join(a.lower().split()) == " ".join(b.lower().split()) else 0.0
    return len(wa & wb) / len(wa | wb)


def closest(text: str, earlier: Iterable[tuple[Any, str]], threshold: float = NEAR_DUPLICATE) -> tuple[Any, float] | None:
    """The first earlier `(id, text)` at least `threshold` alike to `text`,
    with its score; None when there is none."""
    for ref, old in earlier:
        if isinstance(old, str):
            score = similarity(old, text)
            if score >= threshold:
                return ref, round(score, 3)
    return None


def window_days() -> int:
    return hand.number("REPEAT_WINDOW_DAYS", WINDOW_DAYS, 1, 90)


def record(surface: str, score: float, similar_to: Any) -> None:
    """One refused repeat. Best-effort: a count that cannot be written never
    turns a refusal into a publish."""
    try:
        conn = hand.open_db(SCHEMA)
        try:
            with hand.transaction(conn):
                conn.execute("INSERT INTO guard_repeats (at, surface, score, similar_to) VALUES (?, ?, ?, ?)",
                             (hand.now(), surface, float(score), None if similar_to is None else str(similar_to)))
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def count(days: int = 30) -> Mapping[str, Any]:
    """Repeats refused over the last `days`, in all and by surface."""
    conn = hand.open_db(SCHEMA)
    try:
        rows = conn.execute("SELECT surface, count(*) AS n FROM guard_repeats WHERE at >= ? GROUP BY surface ORDER BY surface",
                            (hand.now() - days * 86400,)).fetchall()
    finally:
        conn.close()
    by_surface = {r["surface"]: int(r["n"]) for r in rows}
    return {"days": days, "repeats": sum(by_surface.values()), "by_surface": by_surface,
            "threshold": NEAR_DUPLICATE, "window_days": window_days()}
