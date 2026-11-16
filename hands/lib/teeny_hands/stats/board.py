"""The scoreboard (docs/hands.md, "stats board"): one compact report of how
Teeny is doing, worked out in code so no model does arithmetic.

  audience  X followers, Moltbook followers and karma, Reddit karma: the latest `stats`
            value and its change over 1 and 7 days
  x         own posts, quotes and replies of the last N days with their
            latest `x_post_metrics` row
  moltbook  own posts and comments of the last N days with their latest
            `moltbook_post_metrics` row
  moltbook_daily  the newest snapshot's Moltbook numbers counted from the
            ledger (collect.py, source moltbook_ledger): live posts,
            unanswered own-thread comments, writes lost to the challenge
  playbooks per bin/act playbook over the last N days, from
            state/act/<name>/runs.jsonl: runs, own posts that went out, skips
            (the draft's skips, the anchor judge's "not anchored" among them,
            and runs skipped before the draft for an empty input or a shut
            slot; never a spent budget or a run already going), and how many
            of the skips were "not anchored"
  lines     the same in three short lines, for a prompt, a fourth for
            moltbook_daily once a snapshot has it, and a last one for the
            playbooks once any ran

For each surface: the five best and five worst writes, and the average
result per format (the lab tag on the write; an untagged write counts under
its kind, as `(reply)`). A write's result is one number:

  X         likes + replies + reposts + quotes (impressions shown beside it)
  Moltbook  score + comment count

Reads only. A table that does not exist yet reads as empty.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .. import hand

DAY = 86400.0
#: Writes listed as best and as worst, each.
TOP = 5
#: Characters of a write's text in the report.
TEXT_CHARS = 80
#: Characters of each of the three lines.
LINE_CHARS = 320
AUDIENCE = (("x", "followers"), ("moltbook", "followers"), ("moltbook", "karma"), ("reddit", "karma"))

Json = dict[str, Any]


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _column(conn: sqlite3.Connection, table: str, column: str, alias: str = "") -> str:
    """`table.column`, or NULL when a ledger older than the column lacks it."""
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    return f"{alias or table}.{column}" if column in have else "NULL"


def _num(value: float) -> int | float:
    value = round(float(value), 1)
    return int(value) if value.is_integer() else value


def audience(conn: sqlite3.Connection) -> list[Json]:
    """Each AUDIENCE metric's latest value and its change over 1 and 7 days
    (absent when no snapshot is that old)."""
    if "stats" not in _tables(conn):
        return []
    out = []
    for source, metric in AUDIENCE:
        latest = conn.execute(
            "SELECT value, date FROM stats WHERE source = ? AND metric = ? ORDER BY date DESC LIMIT 1", (source, metric)
        ).fetchone()
        if latest is None:
            continue
        item: Json = {"metric": f"{source}.{metric}", "value": _num(latest["value"]), "date": latest["date"]}
        day = datetime.strptime(latest["date"], "%Y-%m-%d")
        for label, back in (("d1", 1), ("d7", 7)):
            cutoff = (day - timedelta(days=back)).strftime("%Y-%m-%d")
            before = conn.execute(
                "SELECT value FROM stats WHERE source = ? AND metric = ? AND date <= ? ORDER BY date DESC LIMIT 1",
                (source, metric, cutoff),
            ).fetchone()
            if before is not None:
                item[label] = _num(latest["value"] - before["value"])
        out.append(item)
    return out


#: The moltbook_ledger metrics the board's fourth line reads, in its order,
#: each with how it reads.
DAILY = (
    ("live_posts_24h", "live posts (24h) {}"),
    ("own_thread_unanswered", "own-thread comments unanswered {}"),
    ("own_thread_answered_pct", "answered {}%"),
    ("own_thread_answer_minutes_median", "median answer {} min"),
    ("lost_pct_7d", "writes lost to the challenge (7d) {}%"),
    ("post_score_24h_median_7d", "median post score at 24h (7d) {}"),
    ("posts_10_plus_14d", "posts at 10+ (14d) {}"),
    ("posts_50_plus_14d", "at 50+ {}"),
)


def moltbook_daily(conn: sqlite3.Connection) -> Json:
    """The newest snapshot's moltbook_ledger metrics, with its date; {} before one."""
    if "stats" not in _tables(conn):
        return {}
    row = conn.execute("SELECT max(date) FROM stats WHERE source = 'moltbook_ledger'").fetchone()
    if not row or row[0] is None:
        return {}
    values = {r["metric"]: _num(r["value"]) for r in conn.execute(
        "SELECT metric, value FROM stats WHERE source = 'moltbook_ledger' AND date = ?", (row[0],))}
    return {"date": row[0], **values}


