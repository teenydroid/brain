"""The proof events a lore episode may be written from, newest first.

Each is something a ledger or GitHub says happened, never something a
model said:

  generation   a generation of the evolve loop merged into main, promoted by
               the loop or merged by a person (state/evolve/archive.jsonl),
               with its chronicle page when there is one
               (brain/narrative/chronicle/gen-<n>.md)
  merged_pr    a pull request of Teeny's merged into somebody else's project
               (hands/gh activity)
  bounty_paid  a bounty whose prize the wallet paid (the `bounties` table)

A source that can't be read is named in `unavailable` (and on stderr), and
the rest are still answered: no event is ever made up to fill a gap. With no
event the answer is only `{"events": []}`, so the episode playbook, which
requires it, skips without a model turn. Nothing here is
written, so DRY_RUN does not apply.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .. import hand

Json = dict[str, Any]
#: The most of a chronicle page an event carries.
CHRONICLE_CHARS = 1500


def _brain() -> Path:
    """The live brain the core boots (bin/lib/teeny.sh's TEENY_BRAIN)."""
    return Path(os.environ.get("OPENHUMAN_WORKSPACE") or hand.root() / "brain")


def _when(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _iso(at: float) -> str:
    return dt.datetime.fromtimestamp(at, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generations(since: float) -> list[Json]:
    """The generations merged since SINCE, from the evolve archive (an
    append-only file: an entry's latest line wins)."""
    path = hand.state_dir() / "evolve" / "archive.jsonl"
    entries: dict[str, Json] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                entries[record["id"]] = {**entries.get(record["id"], {}), **record}
    out = []
    for entry in entries.values():
        gen = entry.get("gen")
        merged = _when(entry.get("merged_at"))
        if not isinstance(gen, int) or merged is None or merged < since:
            continue
        page = _brain() / "narrative" / "chronicle" / f"gen-{gen}.md"
        try:
            chronicle = page.read_text(encoding="utf-8")[:CHRONICLE_CHARS]
        except OSError:
            chronicle = None
        out.append({
            "kind": "generation",
            "at": _iso(merged),
            "gen": gen,
            "title": entry.get("title"),
            "component": entry.get("component"),
            "merged_by": "the loop" if entry.get("promoted_at") else "the operator",
            "pr_url": entry.get("pr_url"),
            "chronicle": chronicle,
        })
    return out


def bounties_paid(since: float) -> list[Json]:
    path = hand.state_dir() / "hands.db"
    if not path.is_file():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT id, title, paid_at FROM bounties WHERE status = 'paid' AND paid_at >= ?"
                            " ORDER BY paid_at", (since,)).fetchall()
    except sqlite3.OperationalError:  # no bounty was ever opened
        return []
    finally:
        conn.close()
    return [{"kind": "bounty_paid", "at": _iso(r["paid_at"]), "bounty_id": r["id"], "title": r["title"]} for r in rows]


def merged_prs(days: int) -> list[Json]:
    """Teeny's pull requests merged into projects it doesn't own."""
    from ..gh import reads
    from ..gh.client import is_own

    got = reads.activity(days)
    out = []
    for item in got.get("merged") or []:
        repo = item.get("repo") or ""
        if not repo or is_own(repo.split("/", 1)[0]):
            continue
        out.append({"kind": "merged_pr", "at": item.get("merged_at"), "repo": repo, "number": item.get("number"),
                    "title": item.get("title"), "html_url": item.get("html_url")})
    return out


def recent(days: int = 1) -> Json:
    since = hand.now() - days * 86400
    found: list[Json] = []
    unavailable: list[str] = []
    for name, read in (("evolve", lambda: generations(since)), ("bounty", lambda: bounties_paid(since)),
                       ("gh", lambda: merged_prs(days))):
        try:
            found.extend(read())
        except Exception as exc:  # noqa: BLE001 - one unreadable source never hides the others
            unavailable.append(f"{name}: {type(exc).__name__}")
    found.sort(key=lambda e: _when(e.get("at")) or 0.0, reverse=True)
    if unavailable:
        print(f"hands/lore: could not read {', '.join(unavailable)}", file=sys.stderr)
    if not found:
        # Only `events`, so a playbook that requires it skips without a
        # model turn (bin/act: a required input of only blanks is empty).
        return {"events": []}
    return {"days": days, "since": _iso(since), "events": found, **({"unavailable": unavailable} if unavailable else {})}
