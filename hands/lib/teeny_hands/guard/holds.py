"""The record of everything the content filter stopped, kept for a person
to look at afterwards, and what the operator's decision on one does.

The filter is deliberately blunt, and a blunt filter is wrong sometimes.
The public journal says only that something was held; this log, which
never leaves the box, keeps the text, so an operator can say whether the
filter was right. Approving a held link puts its host on the live
allowlist, approving a held mention sends it back to the queue, approving
a withheld draft releases it. Rejecting confirms the filter.

Every write here is best-effort from the gate's point of view: a log that
cannot be reached must never turn a hold into a publish.

A port of the hold log in `crates/teeny-store/src/holds.rs` (Mongo's `holds`
and `holds_links`, here `guard_holds` and `guard_approved_links` in
state/hands.db) and of `crates/teeny-safety/src/review.rs`. Ids are the
table's integers rather than the Mongo log's `h-...` strings.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import logging
import sqlite3
from typing import Any, Callable, Iterator

from .. import hand
from .links import parse_url

log = logging.getLogger(__name__)

# How many holds a list returns by default, and at most.
LIST_DEFAULT = 100
LIST_MAX = 500

# Where a hold is in review. `releasing` is a decision's claim while its
# effect is carried out, never a resting state: it ends approved or
# rejected, or back at pending when the release failed. One left there means
# the process stopped mid-way, and the hold is not offered again, because
# the post may be out. `released` is not written by this hand; it is kept
# open for hands/x to mark a published hold, should it choose to.
STATUSES = ("pending", "releasing", "approved", "rejected", "released")
DECISIONS = ("approved", "rejected")

# What was held. `message` is somebody else's text on the way in; `draft`
# the account's own on the way out; `link` a draft that went out with its
# links cut; `prompt` a picture the account declined to draw; `task` an
# operator's ad-hoc task.
KINDS = ("message", "draft", "link", "prompt", "task")

# Where the held text was going, or coming from.
SURFACES = (
    "x",
    "telegram",
    "task",
    "chart",
    "calendar",
    "morning",
    "moon",
    "narrative",
    "article",
    "quote",
    "lore",
    # Long-form surfaces (Act 1): GitHub titles, bodies and comments, Moltbook
    # posts, AgentMail mail. Their limits are in gate.py's LIMITS.
    "github",
    "moltbook",
    "mail",
)

DIRECTIONS = ("inbound", "outbound", "narrative", "article")

# Which check held it.
BY = ("screen", "judge", "links")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS guard_holds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    direction TEXT NOT NULL,
    kind TEXT NOT NULL,
    context TEXT NOT NULL,
    category TEXT NOT NULL,
    reason TEXT NOT NULL,
    held_by TEXT NOT NULL,
    text TEXT NOT NULL,
    links TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    decided_at TEXT,
    note TEXT,
    effect TEXT
);
CREATE INDEX IF NOT EXISTS guard_holds_status_at ON guard_holds (status, at);
CREATE TABLE IF NOT EXISTS guard_approved_links (
    entry TEXT PRIMARY KEY,
    hold_id INTEGER,
    at TEXT NOT NULL
);
"""


@contextlib.contextmanager
def _store() -> Iterator[sqlite3.Connection]:
    conn = hand.db()
    try:
        conn.executescript(_SCHEMA)
        yield conn
    finally:
        conn.close()


def _iso_now() -> str:
    """Now, as the TypeScript service stamped holds (and TEENY_NOW pins it)."""
    at = datetime.datetime.fromtimestamp(hand.now(), datetime.timezone.utc)
    return at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}Z"