def _daily_line(daily: Json) -> str | None:
    said = [text.format(daily[name]) for name, text in DAILY if name in daily]
    return _line(f"Moltbook on {daily['date']}: " + ", ".join(said)) if said else None


#: Runs bin/act skipped that were never a decision about what to say.
NOT_A_SKIP = ("daily budget spent", "already running")


def _at(text: Any) -> float | None:
    try:
        return datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def playbooks(since: float) -> list[Json]:
    """Per playbook, its bin/act runs since `since` (state/act/<name>/runs.jsonl,
    one JSON line a run): runs, posts that went out, skips, and the skips the
    anchor judge made. Unreadable lines are passed over. Most posts first."""
    out = []
    for path in sorted((hand.state_dir() / "act").glob("*/runs.jsonl")):
        runs = posts = skips = not_anchored = 0
        for line in path.read_text(errors="replace").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            at = _at(record.get("at")) if isinstance(record, dict) else None
            if at is None or at < since:
                continue
            runs += 1
            skipped = record.get("skipped")
            if isinstance(skipped, str) and skipped not in NOT_A_SKIP:
                skips += 1
            for action in record.get("actions") or []:
                if not isinstance(action, dict):
                    continue
                if action.get("action") == "post" and action.get("status") == "done":
                    posts += 1
                elif action.get("action") == "skip":
                    skips += 1
                    if str(action.get("reason") or "").startswith("not anchored"):
                        not_anchored += 1
        if runs:
            out.append({"playbook": path.parent.name, "runs": runs, "posts": posts, "skips": skips,
                        "not_anchored": not_anchored})
    return sorted(out, key=lambda p: (-p["posts"], -p["skips"], p["playbook"]))


def _playbooks_line(days: int, rows: list[Json]) -> str:
    said = ", ".join(f"{r['playbook']} {r['runs']}/{r['posts']}/{r['skips']}"
                     + (f" ({r['not_anchored']} not anchored)" if r["not_anchored"] else "") for r in rows)
    return _line(f"Playbooks last {days}d (runs/posts/skips): {said}")


def x_writes(conn: sqlite3.Connection, since: float, until: float | None = None) -> list[Json]:
    """Own X posts, quotes and replies written in [since, until) with their
    latest measured row; unmeasured ones are left out."""
    tables = _tables(conn)
    if not {"x_posts", "x_post_metrics"} <= tables:
        return []
    rows = conn.execute(
        "SELECT p.id, p.kind, p.text, p.created_at,"
        f" {_column(conn, 'x_posts', 'format', 'p')} AS format, {_column(conn, 'x_posts', 'experiment', 'p')} AS experiment,"
        " m.checkpoint, m.likes, m.replies, m.reposts, m.quotes, m.impressions"
        " FROM x_posts p JOIN x_post_metrics m ON m.post_id = p.id"
        " JOIN (SELECT post_id, max(checkpoint) AS checkpoint FROM x_post_metrics WHERE likes IS NOT NULL"
        "       GROUP BY post_id) newest ON newest.post_id = m.post_id AND newest.checkpoint = m.checkpoint"
        " WHERE p.created_at >= ? AND p.created_at < ? ORDER BY p.created_at DESC",
        (since, until if until is not None else hand.now() + DAY),
    ).fetchall()
    out = []
    for r in rows:
        result = sum(int(r[k] or 0) for k in ("likes", "replies", "reposts", "quotes"))
        item: Json = {"id": r["id"], "kind": r["kind"], "format": r["format"], "experiment": r["experiment"],
                      "text": _cut(r["text"]), "result": result, "checkpoint": r["checkpoint"], "at": r["created_at"]}
        if r["impressions"] is not None:
            item["views"] = int(r["impressions"])
        out.append(item)
    return out


