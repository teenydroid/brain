"""The X peer loop (docs/hands.md, "x peers"): Teeny's quotes into the
communities around other crypto-AI projects, kept rare and specific.

  peers           R  the newest original posts of the accounts in
                     brain/x-peers.txt, through the search cache, screened
  peer-suggest    A  up to 3 scored quote suggestions; all kept, the top one
                     posted only when it clears every bar (one word, as a
                     playbook's `hand:x <verb>` output names it)
  peers suggestions  R  what was suggested, posted and skipped, and why
  peers report    R  per peer: touches, who then spoke to Teeny, how the
                     quote did

The peers file is brain-owned (Teeny edits it), one handle a line, `#`
comments, read by the same parser as brain/moltbook-peers.txt
(teeny_hands/peerfile.py).

Tables, in state/hands.db:

  x_peer_reads       the posts the last `peers` read returned: a suggestion
                     may quote only one of them
  x_peer_candidates  every suggestion, with what became of it
  x_peer_touches     every quote of a peer that went out (`publish` writes
                     it, whichever playbook quoted)

Under DRY_RUN `peer-suggest` runs every bar and every posting rule and writes no
row, as `publish` writes no ledger row.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .. import hand, peerfile
from . import cache, mentions, open_db
from . import posting_rules as rules
from .client import XApi, XError, number_setting
from .model import Json, SearchOptions

PEERS_FILE = "brain/x-peers.txt"
#: At most this many handles are read a call.
PEERS_MAX = 20
#: X's recent search takes a query of at most 512 characters on the
#: self-serve plans; each search stays under it.
QUERY_MAX_CHARS = 512
QUERY_SUFFIX = " -is:retweet -is:reply"
#: A peer quoted in the last this many days is left out of `--skip-quoted`.
QUOTED_DAYS = rules.PEER_ROTATION_DAYS
#: One visit (the drawing of Teeny dropping in) a week at most.
VISIT_DAYS = 7
#: How long after a touch a mention counts as its answer.
ANSWER_HOURS = 48
MAX_CANDIDATES = 3
FORMATS = ("quote", "visit")
CANDIDATE_FIELDS = {"quote_of", "text", "relevance", "why", "source", "format", "image"}

_HANDLE = re.compile(r"^@?([A-Za-z0-9_]{1,15})$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_peer_reads (
    post_id         TEXT PRIMARY KEY,
    author_username TEXT,
    read_at         REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS x_peer_candidates (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    batch     TEXT NOT NULL,
    rank      INTEGER NOT NULL,
    quote_of  TEXT NOT NULL,
    author    TEXT,
    text      TEXT NOT NULL,
    relevance INTEGER NOT NULL,
    why       TEXT,
    source    TEXT,
    format    TEXT NOT NULL,
    status    TEXT NOT NULL CHECK (status IN ('kept', 'skipped', 'posting', 'posted', 'refused', 'failed')),
    rule      TEXT,
    reason    TEXT,
    post_id   TEXT,
    at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS x_peer_candidates_at ON x_peer_candidates (at);
CREATE TABLE IF NOT EXISTS x_peer_touches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    handle        TEXT NOT NULL,
    their_post_id TEXT NOT NULL,
    our_post_id   TEXT NOT NULL,
    format        TEXT,
    at            REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS x_peer_touches_handle ON x_peer_touches (handle, at);
"""


def _db() -> sqlite3.Connection:
    conn = open_db(rules.SCHEMA + SCHEMA)
    rules.add_tag_columns(conn)
    return conn


# --- the peers file ---------------------------------------------------------------------------


def handle(text: str) -> str:
    """An X handle, without its @; anything else is not one."""
    match = _HANDLE.match(text.strip())
    if not match:
        raise hand.HandError(f"not an X handle: {text[:60]!r}")
    return match.group(1)


def handles() -> tuple[list[str], list[str]]:
    """The peers file's handles, in order and once each, and the lines that
    are not handles."""
    return peerfile.names(PEERS_FILE, handle)


