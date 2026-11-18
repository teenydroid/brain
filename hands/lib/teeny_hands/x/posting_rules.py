"""The posting rules, each persisted in state/hands.db (docs/hands.md,
"Invariants every hand port must keep"). `publish` runs them after the
guard and before DRY_RUN; nothing else in the hand posts.

- The gap between own posts (crates/teeny-cadence/src/cadence.rs): an own
  post less than `POST_MIN_GAP_HOURS` after the last one, less
  `POST_GAP_TOLERANCE_MINUTES` (the jobs fire on the hour and then spend
  minutes drawing before they post), is refused. Five hours by default:
  brain/cron.toml has four post slots a day, each a ceiling that skips when it
  has nothing anchored to say, and the outward quote (`peer-suggest`) is one of
  the four. Posts an hour apart are the repetitive-publishing shape the ranker
  demotes. Replies are never subject to it: they are conversations, not slots.
- At most `X_POSTS_MAX_PER_DAY` own posts and quotes a UTC day (4 by default).
  Replies to mentions are not posts and are never counted.
- A thread (`hands/x thread`, 2 to 5 posts) is one own post: its head is
  recorded as kind `post` and counts against the gap and the cap once; every
  later post is recorded as kind `thread` (a continuation, `in_reply_to` the
  post before it, `thread_of` the head). A continuation is measured like any
  post of Teeny's, but is no own post and no reply: the gap, the cap, the
  reply pacing and the one-reply rule never see it, and the link budget counts
  a thread once however many of its posts carry a link.
- Every own post names its anchor (`check_anchor`): the one item from its
  inputs it is about, kept in the ledger beside it. A quote is anchored on
  the post it quotes unless it names another. Replies need none.
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
- A near-duplicate of anything Teeny said in the window is refused, replies
  included (`check_repeat`).
- Attachments by minted id or Giphy id only, never a URL, never both
  (crates/teeny-agent/src/tools/post.rs:393): the media this account posts is
  always something it can account for.
- A quote of somebody else's post lands in their community: it never names a
  ticker (a cashtag, `TINY` in capitals), a Solana mint or EVM contract
  address, or pump.fun (`check_outward`, rule `outward_ticker`), and no author
  is quoted twice within `PEER_ROTATION_DAYS` (`check_peer_rotation`, rule
  `peer_rotation`).
- A reply answers somebody who engaged first (`check_engaged_first`, rule
  `unsolicited`): a stored mention, a post that tags the account or replies
  to it, or a post in the conversation under one of its own posts. X itself
  allows API replies only there.

Times are seconds since the epoch (`hand.now()`); what a caller reads is ISO
8601, as the legacy ledger wrote it.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .. import hand, tags
from .client import number_setting
from .model import utf16_length

#: The hard cap, as X counts it for plain text.
MAX_POST_LENGTH = 280
#: Own posts a UTC day (X_POSTS_MAX_PER_DAY), and the hours between them
#: (POST_MIN_GAP_HOURS): four slots at most, five hours apart.
DEFAULT_POSTS_MAX_PER_DAY = 4
DEFAULT_POST_MIN_GAP_HOURS = 5.0
#: No second quote of one author inside this many days.
PEER_ROTATION_DAYS = 7
#: The longest anchor kept: an id, a URL or "pr 212", never a paragraph.
MAX_ANCHOR_LENGTH = 200
#: A thread is two to five posts: one is `hands/x post`, more is a wall.
THREAD_MIN_POSTS = 2
THREAD_MAX_POSTS = 5
#: Ledger columns added after x_posts was first created. `thread_of` is the
#: head's id on a thread continuation.
COLUMNS = {"anchor": "TEXT", "thread_of": "TEXT"}
#: The id of the account a quote quoted.
QUOTE_COLUMNS = {"quote_author": "TEXT"}
#: Who made an own post, and from what (`studio_tags`): added by the studio
#: cutover, NULL on every row from before it and from the old paths.
STUDIO_COLUMNS = {"producer": "TEXT", "storyline": "TEXT", "shape": "TEXT", "pitch_id": "TEXT"}
#: The producers a post may name. `studio` is bin/studio; `brief` the
#: told-to post (hands/brief post); `peer-quote` the outward quote
#: (`peer-suggest`); `legacy` (or no producer at all, NULL) every older path:
#: the replaced playbooks under STUDIO=0, the mind's own post_x, firsts,
#: votes, giveaways.
PRODUCERS = ("studio", "brief", "peer-quote", "legacy")
STUDIO_FIELDS = tuple(STUDIO_COLUMNS)
_STORYLINE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_PITCH_ID = re.compile(r"^[A-Za-z0-9:_.-]{1,120}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_posts (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('post', 'reply', 'quote', 'thread')),
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
    """`format` and `experiment` on x_posts (teeny_hands/tags.py), the
    post's `anchor`, the quoted author and a continuation's `thread_of`, added to a ledger
    created before they existed, and the kind `thread` its check allows."""
    hand.add_columns(conn, "x_posts", {**tags.COLUMNS, **COLUMNS, **QUOTE_COLUMNS})
    allow_thread_kind(conn)


def allow_thread_kind(conn: sqlite3.Connection) -> None:
    """A ledger made before threads checks `kind` against post, reply and
    quote, and SQLite cannot alter a check: the table is rebuilt once, rows
    and columns as they are, with `thread` allowed."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'x_posts'").fetchone()
    if row is None or "'thread'" in row[0] or "'quote')" not in row[0]:
        return
    rebuilt = re.sub(r"CREATE TABLE\s+(IF NOT EXISTS\s+)?\"?x_posts\"?", "CREATE TABLE x_posts_new", row[0], count=1)
    rebuilt = rebuilt.replace("'quote')", "'quote', 'thread')", 1)
    with contextlib.nullcontext() if conn.in_transaction else hand.transaction(conn):
        conn.execute("DROP TABLE IF EXISTS x_posts_new")
        conn.execute(rebuilt)
        conn.execute("INSERT INTO x_posts_new SELECT * FROM x_posts")
        conn.execute("DROP TABLE x_posts")
        conn.execute("ALTER TABLE x_posts_new RENAME TO x_posts")
        conn.execute("CREATE INDEX IF NOT EXISTS x_posts_kind_created ON x_posts (kind, created_at)")


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