def moltbook_writes(conn: sqlite3.Connection, since: float, until: float | None = None) -> list[Json]:
    """Own Moltbook posts and comments written in [since, until) with their
    latest measured row; unmeasured ones are left out."""
    tables = _tables(conn)
    if "moltbook_post_metrics" not in tables:
        return []
    until = until if until is not None else hand.now() + DAY
    parts, params = [], []
    if "moltbook_posts" in tables:
        parts.append(
            "SELECT 'post' AS kind, p.post_id AS id, p.title AS text, p.at,"
            f" {_column(conn, 'moltbook_posts', 'format', 'p')} AS format,"
            f" {_column(conn, 'moltbook_posts', 'experiment', 'p')} AS experiment"
            " FROM moltbook_posts p WHERE p.post_id IS NOT NULL AND p.at >= ? AND p.at < ?"
        )
        params += [since, until]
    if "moltbook_comments" in tables:
        parts.append(
            "SELECT 'comment', c.comment_id, " + _column(conn, "moltbook_comments", "body", "c") + ", c.at,"
            f" {_column(conn, 'moltbook_comments', 'format', 'c')}, {_column(conn, 'moltbook_comments', 'experiment', 'c')}"
            " FROM moltbook_comments c WHERE c.comment_id IS NOT NULL AND c.at >= ? AND c.at < ?"
        )
        params += [since, until]
    if not parts:
        return []
    rows = conn.execute(
        "SELECT w.*, m.checkpoint, m.score, m.comment_count FROM (" + " UNION ALL ".join(parts) + ") w"
        " JOIN moltbook_post_metrics m ON m.kind = w.kind AND m.target_id = w.id"
        " JOIN (SELECT kind, target_id, max(checkpoint) AS checkpoint FROM moltbook_post_metrics GROUP BY kind, target_id)"
        "   newest ON newest.kind = m.kind AND newest.target_id = m.target_id AND newest.checkpoint = m.checkpoint"
        " ORDER BY w.at DESC",
        params,
    ).fetchall()
    return [
        {"id": r["id"], "kind": r["kind"], "format": r["format"], "experiment": r["experiment"], "text": _cut(r["text"]),
         "result": int(r["score"] or 0) + int(r["comment_count"] or 0), "checkpoint": r["checkpoint"], "at": r["at"]}
        for r in rows
    ]


def _cut(text: Any) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= TEXT_CHARS else text[: TEXT_CHARS - 3] + "..."


def format_key(write: Json) -> str:
    return write.get("format") or f"({write['kind']})"


def by_format(writes: Iterable[Json]) -> list[Json]:
    """Average result per format, the best average first."""
    groups: dict[str, list[Json]] = {}
    for write in writes:
        groups.setdefault(format_key(write), []).append(write)
    out = []
    for name, items in groups.items():
        item: Json = {"format": name, "n": len(items), "avg": _num(sum(w["result"] for w in items) / len(items))}
        views = [w["views"] for w in items if "views" in w]
        if views:
            item["avg_views"] = _num(sum(views) / len(views))
        out.append(item)
    return sorted(out, key=lambda g: (-g["avg"], -g["n"], g["format"]))


def _surface(writes: list[Json]) -> Json:
    ranked = sorted(writes, key=lambda w: (-w["result"], -w["at"]))
    best = ranked[:TOP]
    rest = [w for w in ranked if w not in best]
    worst = list(reversed(rest))[:TOP]
    shown = lambda w: {k: v for k, v in w.items() if v is not None and k != "at"}  # noqa: E731
    out: Json = {"measured": len(writes), "best": [shown(w) for w in best], "worst": [shown(w) for w in worst],
                 "formats": by_format(writes)}
    if writes:
        out["avg"] = _num(sum(w["result"] for w in writes) / len(writes))
    return out


def _signed(value: Any) -> str:
    return f"+{value}" if isinstance(value, (int, float)) and value >= 0 else str(value)


def _line(text: str) -> str:
    return text if len(text) <= LINE_CHARS else text[: LINE_CHARS - 3] + "..."


def _surface_line(name: str, days: int, surface: Json) -> str:
    if not surface["measured"]:
        return f"{name} last {days}d: nothing measured yet."
    best = surface["best"][0]
    formats = ", ".join(f"{g['format']} {g['avg']} ({g['n']})" for g in surface["formats"][:4])
    return _line(
        f"{name} last {days}d: {surface['measured']} measured, avg result {surface['avg']};"
        f" best {format_key(best)} {best['result']} \"{best['text'][:50]}\"; by format: {formats}"
    )


def board(days: int = 7) -> Json:
    conn = hand.db()
    try:
        since = hand.now() - days * DAY
        people = audience(conn)
        x = _surface(x_writes(conn, since))
        moltbook = _surface(moltbook_writes(conn, since))
        daily = moltbook_daily(conn)
    finally:
        conn.close()
    runs = playbooks(since)
    said = "; ".join(
        f"{a['metric']} {a['value']}"
        + (" (" + ", ".join(f"{k} {_signed(a[k])}" for k in ("d1", "d7") if k in a) + ")" if "d1" in a or "d7" in a else "")
        for a in people
    )
    lines = [
        _line("Audience: " + (said or "no snapshot yet.")),
        _surface_line("X", days, x),
        _surface_line("Moltbook", days, moltbook),
    ]
    daily_line = _daily_line(daily) if daily else None
    if daily_line:
        lines.append(daily_line)
    if runs:
        lines.append(_playbooks_line(days, runs))
    return {"days": days, "at": datetime.fromtimestamp(hand.now(), timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "audience": people, "x": x, "moltbook": moltbook, "moltbook_daily": daily, "playbooks": runs,
            "lines": lines}