def queries(names: Sequence[str]) -> list[tuple[list[str], str]]:
    """`(from:a OR from:b ...) -is:retweet -is:reply`, as few searches as
    keep each under QUERY_MAX_CHARS: (the handles, the query) each."""
    chunks: list[tuple[list[str], str]] = []
    current: list[str] = []

    def query(names: list[str]) -> str:
        return "(" + " OR ".join(f"from:{n}" for n in names) + ")" + QUERY_SUFFIX

    for name in names:
        if current and len(query([*current, name])) > QUERY_MAX_CHARS:
            chunks.append((current, query(current)))
            current = []
        current.append(name)
    if current:
        chunks.append((current, query(current)))
    return chunks


# --- the read ---------------------------------------------------------------------------------


def _search_time(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _created(post: Mapping[str, Any]) -> float | None:
    try:
        return datetime.fromisoformat(str(post.get("createdAt")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _quoted_recently(conn: sqlite3.Connection, names: Sequence[str]) -> tuple[set[str], set[str]]:
    """Who was quoted in the last QUOTED_DAYS: author ids from the own-post
    ledger, handles (lowercase) from the touches."""
    since = hand.now() - QUOTED_DAYS * 86400.0
    ids = {r[0] for r in conn.execute(
        "SELECT quote_author FROM x_posts WHERE kind = 'quote' AND quote_author IS NOT NULL AND created_at >= ?", (since,))}
    touched = {r[0].lower() for r in conn.execute("SELECT handle FROM x_peer_touches WHERE at >= ?", (since,))}
    return ids, touched


def read(max_age_hours: float = 8.0, per_peer: int = 2, skip_quoted: bool = False, x: XApi | None = None) -> Json:
    """Each peer's newest `per_peer` original posts of the last
    `max_age_hours`, newest first. One search per chunk of handles, through
    the search cache, from `start_time` on, so X bills only recent posts.
    A post by anyone not on the list is dropped; each kept post is screened
    by the inbound guard (as a mention is), and a held one never reaches a
    drafting model. What it returns is kept as the last read."""
    names, skipped = handles()
    out: Json = {"peers": names[:PEERS_MAX]}
    if len(names) > PEERS_MAX:
        out["not_read"] = names[PEERS_MAX:]
    if skipped:
        out["skipped"] = skipped
    names = names[:PEERS_MAX]
    conn = _db()
    if not names:
        _remember_read(conn, [])
        out["posts"] = []
        return out
    now = hand.now()
    since = now - max_age_hours * 3600.0
    reads = cache.reads(x)
    found: list[Json] = []
    errors: list[Json] = []
    for chunk, query in queries(names):
        options = SearchOptions(
            max_results=min(100, max(10, per_peer * len(chunk))),
            start_time=_search_time(since),
            sort_order="recency",
        )
        try:
            found += reads.search_posts(query, options)
        except XError as error:
            errors.append({"peers": chunk, "error": str(error)})
            if error.status == 429:
                break
    wanted = {n.lower(): n for n in names}
    quoted_ids, quoted_handles = _quoted_recently(conn, names) if skip_quoted else (set(), set())
    left_out: list[str] = []
    per: dict[str, int] = {}
    kept: list[Json] = []
    seen: set[str] = set()
    for post in sorted(found, key=lambda p: int(p["id"]), reverse=True):
        author = str(post.get("authorUsername") or "").lower()
        if author not in wanted or post["id"] in seen:
            continue
        seen.add(post["id"])
        created = _created(post)
        if created is not None and created < since:
            continue
        if skip_quoted and (post.get("authorId") in quoted_ids or author in quoted_handles):
            if wanted[author] not in left_out:
                left_out.append(wanted[author])
            continue
        if per.get(author, 0) >= per_peer:
            continue
        per[author] = per.get(author, 0) + 1
        kept.append(post)
    held = 0
    screened: list[Json] = []
    for post in kept:
        if mentions._screen(post).kind == "allow":
            screened.append(post)
        else:
            held += 1
    _remember_read(conn, screened)
    out["posts"] = screened
    if held:
        out["held"] = held
    if left_out:
        out["quoted_recently"] = sorted(left_out, key=str.lower)
    if errors:
        out["errors"] = errors
    return out


def _remember_read(conn: sqlite3.Connection, posts: Sequence[Mapping[str, Any]]) -> None:
    """The last read replaces the one before: `suggest` quotes only from it."""
    now = hand.now()
    with hand.transaction(conn):
        conn.execute("DELETE FROM x_peer_reads")
        conn.executemany(
            "INSERT OR REPLACE INTO x_peer_reads (post_id, author_username, read_at) VALUES (?, ?, ?)",
            [(p["id"], p.get("authorUsername"), now) for p in posts],
        )


# --- suggest ----------------------------------------------------------------------------------


def _relevance(value: Any) -> int:
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        raise hand.HandError('"relevance" is a whole number from 1 to 5')
    return value


def _candidate(raw: Any) -> Json:
    if not isinstance(raw, Mapping):
        raise hand.HandError("each candidate is an object")
    unknown = sorted(set(raw) - CANDIDATE_FIELDS)
    if unknown:
        raise hand.HandError(f"unknown field(s) in a candidate: {', '.join(unknown)}")
    quote_of = raw.get("quote_of")
    if isinstance(quote_of, int) and not isinstance(quote_of, bool):
        quote_of = str(quote_of)
    if not rules.is_x_id(quote_of):
        raise hand.HandError('"quote_of" must be a numeric X post id from the last peers read')
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        raise hand.HandError('"text" must be a non-empty string')
    fmt = raw.get("format", "quote")
    if fmt not in FORMATS:
        raise hand.HandError(f'"format" is one of {", ".join(FORMATS)}')
    for field in ("why", "source"):
        if raw.get(field) is not None and not isinstance(raw.get(field), str):
            raise hand.HandError(f'"{field}" must be a string')
    return {
        "quote_of": quote_of,
        "text": text,
        "relevance": _relevance(raw.get("relevance")),
        "why": (raw.get("why") or "").strip()[:500],
        "source": (raw.get("source") or "").strip()[:500],
        "format": fmt,
    }


def _body(body: Any) -> tuple[list[Json], list[str]]:
    if not isinstance(body, Mapping):
        raise hand.HandError('stdin must be a JSON object with "candidates"')
    unknown = sorted(set(body) - {"candidates", "media_ids"})
    if unknown:
        raise hand.HandError(f"unknown field(s) in the body: {', '.join(unknown)}")
    raw = body.get("candidates")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_CANDIDATES:
        raise hand.HandError(f'"candidates" is a list of 1 to {MAX_CANDIDATES} suggestions')
    media_ids = body.get("media_ids") or []
    if not isinstance(media_ids, list) or not all(isinstance(m, str) and m for m in media_ids):
        raise hand.HandError('"media_ids" must be a list of media ids')
    return [_candidate(c) for c in raw], list(media_ids)


def _day_start(now: float) -> float:
    return now - (now % 86400.0)


def _bar(conn: sqlite3.Connection, top: Json) -> tuple[str, str] | None:
    """Why the top suggestion is not posted, or None when it clears every
    bar the posting rules do not already hold."""
    floor = int(number_setting("PEER_QUOTE_MIN_RELEVANCE", 5, 1, 5))
    if top["relevance"] < floor:
        return "relevance", f"the best suggestion scored {top['relevance']} and only a {floor} is posted; kept, not posted"
    if not top["source"]:
        return "source", "the best suggestion names no first-hand source (a ledger row, a build, a memory); kept, not posted"
    if not conn.execute("SELECT 1 FROM x_peer_reads WHERE post_id = ?", (top["quote_of"],)).fetchone():
        return "not_read", f"post {top['quote_of']} was not in the last peers read; only a post it returned is quoted"
    now = hand.now()
    cap = int(number_setting("PEER_QUOTES_MAX_PER_DAY", 1, 0, 4))
    sent = conn.execute("SELECT count(*) FROM x_peer_candidates WHERE status = 'posted' AND at >= ?",
                        (_day_start(now),)).fetchone()[0]
    if sent >= cap:
        return "daily", f"{sent} peer quote(s) went out today (UTC) and the cap is {cap}; the next is tomorrow"
    if top["format"] == "visit":
        last = conn.execute("SELECT max(at) FROM x_peer_candidates WHERE status = 'posted' AND format = 'visit'").fetchone()[0]
        if last is not None and last > now - VISIT_DAYS * 86400.0:
            return "visit", f"a visit went out {rules.iso(last)}; one a week, the next from {rules.iso(last + VISIT_DAYS * 86400.0)}"
    return None


def _author(conn: sqlite3.Connection, post_id: str) -> str | None:
    row = conn.execute("SELECT author_username FROM x_peer_reads WHERE post_id = ?", (post_id,)).fetchone()
    return row[0] if row else None


def suggest(body: Any, x: XApi | None = None) -> Json:
    """Keeps every suggestion and posts the top one (the highest relevance,
    the first on a tie) through `publish.post` only when it clears the bar
    (`_bar`) and every posting rule. A skip is not a failure: it is the
    usual outcome."""
    from . import publish

    candidates, media_ids = _body(body)
    top_rank = max(range(len(candidates)), key=lambda i: (candidates[i]["relevance"], -i))
    top = candidates[top_rank]
    live = not hand.dry_run()
    conn = _db()
    with hand.lock("x-peer-suggest"):
        bar = _bar(conn, top)
        status = "skipped" if bar else ("posting" if live else "kept")
        batch = secrets.token_hex(6)
        now = hand.now()
        ids: list[int] = []
        if live:
            with hand.transaction(conn):
                for rank, c in enumerate(candidates):
                    ids.append(conn.execute(
                        "INSERT INTO x_peer_candidates (batch, rank, quote_of, author, text, relevance, why, source, format,"
                        " status, rule, reason, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (batch, rank, c["quote_of"], _author(conn, c["quote_of"]), c["text"], c["relevance"], c["why"],
                         c["source"], c["format"], status if rank == top_rank else "kept",
                         bar[0] if bar and rank == top_rank else None, bar[1] if bar and rank == top_rank else None, now),
                    ).lastrowid)
        out: Json = {"stored": len(ids), "top": top_rank}
        if not live:
            out["dry_run"] = True
        if bar:
            return {**out, "posted": None, "skipped": {"rule": bar[0], "reason": bar[1], "quote_of": top["quote_of"]}}
        draft: Json = {"text": top["text"], "quote_of": top["quote_of"], "format": f"peer-{top['format']}"}
        if media_ids:
            draft["media_ids"] = media_ids
        try:
            posted = publish.post(draft, x)
        except hand.Refused as refused:
            if live:
                _settle(conn, ids[top_rank], "refused", refused.detail.get("rule") or "refused", refused.reason)
            raise
        except Exception as exc:
            if live:
                _settle(conn, ids[top_rank], "failed", "error", f"{type(exc).__name__}: {exc}"[:300])
            raise
        if not live:
            return {**out, "posted": None, "would_post": posted.get("would_post"), "quote_of": top["quote_of"]}
        _settle(conn, ids[top_rank], "posted", None, None, posted.get("id"))
        return {**out, "posted": posted}


def _settle(conn: sqlite3.Connection, row_id: int, status: str, rule: str | None, reason: str | None,
            post_id: str | None = None) -> None:
    with hand.transaction(conn):
        conn.execute("UPDATE x_peer_candidates SET status = ?, rule = ?, reason = ?, post_id = ? WHERE id = ?",
                     (status, rule, (reason or "")[:300] or None, post_id, row_id))


# --- touches ----------------------------------------------------------------------------------


def record_touch(quoted: Mapping[str, Any], our_post_id: str, fmt: str | None) -> bool:
    """A quote that went out, of a post by a peer on the list: one
    x_peer_touches row. False when the author is not a peer."""
    author = str(quoted.get("authorUsername") or "")
    names = {n.lower(): n for n in handles()[0]}
    if author.lower() not in names:
        return False
    conn = _db()
    with hand.transaction(conn):
        conn.execute(
            "INSERT INTO x_peer_touches (handle, their_post_id, our_post_id, format, at) VALUES (?, ?, ?, ?, ?)",
            (author.lower(), quoted["id"], our_post_id, fmt, hand.now()),
        )
    return True


# --- the reads of what came of it ---------------------------------------------------------------


def suggestions(days: int = 7) -> Json:
    """Every suggestion of the last `days`, newest first, with what became
    of it, and the counts the playbook and the operator read."""
    conn = _db()
    since = hand.now() - days * 86400.0
    rows = conn.execute(
        "SELECT * FROM x_peer_candidates WHERE at >= ? ORDER BY at DESC, batch, rank", (since,)
    ).fetchall()
    listed = []
    for r in rows:
        item = {k: r[k] for k in ("quote_of", "author", "relevance", "format", "status", "rule", "reason", "post_id",
                                  "text", "why", "source") if r[k] not in (None, "")}
        item["at"] = rules.iso(r["at"])
        listed.append(item)
    tops = [r for r in rows if r["status"] != "kept"]
    last_visit = conn.execute("SELECT max(at) FROM x_peer_candidates WHERE status = 'posted' AND format = 'visit'").fetchone()[0]
    return {
        "suggestions": listed,
        "runs": len({r["batch"] for r in rows}),
        "posted": sum(1 for r in tops if r["status"] == "posted"),
        "skipped": sum(1 for r in tops if r["status"] == "skipped"),
        "last_visit_at": rules.iso(last_visit) if last_visit is not None else None,
    }


def _latest_metrics(conn: sqlite3.Connection, post_id: str) -> Json | None:
    try:
        row = conn.execute(
            "SELECT checkpoint, likes, replies, reposts, quotes, impressions, bookmarks FROM x_post_metrics"
            " WHERE post_id = ? ORDER BY checkpoint DESC LIMIT 1", (post_id,)
        ).fetchone()
    except sqlite3.OperationalError:  # no metrics ledger yet
        return None
    return {k: row[k] for k in row.keys() if row[k] is not None} if row else None


def report(days: int = 14) -> Json:
    """Per peer (the file's, in order, then any touched since), the touches
    of the last `days`: whether the peer then spoke to Teeny (a mention
    within ANSWER_HOURS), which accounts mentioned Teeny for the first time
    in that window, and the latest metrics of the quote."""
    conn = open_db(rules.SCHEMA + SCHEMA + mentions.SCHEMA)
    now = hand.now()
    since = now - days * 86400.0
    touches = conn.execute("SELECT * FROM x_peer_touches WHERE at >= ? ORDER BY at DESC", (since,)).fetchall()
    order = [n.lower() for n in handles()[0]]
    for t in touches:
        if t["handle"] not in order:
            order.append(t["handle"])
    out = []
    for name in order:
        recent = []
        for t in (t for t in touches if t["handle"] == name):
            until = t["at"] + ANSWER_HOURS * 3600.0
            authors = [r[0] for r in conn.execute(
                "SELECT DISTINCT lower(author_username) FROM x_mentions WHERE seen_at >= ? AND seen_at < ?"
                " AND author_username IS NOT NULL", (t["at"], until))]
            new = sorted(a for a in authors if a != name and not conn.execute(
                "SELECT 1 FROM x_mentions WHERE lower(author_username) = ? AND seen_at < ?", (a, t["at"])).fetchone())
            item: Json = {
                "at": rules.iso(t["at"]),
                "their_post_id": t["their_post_id"],
                "our_post_id": t["our_post_id"],
                "format": t["format"],
                "peer_answered": name in authors,
                "new_accounts": new,
            }
            measured = _latest_metrics(conn, t["our_post_id"])
            if measured:
                item["metrics"] = measured
            recent.append(item)
        out.append({
            "handle": name,
            "touches": len(recent),
            "answered": sum(1 for t in recent if t["peer_answered"]),
            "new_accounts": sum(len(t["new_accounts"]) for t in recent),
            "last_touch_at": recent[0]["at"] if recent else None,
            "recent": recent,
        })
    return {"days": days, "peers": out}
