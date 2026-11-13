"""The posting rules, each persisted in state/hands.db (docs/hands.md,
"Invariants every hand port must keep"). `publish` runs them after the
guard and before DRY_RUN; nothing else in the hand posts.

- The gap between own posts (crates/teeny-cadence/src/cadence.rs): an own
  post less than `POST_MIN_GAP_HOURS` after the last one, less
  `POST_GAP_TOLERANCE_MINUTES` (the jobs fire on the hour and then spend
  minutes drawing before they post), is refused. Three hours by default:
  brain/cron.toml spreads the day's own posts at least that far apart. Posts an hour apart are the
  repetitive-publishing shape the ranker demotes. Replies are never subject
  to it: they are conversations, not slots.
- One reply per post (crates/teeny-jobs/src/reply/job.rs:464), claimed
  before X is called and keyed on the post replied to, so neither a second
  job nor a retry after a crash can answer it twice.
- The pacing between replies (reply/job.rs:34-60): random within
  `REPLY_MIN_INTERVAL_MS..=REPLY_MAX_INTERVAL_MS`, because a fixed gap is its
  own signature. In-memory in the Rust; persisted here, and refused with the
  wait rather than slept through.
- Links cost money: X bills a post whose text carries a URL $0.20 against
  $0.015 without (docs.x.com, pricing), so posts and replies with a link are
  capped at `X_LINK_POSTS_MAX_PER_DAY` a UTC day (1 by default; 0 forbids
  them). A bare domain X would turn into a link counts.
- Attachments by minted id or Giphy id only, never a URL, never both
  (crates/teeny-agent/src/tools/post.rs:393): the media this account posts is
  always something it can account for.

Times are seconds since the epoch (`hand.now()`); what a caller reads is ISO
8601, as the legacy ledger wrote it.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .. import hand
from .client import number_setting
from .model import utf16_length

#: The hard cap, as X counts it for plain text.
MAX_POST_LENGTH = 280

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_posts (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('post', 'reply', 'quote')),
    text        TEXT NOT NULL,
    in_reply_to TEXT,
    quote_of    TEXT,
    media       TEXT NOT NULL DEFAULT '[]',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS x_posts_kind_created ON x_posts (kind, created_at);
CREATE TABLE IF NOT EXISTS x_replies (
    in_reply_to TEXT PRIMARY KEY,
    reply_id    TEXT,
    claimed_at  REAL NOT NULL,
    replied_at  REAL,
    note        TEXT
);
CREATE TABLE IF NOT EXISTS x_likes (
    post_id  TEXT PRIMARY KEY,
    liked_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS x_pacing (
    lane    TEXT PRIMARY KEY,
    last_at REAL NOT NULL,
    next_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS x_media (
    media_id   TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    mime       TEXT NOT NULL,
    bytes      INTEGER NOT NULL,
    dry_run    INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
"""

_X_ID = re.compile(r"^[0-9]{1,25}$")
_GIF_ID = re.compile(r"^[A-Za-z0-9]{1,64}$")
_MEDIA_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    add_tag_columns(conn)


def add_tag_columns(conn: sqlite3.Connection) -> None:
    """`format` and `experiment` on x_posts (teeny_hands/tags.py), added to
    a ledger created before they existed."""
    hand.add_columns(conn, "x_posts", tags.COLUMNS)


def iso(seconds: float) -> str:
    """A timestamp as JavaScript's `toISOString()` writes it, as the legacy
    ledger stored them and the logs show them."""
    at = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}Z"


def is_x_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_X_ID.match(value))


# --- the text ------------------------------------------------------------------------------


def check_length(text: str) -> None:
    """Refuse, don't fix (crates/teeny-safety/src/gate.rs:377): what is
    measured is the guard's rewrite, as X counts it."""
    length = utf16_length(text)
    if length > MAX_POST_LENGTH:
        raise hand.Refused(
            f"the post is {length} characters; X allows {MAX_POST_LENGTH}",
            rule="length",
            length=length,
            limit=MAX_POST_LENGTH,
            rewrite=True,
        )


# --- attachments ----------------------------------------------------------------------------


def check_attachments(
    conn: sqlite3.Connection, media_ids: Sequence[str], gif_id: str | None, live: bool
) -> None:
    """Only a media id this hand minted (`x_media`) or a Giphy id goes on a
    post, never a URL and never both. A rehearsal's placeholder id is good
    for another rehearsal, never for a live post."""
    if media_ids and gif_id:
        raise hand.Refused(
            "a post carries either a GIF or an uploaded file, not both; drop one", rule="attachments"
        )
    if gif_id is not None and not _GIF_ID.match(gif_id):
        raise hand.Refused("gif_id must be a Giphy id (letters and digits), never a URL", rule="attachments")
    for media_id in media_ids:
        row = None
        if _MEDIA_ID.match(media_id):
            row = conn.execute("SELECT dry_run FROM x_media WHERE media_id = ?", (media_id,)).fetchone()
        if row is None:
            raise hand.Refused(
                f"no media with id {media_id[:64]!r} was uploaded through hands/x upload; attach only ids it returned",
                rule="attachments",
            )
        if live and row["dry_run"]:
            raise hand.Refused(
                f"media {media_id} was a dry-run upload and never reached X; upload it again", rule="attachments"
            )


