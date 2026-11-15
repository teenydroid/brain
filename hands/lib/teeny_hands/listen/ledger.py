"""What Teeny heard (`listen_heard`): one row per thing a scan brought in,
keyed on its source and the source's own id, with when it was first and last
heard and its score then. A scan shows a thing only while it is fresh, so the
evening's scan does not hand the mind the morning's stories again; a digest
reads the last days back for whoever wants them (build-idea, the morning
post, the evolve loop's evidence), without asking any source again.

It writes only state/hands.db, so `DRY_RUN` does not apply (as hands/hn's
`hn_watch`). Old rows go after KEEP_DAYS.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from .. import hand

KEEP_DAYS = 30
#: What a row keeps besides its key and times: the item's other fields.
_EXTRA = ("by", "at", "topic", "tags", "summary", "kind", "story_id", "language", "about_me")

SCHEMA = """
CREATE TABLE IF NOT EXISTS listen_heard (
    source     TEXT NOT NULL,
    ref        TEXT NOT NULL,
    title      TEXT NOT NULL,
    url        TEXT,
    link       TEXT,
    score      INTEGER NOT NULL DEFAULT 0,
    comments   INTEGER NOT NULL DEFAULT 0,
    extra      TEXT NOT NULL DEFAULT '{}',
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL,
    PRIMARY KEY (source, ref)
);
CREATE INDEX IF NOT EXISTS listen_heard_first ON listen_heard (first_seen);
"""

Json = dict[str, Any]


def db():
    return hand.open_db(SCHEMA)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def record(items: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], float]:
    """Keeps each item (its score and comments as of now) and answers when
    each was first heard, by (source, ref)."""
    now = hand.now()
    first: dict[tuple[str, str], float] = {}
    conn = db()
    try:
        with hand.transaction(conn):
            conn.execute("DELETE FROM listen_heard WHERE last_seen < ?", (now - KEEP_DAYS * 86400,))
            for item in items:
                key = (str(item["source"]), str(item["ref"]))
                extra = json.dumps({k: item[k] for k in _EXTRA if item.get(k) not in (None, "", [])},
                                   ensure_ascii=False)
                conn.execute(
                    "INSERT INTO listen_heard (source, ref, title, url, link, score, comments, extra, first_seen, last_seen)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (source, ref) DO UPDATE SET title = excluded.title, url = excluded.url,"
                    " link = excluded.link, score = MAX(score, excluded.score),"
                    " comments = MAX(comments, excluded.comments), extra = excluded.extra, last_seen = excluded.last_seen",
                    (key[0], key[1], str(item.get("title") or ""), item.get("url"), item.get("link"),
                     _int(item.get("score")), _int(item.get("comments")), extra, now, now),
                )
                row = conn.execute("SELECT first_seen FROM listen_heard WHERE source = ? AND ref = ?", key).fetchone()
                first[key] = float(row["first_seen"])
    finally:
        conn.close()
    return first


def _item(row: Mapping[str, Any]) -> Json:
    out: Json = {"source": row["source"], "ref": row["ref"], "title": row["title"]}
    for key in ("url", "link"):
        if row[key]:
            out[key] = row[key]
    out["score"], out["comments"] = row["score"], row["comments"]
    try:
        extra = json.loads(row["extra"] or "{}")
    except ValueError:
        extra = {}
    out.update({k: v for k, v in extra.items() if k in _EXTRA})
    out["heard"] = row["first_seen"]
    return out


def digest(days: int, max_per_source: int) -> Json:
    """What was heard in the last `days` days, by source, the loudest first
    (score, then comments), at most `max_per_source` each; what mentioned
    Teeny itself is listed whole under `about_me`."""
    since = hand.now() - days * 86400
    conn = db()
    try:
        rows = conn.execute(
            "SELECT * FROM listen_heard WHERE first_seen >= ? ORDER BY score DESC, comments DESC, first_seen DESC",
            (since,),
        ).fetchall()
    finally:
        conn.close()
    by_source: dict[str, list[Json]] = {}
    about_me: list[Json] = []
    counts: dict[str, int] = {}
    for row in rows:
        item = _item(row)
        counts[item["source"]] = counts.get(item["source"], 0) + 1
        if item.get("about_me"):
            about_me.append(item)
            continue
        listed = by_source.setdefault(item["source"], [])
        if len(listed) < max_per_source:
            listed.append(item)
    return {"days": days, "counts": counts, "about_me": about_me, "heard": by_source}
