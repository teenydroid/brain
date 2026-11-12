"""Teeny's own receipts of the last few days, as short dated fact lines its
Moltbook posts may cite (docs/hands.md, "moltbook material"). Read-only,
deterministic, from Teeny's own state:

  guard_holds  how many drafts the guard held, by day and category (a hold
               whose judge could not be reached is "judge unreachable");
               never the held text, never the hold's reason
  health       the health check's alarms and recoveries (its `health`
               stimuli) and what state/health.json says now
  merged_prs   Teeny's PRs merged into teenydroid/brain (hands/gh
               `activity`, through its cache); skipped with a note when
               there is no GITHUB_TOKEN
  journal      the beats of the last JOURNAL_FILES days of state/journal/
               (each beat's first line; quiet beats left out) and its
               bullets, newest first, at most JOURNAL_LINES

Every line goes through `teeny_hands.scrub`: no URL or query string, no
address, nothing that looks like a key. What could not be read is in
`notes`, never an error: a missing source is a shorter list.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import hand
from ..scrub import scrub

DAY = 86400.0
REPO = "teenydroid/brain"
JOURNAL_FILES = 7
JOURNAL_LINES = 40
LINE_CHARS = 200
#: The guard judge's own words when it fails closed (as bin/health counts them).
JUDGE_UNREACHABLE = "the judge could not be reached%"

_BEAT = re.compile(r"^##\s+(\d{1,2}:\d{2})\s*·\s*(\w+)")
_BULLET = re.compile(r"^\s*[-*]\s+(.+)")
_DATE_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")

Json = dict[str, Any]


def _line(text: str) -> str:
    return scrub(text, LINE_CHARS)


def _stamp(at: float, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return datetime.fromtimestamp(at, timezone.utc).strftime(fmt)


def guard_holds(since: float) -> Json:
    stamp = _stamp(since, "%Y-%m-%dT%H:%M:%S")
    try:
        conn = hand.db()
        try:
            rows = conn.execute(
                "SELECT substr(at, 1, 10) AS day,"
                " CASE WHEN reason LIKE ? THEN 'judge unreachable' ELSE category END AS what, count(*)"
                " FROM guard_holds WHERE at >= ? GROUP BY day, what ORDER BY day DESC, what",
                (JUDGE_UNREACHABLE, stamp),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError:  # no holds table yet
        rows = []
    days: dict[str, Counter] = {}
    for day, what, count in rows:
        days.setdefault(day, Counter())[what] += int(count)
    lines = []
    for day, counts in days.items():
        total = sum(counts.values())
        parts = ", ".join(f"{what} {n}" for what, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
        lines.append(_line(f"{day}: {total} guard hold{'s' if total != 1 else ''} ({parts})"))
    return {"count": sum(sum(c.values()) for c in days.values()), "lines": lines}


def health(since: float) -> list[str]:
    lines: list[str] = []
    try:
        conn = hand.db()
        try:
            rows = conn.execute(
                "SELECT at, kind, text FROM stimuli WHERE surface = 'health' AND at >= ? ORDER BY at", (since,)
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        rows = []
    lines += [_line(f"{_stamp(at)} {kind}: {text}") for at, kind, text in rows]
    try:
        state = json.loads((hand.state_dir() / "health.json").read_text())
    except (FileNotFoundError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    for name, what in (("alarm", "model alarm open"), ("cron_alarm", "cron alarm open")):
        alarm = state.get(name)
        if isinstance(alarm, dict) and isinstance(alarm.get("opened_at"), (int, float)):
            issue = f" ({REPO}#{alarm['issue']})" if alarm.get("issue") else ""
            lines.append(_line(f"{_stamp(alarm['opened_at'])} {what}{issue}"))
    failed = state.get("last_failed_at")
    if isinstance(failed, (int, float)) and failed >= since and int(state.get("failures") or 0) > 0:
        lines.append(_line(f"{_stamp(failed)} last failed model check ({int(state['failures'])} in a row):"
                           f" {state.get('last_error') or 'no error recorded'}"))
    return lines


def merged_prs(days: int, notes: list[str]) -> list[str]:
    if not hand.setting("GITHUB_TOKEN"):
        notes.append("merged_prs: GITHUB_TOKEN not set")
        return []
    from ..gh import reads as gh

    try:
        merged = gh.activity(days).get("merged") or []
    except (hand.HandError, hand.Refused) as error:
        notes.append(_line(f"merged_prs: {error}"))
        return []
    lines = []
    for pr in merged:
        if not isinstance(pr, dict) or str(pr.get("repo") or "").lower() != REPO:
            continue
        when = str(pr.get("merged_at") or "")[:10] or "?"
        lines.append(_line(f"{when} merged {REPO}#{pr.get('number')}: {pr.get('title') or ''}".rstrip()))
    return lines


def journal(since: float) -> list[str]:
    folder = hand.state_dir() / "journal"
    first = _stamp(since, "%Y-%m-%d")
    files = sorted((p for p in folder.glob("*.md") if _DATE_FILE.match(p.name) and p.stem >= first), reverse=True)
    lines: list[str] = []
    for path in files[:JOURNAL_FILES]:
        try:
            text = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        beat: str | None = None
        for raw in text:
            if len(lines) >= JOURNAL_LINES:
                return lines
            heading = _BEAT.match(raw)
            if heading:
                beat = heading.group(1) if heading.group(2) != "quiet" else None
                continue
            if raw.startswith("#"):
                beat = None
                continue
            bullet = _BULLET.match(raw)
            if bullet:
                lines.append(_line(f"{path.stem}: {bullet.group(1)}"))
                continue
            if beat and raw.strip() and not raw.startswith(">") and not raw.startswith("did:"):
                lines.append(_line(f"{path.stem} {beat}: {raw.strip()}"))
                beat = None
    return lines


def material(days: int = 7) -> Json:
    since = hand.now() - days * DAY
    notes: list[str] = []
    return {
        "days": days,
        "since": _stamp(since, "%Y-%m-%d"),
        "guard_holds": guard_holds(since),
        "health": health(since),
        "merged_prs": merged_prs(days, notes),
        "journal": journal(since),
        "notes": notes,
    }