def record_media(
    conn: sqlite3.Connection, media_id: str, source: str, mime: str, size: int, dry_run: bool = False
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO x_media (media_id, source, mime, bytes, dry_run, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (media_id, source, mime, size, 1 if dry_run else 0, hand.now()),
    )


# --- links -----------------------------------------------------------------------------------


#: Anything X could turn into a link: a scheme, a www., or a bare
#: "name.tld". Broader than X's own list of TLDs on purpose: over-counting
#: only refuses a link post sooner, under-counting is a $0.20 surprise.
#: (hands/x keeps its own pattern; it never imports the guard's.)
_LINK = re.compile(
    r"(?<![A-Za-z0-9_@.\-])(?:https?://|www\.|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,24}(?![A-Za-z0-9-]))",
    re.IGNORECASE,
)


def has_link(text: str) -> bool:
    """Whether X would see a URL in the text (and bill the post as one)."""
    return bool(_LINK.search(text))


def check_link_budget(conn: sqlite3.Connection, text: str) -> None:
    """A post or reply with a link, only while today's link posts are under
    the cap. Run under the posting or replying lock, before X is called."""
    if not has_link(text):
        return
    cap = int(number_setting("X_LINK_POSTS_MAX_PER_DAY", 1, 0, 50))
    now = hand.now()
    day_start = now - (now % 86400.0)
    sent = sum(
        1
        for row in conn.execute("SELECT text FROM x_posts WHERE created_at >= ?", (day_start,)).fetchall()
        if has_link(row["text"])
    )
    if sent >= cap:
        raise hand.Refused(
            f"a post with a link costs $0.20 on X (without one, $0.015); {sent} of today's {cap} already went out. "
            "Nothing was published; post it without the link.",
            rule="links",
            sent_today=sent,
            cap=cap,
        )


# --- the gap between own posts -----------------------------------------------------------------


def check_gap(conn: sqlite3.Connection) -> None:
    """Whether an own post may go out now. Run under the posting lock, so the
    answer still holds when the post is recorded."""
    min_gap = number_setting("POST_MIN_GAP_HOURS", 3.0, 0.0, 48.0) * 3600.0
    tolerance = number_setting("POST_GAP_TOLERANCE_MINUTES", 20.0, 0.0, 120.0) * 60.0
    last = conn.execute(
        "SELECT id, created_at FROM x_posts WHERE kind IN ('post', 'quote') ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if last is None:
        return
    since = hand.now() - last["created_at"]
    if since + tolerance < min_gap:
        posted_at, next_at = iso(last["created_at"]), iso(last["created_at"] + min_gap)
        raise hand.Refused(
            f"cadence: too soon after post {last['id']} ({posted_at}); next slot at {next_at}. "
            "Nothing was published; do not post again before then.",
            rule="gap",
            last_post_id=last["id"],
            last_posted_at=posted_at,
            next_at=next_at,
        )


def check_repeat(conn: sqlite3.Connection, text: str) -> None:
    """Whether an own post says something Teeny has not already said
    (docs/roadmap.md, A2): refused when it is a near-duplicate
    (teeny_hands.guard.repeats) of an own post or quote of the last
    REPEAT_WINDOW_DAYS. The refusal is counted (`hands/guard repeats`)."""
    from ..guard import repeats

    since = hand.now() - repeats.window_days() * 86400.0
    earlier = conn.execute(
        "SELECT id, text, created_at FROM x_posts WHERE kind IN ('post', 'quote') AND created_at >= ?"
        " ORDER BY created_at DESC",
        (since,),
    ).fetchall()
    hit = repeats.closest(text, ((r["id"], r["text"]) for r in earlier))
    if hit is None:
        return
    post_id, score = hit
    when = next(iso(r["created_at"]) for r in earlier if r["id"] == post_id)
    repeats.record("x", score, post_id)
    raise hand.Refused(
        f"repeat: this says what post {post_id} ({when}) already said. Nothing was published; say something new.",
        rule="repeat",
        similar_to=post_id,
        similar_at=when,
        score=score,
    )


def record_post(
    conn: sqlite3.Connection,
    post_id: str,
    kind: str,
    text: str,
    in_reply_to: str | None = None,
    quote_of: str | None = None,
    media: Sequence[str] = (),
    created_at: float | None = None,
    format: str | None = None,
    experiment: int | None = None,
) -> None:
    """The own-post ledger. The hand writes it; `cadence` reads it. `format`
    and `experiment` are the lab tags (teeny_hands/tags.py)."""
    add_tag_columns(conn)
    conn.execute(
        "INSERT OR REPLACE INTO x_posts (id, kind, text, in_reply_to, quote_of, media, created_at, format, experiment)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (post_id, kind, text, in_reply_to, quote_of, json.dumps(list(media)),
         hand.now() if created_at is None else created_at, format, experiment),
    )


def record_like(conn: sqlite3.Connection, post_id: str) -> None:
    """The likes ledger: one row per post the account liked, the first time."""
    conn.execute("INSERT OR IGNORE INTO x_likes (post_id, liked_at) VALUES (?, ?)", (post_id, hand.now()))


# --- replies --------------------------------------------------------------------------------


def check_not_own(target: Mapping[str, Any], account_id: str) -> None:
    """Never answer the account's own post: a self-reply is the failure that
    compounds (reply/job.rs:534). Not knowing who wrote it fails closed."""
    author = target.get("authorId")
    if not author or not account_id:
        raise hand.Refused(
            f"could not tell who wrote post {target.get('id')}, so nothing was published", rule="self"
        )
    if author == account_id:
        raise hand.Refused("refused: the account wrote this post; never reply to it", rule="self")


def check_not_replied(conn: sqlite3.Connection, in_reply_to: str) -> None:
    """A post gets one reply. A row with a reply id is answered; a row
    without one is a reply whose fate is unknown (X timed out or failed
    mid-call, or the hand died), and is not risked twice."""
    row = conn.execute("SELECT reply_id FROM x_replies WHERE in_reply_to = ?", (in_reply_to,)).fetchone()
    if row is None:
        return
    if row["reply_id"]:
        raise hand.Refused(
            f"already replied to post {in_reply_to} (reply {row['reply_id']}); a post gets one reply",
            rule="replied",
            reply_id=row["reply_id"],
        )
    raise hand.Refused(
        f"a reply to post {in_reply_to} may already have gone out (X did not confirm it); not replying again",
        rule="replied",
    )


def claim_reply(conn: sqlite3.Connection, in_reply_to: str) -> None:
    """Claims the post's one reply, before X is called, so a crash between
    X answering and the record can never lead to a second reply."""
    conn.execute("INSERT INTO x_replies (in_reply_to, claimed_at) VALUES (?, ?)", (in_reply_to, hand.now()))


def release_reply(conn: sqlite3.Connection, in_reply_to: str) -> None:
    """X said no, so nothing went out and the post may be answered later."""
    conn.execute("DELETE FROM x_replies WHERE in_reply_to = ? AND reply_id IS NULL", (in_reply_to,))


def note_reply_in_doubt(conn: sqlite3.Connection, in_reply_to: str, note: str) -> None:
    conn.execute("UPDATE x_replies SET note = ? WHERE in_reply_to = ?", (note[:300], in_reply_to))


def record_reply_id(conn: sqlite3.Connection, in_reply_to: str, reply_id: str) -> None:
    """The reply is out: said first, before anything else can fail or stop."""
    conn.execute(
        "UPDATE x_replies SET reply_id = ?, replied_at = ? WHERE in_reply_to = ?",
        (reply_id, hand.now(), in_reply_to),
    )


# --- pacing ---------------------------------------------------------------------------------


def gap_ms(minimum: int, maximum: int, roll: float) -> int:
    """The gap between two published replies, random within
    `minimum..=maximum`: a fixed gap is its own signature. Never below the
    minimum, whatever the range says; a zero minimum turns pacing off."""
    if minimum == 0:
        return 0
    maximum = max(maximum, minimum)
    return round(minimum + roll * (maximum - minimum))


def check_pacing(conn: sqlite3.Connection) -> None:
    """Refuses a reply before the gap since the last one is up, with how long
    to wait. The caller waits, not the hand: a sleeping hand holds the
    replying lock and the job's slot."""
    row = conn.execute("SELECT next_at FROM x_pacing WHERE lane = 'reply'").fetchone()
    if row is None:
        return
    wait = row["next_at"] - hand.now()
    if wait > 0:
        raise hand.Refused(
            f"pacing: the next reply may go out in {wait:.1f}s; wait and try again",
            rule="pacing",
            wait_seconds=round(wait, 3),
            next_at=iso(row["next_at"]),
        )


def advance_pacing(conn: sqlite3.Connection, roll: float) -> None:
    """A reply went out now: the next one waits a fresh random gap, rolled
    here and kept, so every process sees the same one."""
    minimum = int(number_setting("REPLY_MIN_INTERVAL_MS", 30_000, 0, 3_600_000))
    maximum = int(number_setting("REPLY_MAX_INTERVAL_MS", 60_000, 0, 3_600_000))
    now = hand.now()
    conn.execute(
        "INSERT OR REPLACE INTO x_pacing (lane, last_at, next_at) VALUES ('reply', ?, ?)",
        (now, now + gap_ms(minimum, maximum, roll) / 1000.0),
    )