# --- the anchor ------------------------------------------------------------------------------


def check_anchor(anchor: Any, quote_of: str | None) -> str:
    """The anchor an own post is about: the item from its playbook's inputs
    it names (a listen digest item id or URL, a material receipt id, a PR
    number, the post it quotes). A quote with none is anchored on the quoted
    post. A post with none, or a blank one, is refused before the guard is
    asked or anything is sent: a post about nothing in particular is the
    filler the slots are not for."""
    if anchor is None and quote_of:
        return quote_of
    if isinstance(anchor, int) and not isinstance(anchor, bool):
        anchor = str(anchor)
    if anchor is not None and not isinstance(anchor, str):
        raise hand.HandError('"anchor" must be a short string naming the item the post is about')
    anchor = (anchor or "").strip()
    if not anchor:
        raise hand.Refused(
            "no anchor: an own post must name the one item from its inputs it is about (a listen item, a receipt, "
            "a PR, the post it quotes) in \"anchor\". Nothing was published; skip when there is nothing to anchor on.",
            rule="anchor",
        )
    if len(anchor) > MAX_ANCHOR_LENGTH:
        raise hand.HandError(f'"anchor" is at most {MAX_ANCHOR_LENGTH} characters: an id, a URL or a PR number')
    return anchor


# --- threads ---------------------------------------------------------------------------------


