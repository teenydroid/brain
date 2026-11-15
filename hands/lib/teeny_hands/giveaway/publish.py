"""A round in the public diary (teenydroid/diary, `giveaways/`), in the shape
docs/drops-schema.md and web/src/lib/drops.ts fix: `giveaways/drop-<id>.json`
for each round and `giveaways/index.json` listing them.

The files are built from the tables alone, field by field, and carry no free
text but the round's prompt (which the guard passed when the round post went
out) and the entrants' handles and reasons (code's words). Live, they go out
through the same checkout, lock, secret gate and push as bin/lib/receipts.py
(its helpers, imported); a push that fails resets the checkout to the remote.
Every pushed round file is recorded in `giveaway_publications` with its
sha256, which receipts publishes as a `giveaway.published` receipt. Under
DRY_RUN the files are written to state/giveaway-preview/giveaways/ and
nothing else is touched.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .. import hand
from ..wallet import solana
from . import caps, store

Json = dict[str, Any]
DIR = "giveaways"
INDEX = "index.json"
PREVIEW = "giveaway-preview"


def iso(at: float | None) -> str | None:
    if at is None:
        return None
    return _dt.datetime.fromtimestamp(at, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def own_handle() -> str:
    return (hand.setting("X_BOT_USERNAME", "teenydroid") or "teenydroid").lstrip("@").lower()


def public_status(status: str) -> str:
    """The diary has no `void`: a round with nothing to pay shows as drawn,
    with no claims."""
    return "drawn" if status == "void" else status


def authority() -> str | None:
    value = hand.setting("GIVEAWAY_AUTHORITY")
    return value if solana.is_address(value) else None


def round_json(conn: sqlite3.Connection, row: sqlite3.Row) -> Json:
    rid = int(row["id"])
    draws = conn.execute(
        "SELECT d.*, e.created_at AS entered_at FROM giveaway_draws d JOIN giveaway_entries e ON e.id = d.entry_id"
        " WHERE d.round_id = ? ORDER BY e.created_at, e.id", (rid,)
    ).fetchall()
    order = sorted((d for d in draws if d["position"] is not None), key=lambda d: d["position"])
    claims = sorted((d for d in draws if d["kind"] is not None and d["leaf_index"] is not None),
                    key=lambda d: d["leaf_index"])
    seed = None
    if row["seed_sha256"]:
        seed = {"slot": int(row["seed_slot"]), "blockhash": row["seed_blockhash"], "sha256": row["seed_sha256"]}
    txs = {k: row[f"tx_{k}"] for k in ("create", "fund", "clawback") if row[f"tx_{k}"]}
    return {
        "id": rid,
        "tag": store.tag(rid),
        "status": public_status(row["status"]),
        "deadline": iso(row["deadline"]),
        "format": row["format"],
        "pool_tiny": caps.display(row["pool_raw"]),
        "winners": int(row["winners"]),
        "best_reply_tiny": caps.display(row["best_reply_raw"]) if row["best_reply_raw"] else "0",
        "prompt": row["prompt"],
        "post_id": row["post_id"],
        "post_url": f"https://x.com/{own_handle()}/status/{row['post_id']}",
        "cluster": row["cluster"],
        "mint": caps.TINY_MINT,
        "decimals": caps.TINY_DECIMALS,
        "token_program": caps.TOKEN_2022,
        "program_id": caps.REWARDS_PROGRAM,
        "authority": row["authority"] or authority(),
        "distribution_seed": row["distribution_seed"],
        "distributor": row["distributor"],
        "vault": row["vault"],
        "merkle_root": row["merkle_root"],
        "clawback_at": iso(row["clawback_at"]),
        "seed": seed,
        "entrants": [{"handle": d["handle"], "eligible": bool(d["eligible"]), "reason": d["reason"]} for d in draws],
        "draw_order": [d["handle"] for d in order],
        "claims": [
            {"index": int(d["leaf_index"]), "handle": d["handle"], "kind": d["kind"], "address": d["address"],
             "amount_atomic": str(int(d["amount_raw"])), "proof": json.loads(d["proof"] or "[]")}
            for d in claims
        ],
        "txs": txs,
    }


def index_json(conn: sqlite3.Connection) -> Json:
    rows = conn.execute("SELECT id, status, deadline FROM giveaway_rounds ORDER BY id").fetchall()
    return {"rounds": [{"id": int(r["id"]), "tag": store.tag(r["id"]), "status": public_status(r["status"]),
                        "deadline": iso(r["deadline"])} for r in rows]}


def text(obj: Json) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def digest(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def _write(root: Path, round_id: int, round_text: str, index_text: str) -> None:
    folder = root / DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"drop-{round_id}.json").write_text(round_text, encoding="utf-8")
    (folder / INDEX).write_text(index_text, encoding="utf-8")


def _receipts() -> Any:
    """bin/lib/receipts.py, whose checkout, secret gate and push this reuses."""
    lib = str(Path(__file__).resolve().parents[4] / "bin" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import receipts  # noqa: PLC0415

    return receipts


def _push(round_id: int, round_text: str, index_text: str) -> bool:
    """Commits and pushes the two files; False when they are already there."""
    receipts = _receipts()
    token = hand.setting("RECEIPTS_GITHUB_TOKEN") or hand.setting("DIARY_GITHUB_TOKEN") or hand.setting("GITHUB_TOKEN")
    if not token:
        raise hand.HandError("no token: set RECEIPTS_GITHUB_TOKEN (or GITHUB_TOKEN) in secrets/teeny.env")
    branch = receipts._branch()
    with hand.lock("receipts", timeout=60):
        repo = receipts._checkout(token)
        _write(repo, round_id, round_text, index_text)
        receipts._git(repo, "add", "--", DIR)
        if not receipts._git(repo, "diff", "--cached", "--name-only").strip():
            return False
        try:
            receipts.check_secrets(receipts._git(repo, "diff", "--cached", "--unified=0"))
        except hand.Refused as why:
            receipts._git(repo, "reset", "-q", "--hard", f"origin/{branch}")
            raise hand.Refused("the round file carries a secret, nothing committed", why=why.reason) from None
        receipts._git(repo, "commit", "-q", "-m", f"giveaways: {store.tag(round_id)}")
        try:
            receipts._git(repo, "push", "-q", "origin", f"HEAD:{branch}", token=token)
        except hand.HandError:
            receipts._git(repo, "reset", "-q", "--hard", f"origin/{branch}")
            raise
    return True


def sync(conn: sqlite3.Connection, round_id: int, overlay: Json | None = None) -> Json:
    """Publishes round `round_id` (with `overlay` laid over its JSON, for a
    rehearsal's numbers that were never stored) when its file changed.
    Under DRY_RUN: the preview only."""
    row = store.get(conn, round_id)
    if row is None:
        raise hand.HandError(f"there is no round {round_id}")
    body = round_json(conn, row)
    body.update(overlay or {})
    round_text, index_text = text(body), text(index_json(conn))
    sha = digest(round_text)
    if hand.dry_run():
        root = hand.state_dir() / PREVIEW
        _write(root, round_id, round_text, index_text)
        return {"dry_run": True, "preview": str(root / DIR / f"drop-{round_id}.json"), "sha256": sha}
    if overlay:
        raise hand.HandError("only a rehearsal publishes numbers it did not store")
    if row["published_sha256"] == sha:
        return {"published": False, "sha256": sha, "unchanged": True}
    pushed = _push(round_id, round_text, index_text)
    now = hand.now()
    conn.execute("UPDATE giveaway_rounds SET published_sha256 = ?, published_at = ? WHERE id = ?", (sha, now, round_id))
    conn.execute("INSERT OR IGNORE INTO giveaway_publications (round_id, status, sha256, at) VALUES (?, ?, ?, ?)",
                 (round_id, body["status"], sha, now))
    return {"published": True, "pushed": pushed, "sha256": sha, "path": f"{DIR}/drop-{round_id}.json"}
