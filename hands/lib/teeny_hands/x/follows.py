"""Who follows the account, as X's webhook told us: `x_follows`.

The X Activity API pushes `follow.follow` and `follow.unfollow` for the
account (docs.x.com/x-api/activity/introduction, "Follow events"; payload
`{source, target}`, each `{data: <v2 user>}`, `source` the follower). Each
one about somebody else following (or unfollowing) the account becomes, or
moves, one row here, keyed on the follower's user id. Nothing is read from X
for it, so "does this account follow @teenydroid" is a free local lookup for
everyone who followed or unfollowed after the subscription began; for anyone
else it is unknown (`follow_state` answers None), and only a paid
`hands/x users` lookup can say.

X may deliver an event twice, or late. A row only moves to an event newer
than the last one it took (`event_at`), so a replayed or reordered follow
cannot undo a later unfollow.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from .. import hand
from . import open_db
from .model import Json

FOLLOW = "follow.follow"
UNFOLLOW = "follow.unfollow"
EVENTS = (FOLLOW, UNFOLLOW)

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_follows (
    user_id         TEXT PRIMARY KEY,
    username        TEXT,
    followed_at     REAL,
    unfollowed_at   REAL,
    event_at        REAL NOT NULL,
    user_created_at TEXT,
    followers       INTEGER,
    verified        INTEGER,
    updated_at      REAL NOT NULL
);
"""


def _db():
    return open_db(SCHEMA)


def _epoch(stamp: Any) -> float | None:
    """An ISO 8601 time (X's `created_at`) as seconds since the epoch."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.timestamp() if parsed.tzinfo else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _user(side: Any) -> Mapping[str, Any]:
    """`source` or `target`: `{data: user}`, or the user itself."""
    if not isinstance(side, Mapping):
        return {}
    data = side.get("data")
    return data if isinstance(data, Mapping) else side


def from_delivery(delivery: Any, me_id: str) -> list[Json]:
    """The follow events in one delivery that are somebody else following or
    unfollowing the account: `{kind, userId, username?, at, createdAt?,
    followers?, verified?}`. The account's own follows of others (X sends
    both directions unless the subscription asks for `inbound`) and events
    for another account are dropped."""
    if not isinstance(delivery, Mapping):
        return []
    data = delivery.get("data")
    if not isinstance(data, Mapping) or data.get("event_type") not in EVENTS:
        return []
    target_filter = (data.get("filter") or {}).get("user_id") if isinstance(data.get("filter"), Mapping) else None
    if target_filter is not None and str(target_filter) != me_id:
        return []
    payload = data.get("payload") if isinstance(data.get("payload"), Mapping) else {}
    source, target = _user(payload.get("source")), _user(payload.get("target"))
    follower, followed = source.get("id"), target.get("id")
    if not follower or str(followed) != me_id or str(follower) == me_id:
        return []
    event: Json = {"kind": data["event_type"], "userId": str(follower), "at": _epoch(data.get("created_at")) or hand.now()}
    metrics = source.get("public_metrics") if isinstance(source.get("public_metrics"), Mapping) else {}
    for key, value in (
        ("username", source.get("username")),
        ("createdAt", source.get("created_at")),
        ("followers", _int(metrics.get("followers_count"))),
        ("verified", source.get("verified") if isinstance(source.get("verified"), bool) else None),
    ):
        if value is not None:
            event[key] = value
    return [event]


def record(events: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Each event into `x_follows`. A row moves only to an event newer than
    the last it took; an older or repeated one is `stale`."""
    counts = {"followed": 0, "unfollowed": 0, "stale": 0}
    conn = _db()
    try:
        now = hand.now()
        for event in events:
            at = float(event["at"])
            following = event["kind"] == FOLLOW
            row = conn.execute("SELECT event_at FROM x_follows WHERE user_id = ?", (event["userId"],)).fetchone()
            if row is not None and row["event_at"] >= at:
                counts["stale"] += 1
                continue
            verified = event.get("verified")
            conn.execute(
                "INSERT INTO x_follows (user_id, username, followed_at, unfollowed_at, event_at, user_created_at,"
                " followers, verified, updated_at) VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET"
                "  username = COALESCE(excluded.username, username),"
                "  followed_at = COALESCE(excluded.followed_at, followed_at),"
                "  unfollowed_at = CASE WHEN excluded.followed_at IS NOT NULL THEN NULL ELSE excluded.unfollowed_at END,"
                "  event_at = excluded.event_at,"
                "  user_created_at = COALESCE(excluded.user_created_at, user_created_at),"
                "  followers = COALESCE(excluded.followers, followers),"
                "  verified = COALESCE(excluded.verified, verified),"
                "  updated_at = excluded.updated_at",
                (
                    event["userId"],
                    event.get("username"),
                    at if following else None,
                    None if following else at,
                    at,
                    event.get("createdAt"),
                    event.get("followers"),
                    None if verified is None else int(verified),
                    now,
                ),
            )
            counts["followed" if following else "unfollowed"] += 1
    finally:
        conn.close()
    return {k: v for k, v in counts.items() if v}


def follow_state(user_ids: Iterable[str]) -> dict[str, bool | None]:
    """Whether each account follows the account, from `x_follows` only:
    True (its last event was a follow), False (an unfollow), None (no event
    seen: it followed before the subscription, or never). Reads nothing from
    X; hands/giveaway asks `hands/x users` only about the None ones."""
    ids = [str(i) for i in user_ids if i]
    state: dict[str, bool | None] = {i: None for i in ids}
    if not ids:
        return state
    conn = _db()
    try:
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" for _ in chunk)
            for row in conn.execute(
                f"SELECT user_id, followed_at, unfollowed_at FROM x_follows WHERE user_id IN ({marks})", chunk
            ):
                state[row["user_id"]] = row["followed_at"] is not None and row["unfollowed_at"] is None
    finally:
        conn.close()
    return state
