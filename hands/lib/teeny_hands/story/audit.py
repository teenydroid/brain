"""The audit of the story bible: is every beat told from something real?

The deterministic half, here: every ref a beat cites must resolve, and a
beat on X must name a post the X ledger holds. A ref is a desk source:

  <table>:<key>          a row of one of TABLES (stats: <date>:<source>:<metric>)
  evolve:<id>            an entry of state/evolve/archive.jsonl
  file:<path>            a file inside the checkout
  gh:<owner/repo>#<n>    GitHub; not checked here (no network), listed as `unchecked`

A beat with no ref at all, a ref of no known form, or one that does not
resolve is reported as `unsourced`. Nothing is struck or changed: the
audit reports.

The second half, a model reading each storyline's fields against the
ledgers and striking what it cannot source, comes later (docs/hands.md,
"story audit"). Its seam is `model_check`, which this half does not call
a model through: it answers `None`, and the report says `model_checked`
false.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .. import hand
from ..lore import events as lore
from . import store

Json = dict[str, Any]

#: The ledger tables a ref may name, each with the column its key is.
TABLES = {
    "wallet_payments": "id",
    "surplus_runs": "id",
    "people_promises": "id",
    "media_assets": "id",
    "x_mentions": "id",
    "x_posts": "id",
    "stimuli": "id",
    "bounties": "id",
}


class Resolver:
    def __init__(self) -> None:
        path = hand.state_dir() / "hands.db"
        self.conn: sqlite3.Connection | None = None
        self.tables: set[str] = set()
        if path.is_file():
            self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            self.tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self._archive: dict[str, Json] | None = None

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()

    def _row(self, table: str, where: str, params: tuple[Any, ...]) -> bool:
        if self.conn is None or table not in self.tables:
            return False
        return self.conn.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", params).fetchone() is not None

    def resolve(self, ref: str) -> str | None:
        """None when REF resolves, "unchecked" when it cannot be checked
        here, else why it does not."""
        kind, sep, key = ref.partition(":")
        if not sep or not key:
            return "not a source ref"
        if kind == "gh":
            return "unchecked"
        if kind == "evolve":
            if self._archive is None:
                self._archive = lore.archive()
            return None if key in self._archive else "no such evolve entry"
        if kind == "file":
            root = hand.root().resolve()
            target = (root / key).resolve()
            if not target.is_relative_to(root):
                return "outside the checkout"
            return None if target.is_file() else "no such file"
        if kind == "stats":
            parts = key.split(":")
            if len(parts) != 3:
                return "a stats ref is stats:<date>:<source>:<metric>"
            return None if self._row("stats", "date = ? AND source = ? AND metric = ?", tuple(parts)) else "no such stats row"
        if kind in TABLES:
            return None if self._row(kind, f"{TABLES[kind]} = ?", (key,)) else f"no such {kind} row"
        return "not a source ref"


def model_check(storyline: Json, beats: list[Json]) -> Json | None:
    """The seam for the model-checked audit (a second model reads the
    storyline's fields against the ledgers and names what it cannot
    source). Not built yet: no model is called, and None is the answer."""
    return None


def audit(key: str | None = None) -> Json:
    conn = store.db()
    resolver = Resolver()
    try:
        rows = conn.execute("SELECT * FROM storylines" + (" WHERE key = ?" if key else "") + " ORDER BY created_at, key",
                            (key,) if key else ()).fetchall()
        if key and not rows:
            raise hand.Refused("unknown storyline", storyline=key)
        out, unsourced_total, unchecked = [], 0, []
        for row in rows:
            told = store.beats(conn, row["key"])
            unsourced = []
            refs = 0
            for beat in told:
                if beat["surface"] == "x" and resolver.resolve(f"x_posts:{beat['post_id']}") is not None:
                    unsourced.append({"beat_id": beat["id"], "ref": f"x_posts:{beat['post_id']}",
                                      "why": "the post is not in the X ledger"})
                if not beat["refs"]:
                    unsourced.append({"beat_id": beat["id"], "ref": None, "why": "the beat cites nothing"})
                for ref in beat["refs"]:
                    refs += 1
                    why = resolver.resolve(ref)
                    if why == "unchecked":
                        unchecked.append({"storyline": row["key"], "beat_id": beat["id"], "ref": ref})
                    elif why is not None:
                        unsourced.append({"beat_id": beat["id"], "ref": ref, "why": why})
            unsourced_total += len(unsourced)
            checked = model_check(dict(row), told)
            out.append({"key": row["key"], "status": row["status"], "beats": len(told), "refs": refs,
                        "unsourced": unsourced, **({"model": checked} if checked is not None else {})})
        return {"storylines": out, "unsourced": unsourced_total, "unchecked": unchecked, "model_checked": False,
                "clean": unsourced_total == 0}
    finally:
        resolver.close()
        conn.close()
