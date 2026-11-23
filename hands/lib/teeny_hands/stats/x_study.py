"""What worked and what flopped on X, for the dream (bin/lib/scoreboard.py,
"x: what worked and what flopped"): the Moltbook study's counterpart.

Teeny's own X writes over 7 and 14 days, each with its latest measured row
(`board.x_writes`), grouped by kind and producer and, for originals, by
format: how many were measured, the average impressions and the average
engagements (likes + replies + reposts + quotes). Then the best and worst
originals of the 14 days with their words clipped, and who the replies
reached: how many accounts, and the regulars (answered on two days or more).

Counted in code from the ledger; nothing here is a model's number. Until
2026-11-23 the dream saw Moltbook's results by format but nothing of X's,
where most of Teeny's writing now goes.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from .. import hand
from . import board

DAY = 86400.0
WINDOWS = (7, 14)
#: The best and worst originals shown.
TOP = 3
#: Originals: what a stranger sees in a feed (a thread's later posts are not).
ORIGINALS = ("post", "quote")
TEXT_CHARS = 90
REGULARS_SHOWN = 8

Json = dict[str, Any]


def _clip(text: Any, most: int = TEXT_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= most else text[: most - 3].rstrip() + "..."


def _avg(values: list[float]) -> int | float:
    value = round(sum(values) / len(values), 1) if values else 0
    return int(value) if float(value).is_integer() else value


def _writes(conn: sqlite3.Connection, since: float) -> list[Json]:
    """board.x_writes with each write's producer and, for a reply, the
    account it answered (from x_mentions), when the ledger has them."""
    writes = board.x_writes(conn, since)
    if not writes:
        return []
    cols = {r[1] for r in conn.execute("PRAGMA table_info(x_posts)")}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    producer = "p.producer" if "producer" in cols else "NULL"
    who = "m.author_username" if "x_mentions" in tables else "NULL"
    join = " LEFT JOIN x_mentions m ON m.id = p.in_reply_to" if "x_mentions" in tables else ""
    extra = {r[0]: (r[1], r[2]) for r in conn.execute(
        f"SELECT p.id, {producer}, {who} FROM x_posts p{join} WHERE p.created_at >= ?", (since,))}
    for w in writes:
        w["producer"], w["to"] = extra.get(w["id"], (None, None))
    return writes


def _group_line(label: str, writes: list[Json]) -> str:
    views = [float(w["views"]) for w in writes if "views" in w]
    return (f"{label} n={len(writes)} avg views {_avg(views) if views else '-'}"
            f" eng {_avg([float(w['result']) for w in writes])}")


def _groups(writes: Iterable[Json], key) -> list[tuple[str, list[Json]]]:
    out: dict[str, list[Json]] = {}
    for w in writes:
        out.setdefault(key(w), []).append(w)
    return sorted(out.items(), key=lambda kv: -_avg([float(w["result"]) for w in kv[1]]))


def _post_line(w: Json) -> str:
    when = datetime.fromtimestamp(float(w["at"]), timezone.utc).strftime("%m-%d")
    views = f", {w['views']} views" if "views" in w else ""
    return f"{w['result']} eng{views} ({when}, {w['producer'] or 'legacy'}, {w['format'] or '-'}): \"{_clip(w['text'])}\""


def summary(now: float | None = None) -> Json:
    now = hand.now() if now is None else now
    conn = hand.db()
    try:
        by_window = {days: _writes(conn, now - days * DAY) for days in WINDOWS}
    finally:
        conn.close()
    return {"windows": by_window}


def summary_lines(got: Json | None = None) -> list[str]:
    """The study as short lines for the dream, one per window and table,
    groups best first; then the best and worst originals and the reach of
    the replies. [] when nothing is measured."""
    got = summary() if got is None else got
    windows = got["windows"]
    lines: list[str] = []
    for days in WINDOWS:
        writes = windows.get(days) or []
        if not writes:
            continue
        kinds = _groups(writes, lambda w: f"{w['kind']}/{w['producer'] or 'legacy'}")
        lines.append(f"X by kind/producer, last {days}d: " + "; ".join(_group_line(k, ws) for k, ws in kinds))
        originals = [w for w in writes if w["kind"] in ORIGINALS]
        if originals:
            formats = _groups(originals, lambda w: w["format"] or "untagged")
            lines.append(f"X originals by format, last {days}d: " + "; ".join(_group_line(k, ws) for k, ws in formats))
    longest = windows.get(max(WINDOWS)) or []
    originals = sorted((w for w in longest if w["kind"] in ORIGINALS), key=lambda w: (-w["result"], -w.get("views", 0)))
    if originals:
        lines += [f"X best original, last {max(WINDOWS)}d: {_post_line(w)}" for w in originals[:TOP]]
        worst = [w for w in reversed(originals) if w not in originals[:TOP]][:TOP]
        lines += [f"X worst original, last {max(WINDOWS)}d: {_post_line(w)}" for w in worst]
    replies = [w for w in (windows.get(min(WINDOWS)) or []) if w["kind"] == "reply"]
    if replies:
        days_of: dict[str, set[str]] = {}
        for w in replies:
            if w.get("to"):
                day = datetime.fromtimestamp(float(w["at"]), timezone.utc).strftime("%Y-%m-%d")
                days_of.setdefault(str(w["to"]).lower(), set()).add(day)
        regulars = sorted((h for h, d in days_of.items() if len(d) >= 2), key=lambda h: (-len(days_of[h]), h))
        shown = ", ".join(f"@{h}" for h in regulars[:REGULARS_SHOWN])
        lines.append(f"X replies, last {min(WINDOWS)}d: {len(replies)} measured to {len(days_of)} accounts;"
                     f" {len(regulars)} regulars (answered on 2+ days)" + (f": {shown}" if shown else ""))
    return lines