def _one_of(name: str, value: Any, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise hand.HandError(f"{name} must be one of {', '.join(allowed)}, not {value!r}")
    return value


def _hold(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "at": row["at"],
        "direction": row["direction"],
        "kind": row["kind"],
        "context": json.loads(row["context"]),
        "category": row["category"],
        "reason": row["reason"],
        "by": row["held_by"],
        "text": row["text"],
        "links": json.loads(row["links"]) if row["links"] is not None else None,
        "status": row["status"],
        "decided_at": row["decided_at"],
        "note": row["note"],
        "effect": row["effect"],
    }


def _select(conn: sqlite3.Connection, hold_id: int) -> dict[str, Any] | None:
    return _hold(conn.execute("SELECT * FROM guard_holds WHERE id = ?", (hold_id,)).fetchone())


def record(
    *,
    direction: str,
    kind: str,
    context: dict[str, Any],
    category: str,
    reason: str,
    by: str,
    text: str,
    links: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Writes one hold, pending review, and returns it."""
    _one_of("direction", direction, DIRECTIONS)
    _one_of("kind", kind, KINDS)
    _one_of("by", by, BY)
    _one_of("surface", (context or {}).get("surface"), SURFACES)
    with _store() as conn, hand.transaction(conn):
        cursor = conn.execute(
            "INSERT INTO guard_holds (at, direction, kind, context, category, reason, held_by, text, links)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _iso_now(),
                direction,
                kind,
                json.dumps(context, ensure_ascii=False, sort_keys=True),
                category,
                reason,
                by,
                text,
                json.dumps(links, ensure_ascii=False) if links is not None else None,
            ),
        )
        return _select(conn, cursor.lastrowid)  # type: ignore[return-value]


def get(hold_id: int) -> dict[str, Any] | None:
    with _store() as conn:
        return _select(conn, hold_id)


def list_holds(status: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    """Newest first; `limit` defaults to LIST_DEFAULT and is held to 1..LIST_MAX."""
    limit = max(1, min(LIST_MAX, LIST_DEFAULT if limit is None else limit))
    with _store() as conn:
        if status is None:
            rows = conn.execute("SELECT * FROM guard_holds ORDER BY at DESC, id DESC LIMIT ?", (limit,))
        else:
            _one_of("status", status, STATUSES)
            rows = conn.execute(
                "SELECT * FROM guard_holds WHERE status = ? ORDER BY at DESC, id DESC LIMIT ?", (status, limit)
            )
        return [_hold(row) for row in rows]  # type: ignore[misc]


def decide(hold_id: int, decision: str, note: str | None = None, effect: str | None = None) -> dict[str, Any] | None:
    """Closes a hold; None when there is no such hold. An empty note or
    effect leaves the one already there."""
    _one_of("decision", decision, DECISIONS)
    with _store() as conn, hand.transaction(conn):
        conn.execute(
            "UPDATE guard_holds SET status = ?, decided_at = ?, note = COALESCE(?, note), effect = COALESCE(?, effect)"
            " WHERE id = ?",
            (decision, _iso_now(), note or None, effect or None, hold_id),
        )
        return _select(conn, hold_id)


def _move(hold_id: int, source: str, target: str, effect: str | None = None) -> dict[str, Any] | None:
    """Moves a hold from one status to another in one guarded update, so two
    callers cannot both see `source`; None when it is not at `source`."""
    with _store() as conn, hand.transaction(conn):
        moved = conn.execute(
            "UPDATE guard_holds SET status = ?, effect = COALESCE(?, effect) WHERE id = ? AND status = ?",
            (target, effect, hold_id, source),
        )
        return _select(conn, hold_id) if moved.rowcount else None


def claim_release(hold_id: int) -> dict[str, Any] | None:
    """Takes a pending hold for a decision, atomically: pending becomes
    releasing, and the hold is returned. None when it is not pending (another
    decision has it, or it is decided) or does not exist. Only the caller
    that gets the hold back may carry out the decision, so two approvals of
    one withheld draft publish it once."""
    return _move(hold_id, "pending", "releasing")


def unclaim_release(hold_id: int, error: str) -> dict[str, Any] | None:
    """Puts a claimed hold back to pending after its release failed, with
    the failure as its effect. None when it was not claimed."""
    return _move(hold_id, "releasing", "pending", f"release failed, still pending: {error}")


def counts() -> dict[str, int]:
    """How many holds are in each status. `releasing` (and `released`) are
    left out when there are none, so the counts read as they always did."""
    tally = {"pending": 0, "approved": 0, "rejected": 0}
    with _store() as conn:
        for status, total in conn.execute("SELECT status, count(*) FROM guard_holds GROUP BY status"):
            if status in STATUSES:
                tally[status] = tally.get(status, 0) + total
    return tally


def allowed_links() -> list[str]:
    """Hosts an operator approved, in allowlist spelling. The gate reads
    these alongside the built-in list, so an approval takes effect
    everywhere the gate runs."""
    with _store() as conn:
        return [row[0] for row in conn.execute("SELECT entry FROM guard_approved_links ORDER BY at, entry")]


def approved_link_rows() -> list[dict[str, Any]]:
    """The approved entries with where they came from, for the operator."""
    with _store() as conn:
        rows = conn.execute("SELECT entry, hold_id, at FROM guard_approved_links ORDER BY at, entry")
        return [dict(row) for row in rows]


def allow_link(entry: str, hold_id: int | None) -> None:
    """Allows a host (or host/path), once."""
    with _store() as conn, hand.transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO guard_approved_links (entry, hold_id, at) VALUES (?, ?, ?)",
            (entry, hold_id, _iso_now()),
        )


# --- review -------------------------------------------------------------------


class ReviewError(hand.HandError):
    """An approval's effect failed (a release that did not publish). The hold
    goes back to pending, the failure recorded, so it can be tried again."""


def allowlist_entry_for(link: str) -> str | None:
    """A cut link as an allowlist entry: its host, with subdomains."""
    lower = link[:8].lower()
    absolute = link if lower.startswith(("http://", "https://")) else f"https://{link}"
    url = parse_url(absolute)
    return url.host if url is not None and url.host else None


Requeue = Callable[[str], bool]
Release = Callable[[dict[str, Any]], str]


def review_hold(
    hold_id: int,
    decision: str,
    note: str | None = None,
    *,
    requeue: Requeue | None = None,
    release: Release | None = None,
    refresh_links: Callable[[], None] | None = None,
) -> dict[str, Any] | None:
    """Takes an operator's decision on a hold. None for an unknown id.

    `requeue(post_id)` puts an X mention back to be answered and says whether
    it was still there; `release(hold)` publishes a withheld draft and says
    where ("published on X as 99"); `refresh_links()` tells a gate in this
    process to re-read the approved links. Each is optional, and without it
    the approval is recorded and says so.

    A decision is taken once: re-deciding would re-run the effect (a second
    publish of a released draft, a second requeue), so the hold is claimed
    first, atomically, and a hold that cannot be claimed (decided, or being
    decided by a second click this instant) is answered as it stands.

    A failed effect puts the claim back, so the hold can be tried again. Once
    the effect has happened the hold is never pending again: a decision that
    cannot be written is retried once with the failure in its note, and if
    that fails too the hold stays `releasing`, visibly stuck rather than
    offered for a second publish."""
    _one_of("decision", decision, DECISIONS)
    held = claim_release(hold_id)
    if held is None:
        return get(hold_id)
    if decision == "rejected":
        effect = "confirmed the filter"
    else:
        try:
            effect = _approve(held, requeue, release, refresh_links)
        except Exception as failure:
            try:
                unclaim_release(hold_id, str(failure))
            except Exception as error:  # noqa: BLE001 - the failure being raised matters more
                log.error("could not put failed hold %s back to pending: %s", hold_id, error)
            raise
    try:
        return decide(hold_id, decision, note, effect)
    except sqlite3.Error as first:
        log.warning("carried out the decision on hold %s but could not record it; retrying: %s", hold_id, first)
        late = f"decision recorded late: {first}"
        return decide(hold_id, decision, f"{note} ({late})" if note else late, effect)


def _approve(
    held: dict[str, Any],
    requeue: Requeue | None,
    release: Release | None,
    refresh_links: Callable[[], None] | None,
) -> str:
    kind = held["kind"]
    if kind == "link":
        hosts: list[str] = []
        for cut in held["links"] or []:
            host = allowlist_entry_for(cut["link"])
            if host and host not in hosts:
                hosts.append(host)
        if not hosts:
            return "no usable host in the cut links; nothing allowlisted"
        for host in hosts:
            allow_link(host, held["id"])
        if refresh_links is not None:
            refresh_links()
        return f"allowlisted {', '.join(hosts)}"
    if kind == "message":
        context = held["context"]
        post_id = context.get("post_id") or context.get("postId")
        if context.get("surface") != "x" or not post_id:
            return "recorded; a held chat message cannot be answered after the fact"
        if requeue is None:
            return "recorded; no queue to return it to"
        return "requeued to be answered" if requeue(str(post_id)) else "recorded; the mention is no longer in the queue"
    if kind == "draft":
        # A release that fails raises, and the hold goes back to pending:
        # marking it approved with a failure note would be a decision that
        # did nothing, dressed as one that did.
        if release is None:
            return "recorded; nothing here can publish it"
        return release(held)
    return "recorded"