def check_thread_size(count: int) -> None:
    """A thread is THREAD_MIN_POSTS to THREAD_MAX_POSTS posts; one is a post."""
    if not THREAD_MIN_POSTS <= count <= THREAD_MAX_POSTS:
        raise hand.Refused(
            f"a thread is {THREAD_MIN_POSTS} to {THREAD_MAX_POSTS} posts, not {count}"
            + ("; one post is hands/x post" if count == 1 else "") + ". Nothing was published.",
            rule="thread",
            posts=count,
            min=THREAD_MIN_POSTS,
            max=THREAD_MAX_POSTS,
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


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def link_budget(conn: sqlite3.Connection) -> dict[str, Any]:
    """Today's link posts (UTC day) against the cap: {cap, sent_today, left,
    resets_at}. What `check_link_budget` refuses on, and what `hands/x links`
    reads out, so a playbook can leave the link out before it is refused."""
    cap = int(number_setting("X_LINK_POSTS_MAX_PER_DAY", 1, 0, 50))
    now = hand.now()
    day_start = now - (now % 86400.0)
    # A thread is one link post however many of its posts carry a link: its
    # continuations count under their head.
    head = "COALESCE(thread_of, id)" if _has_column(conn, "x_posts", "thread_of") else "id"
    sent = len({
        row["head"]
        for row in conn.execute(f"SELECT {head} AS head, text FROM x_posts WHERE created_at >= ?", (day_start,)).fetchall()
        if has_link(row["text"])
    })
    return {"cap": cap, "sent_today": sent, "left": max(0, cap - sent), "resets_at": iso(day_start + 86400.0)}


def check_link_budget(conn: sqlite3.Connection, text: str) -> None:
    """A post or reply with a link, only while today's link posts are under
    the cap. Run under the posting or replying lock, before X is called."""
    if not has_link(text):
        return
    budget = link_budget(conn)
    cap, sent = budget["cap"], budget["sent_today"]
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
    min_gap = number_setting("POST_MIN_GAP_HOURS", DEFAULT_POST_MIN_GAP_HOURS, 0.0, 48.0) * 3600.0
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


def check_daily_cap(conn: sqlite3.Connection) -> None:
    """Own posts and quotes today (UTC), against `X_POSTS_MAX_PER_DAY`. Run
    under the posting lock with the gap. Replies are not counted."""
    cap = int(number_setting("X_POSTS_MAX_PER_DAY", DEFAULT_POSTS_MAX_PER_DAY, 0, 24))
    now = hand.now()
    day_start = now - (now % 86400.0)
    sent = conn.execute(
        "SELECT count(*) FROM x_posts WHERE kind IN ('post', 'quote') AND created_at >= ?", (day_start,)
    ).fetchone()[0]
    if sent >= cap:
        raise hand.Refused(
            f"{sent} own post(s) went out today (UTC) and the cap is {cap}. Nothing was published; "
            "the next slot is tomorrow.",
            rule="daily_cap",
            sent_today=int(sent),
            cap=cap,
        )


def check_repeat(conn: sqlite3.Connection, text: str) -> None:
    """Whether an own post says something Teeny has not already said
    (docs/roadmap.md, A2): refused when it is a near-duplicate
    (teeny_hands.guard.repeats) of an own post, quote or reply of the last
    REPEAT_WINDOW_DAYS. The refusal is counted (`hands/guard repeats`)."""
    from ..guard import repeats

    since = hand.now() - repeats.window_days() * 86400.0
    earlier = conn.execute(
        "SELECT id, text, created_at FROM x_posts WHERE created_at >= ? ORDER BY created_at DESC",
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


# --- quotes of somebody else's post ----------------------------------------------------------


#: What an outward quote never carries: a cashtag, `TINY` in capitals, a
#: Solana mint (base58, 32 to 44 characters), an EVM contract address, or
#: pump.fun. Teeny's own token is for its own posts and for whoever asks.
_OUTWARD = (
    ("a cashtag", re.compile(r"(?<![A-Za-z0-9_$])\$[A-Za-z][A-Za-z0-9_]{0,15}")),
    ("TINY", re.compile(r"(?<![A-Za-z0-9_])TINY(?![A-Za-z0-9_])")),
    ("a contract address", re.compile(r"(?<![A-Za-z0-9])0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")),
    ("a mint address", re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![1-9A-HJ-NP-Za-km-z])")),
    ("pump.fun", re.compile(r"pump\s*\.?\s*fun", re.IGNORECASE)),
)


def check_outward(text: str) -> None:
    """A quote of somebody else's post goes out to their community, so it
    names no ticker, address or launchpad: refused, with a rewrite asked
    for, whatever the guard allowed."""
    for what, pattern in _OUTWARD:
        if pattern.search(text):
            raise hand.Refused(
                f"a quote of somebody else's post carries no ticker, address or launchpad, and this one has {what}. "
                "Nothing was published; say it without.",
                rule="outward_ticker",
                found=what,
                rewrite=True,
            )


def check_peer_rotation(conn: sqlite3.Connection, author_id: str, author_of: Any) -> None:
    """No second quote of `author_id` inside PEER_ROTATION_DAYS. A quote
    recorded before the ledger kept its author is looked up with
    `author_of(post_id)` (the read cache: a quoted post was read before)."""
    now = hand.now()
    since = now - PEER_ROTATION_DAYS * 86400.0
    add_tag_columns(conn)
    rows = conn.execute(
        "SELECT id, quote_of, quote_author, created_at FROM x_posts WHERE kind = 'quote' AND created_at >= ?"
        " ORDER BY created_at DESC",
        (since,),
    ).fetchall()
    for row in rows:
        quoted = row["quote_author"]
        if quoted is None and row["quote_of"]:
            quoted = author_of(row["quote_of"])
        if quoted == author_id:
            posted_at, next_at = iso(row["created_at"]), iso(row["created_at"] + PEER_ROTATION_DAYS * 86400.0)
            raise hand.Refused(
                f"peer rotation: quote {row['id']} ({posted_at}) already quoted this author; the next quote of them "
                f"may go out at {next_at}. Nothing was published; quote somebody else.",
                rule="peer_rotation",
                last_quote_id=row["id"],
                last_quoted_at=posted_at,
                next_at=next_at,
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
    anchor: str | None = None,
    thread_of: str | None = None,
    quote_author: str | None = None,
) -> None:
    """The own-post ledger. The hand writes it; `cadence` reads it. `format`
    and `experiment` are the lab tags (teeny_hands/tags.py); `anchor` is what
    an own post is about (`check_anchor`), None on a reply. `thread_of` is the
    head's id on a thread continuation (kind `thread`), None elsewhere; `quote_author` is the id of the account a quote
    quoted (`check_peer_rotation`)."""
    add_tag_columns(conn)
    conn.execute(
        "INSERT OR REPLACE INTO x_posts"
        " (id, kind, text, in_reply_to, quote_of, media, created_at, format, experiment, anchor, thread_of, quote_author)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (post_id, kind, text, in_reply_to, quote_of, json.dumps(list(media)),
         hand.now() if created_at is None else created_at, format, experiment, anchor, thread_of, quote_author),
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


def check_engaged_first(
    conn: sqlite3.Connection, target: Mapping[str, Any], account: Mapping[str, Any], author_of: Any
) -> None:
    """A reply answers somebody who engaged first: the target is a stored
    mention (`x_mentions`), tags the account, replies to it, or sits in the
    conversation under one of its own posts (the ledger's, else the root's
    author, looked up with `author_of(post_id)`). Anything else is
    unsolicited, which X refuses from the API anyway."""
    post_id = str(target.get("id") or "")
    if conn.execute("SELECT 1 FROM x_mentions WHERE id = ?", (post_id,)).fetchone():
        return
    if account.get("id") and target.get("inReplyToUserId") == account["id"]:
        return
    username = account.get("username")
    if username and re.search(rf"(?<![A-Za-z0-9_])@{re.escape(username)}(?![A-Za-z0-9_])", str(target.get("text") or ""),
                              re.IGNORECASE):
        return
    root = target.get("conversationId")
    if root and root != post_id:
        if conn.execute("SELECT 1 FROM x_posts WHERE id = ? AND kind IN ('post', 'quote')", (root,)).fetchone():
            return
        if account.get("id") and author_of(root) == account["id"]:
            return
    raise hand.Refused(
        f"post {post_id} never engaged the account (not a mention, not a reply to it, not under one of its posts); "
        "a reply answers only somebody who spoke first. Nothing was published.",
        rule="unsolicited",
    )


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
