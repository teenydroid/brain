"""The weekly advisory vote on what Teeny builds next (docs/roadmap.md, P5;
docs/hands.md, "requests").

Teeny posts 2 to 4 pending build requests as numbered options
(`vote-open`, through `hands/x post`), and people answer by replying with a
number. The count is code, never a model:

- a vote is a reply in the vote post's conversation (`x_mentions`, read by
  `hands/x ingest`) whose text, after any leading @handles, starts with an
  option's number ("2", "#2", "vote 2");
- one account, one vote: an account's last vote before the close counts;
- replies the inbound guard held, the account's own posts, and anything seen
  after the close are not counted;
- the most votes wins; a tie, or no votes, has no winner.

It is advisory and narrow: the winner becomes the next request `pick` takes,
and that is all. Every rule `pick` keeps still holds (a request the guard
held is skipped, one at a time, and the pick-request playbook's refusals),
and nothing here touches the wallet, a fence, or anything but which request
is built next. A winner that is no longer pending (skipped for a reason, or
built) binds nothing.

  votes(id, post_id, options, opened_at, closes_at, status, tally, winner_id, note)
  status: open -> closed (the count is final) -> honoured (the winner was picked)
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Mapping

from . import hand, xpost

Json = dict[str, Any]

SCHEMA = """
CREATE TABLE IF NOT EXISTS votes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id   TEXT NOT NULL,
    options   TEXT NOT NULL,
    opened_at REAL NOT NULL,
    closes_at REAL NOT NULL,
    status    TEXT NOT NULL,
    tally     TEXT,
    winner_id INTEGER,
    note      TEXT
);
"""

DAY = 86400.0
#: How long a vote stays open, and the least time between two votes.
OPEN_DAYS = 7
MIN_GAP_DAYS = 6
MIN_OPTIONS, MAX_OPTIONS = 2, 4
TEXT_MAX = 280
#: A reply's vote: after any leading @handles, a number first.
_VOTE = re.compile(r"^(?:@\w+[\s,:]*)*(?:vote\s*[:#]?\s*)?#?([1-9])(?!\d)", re.IGNORECASE)


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def _has(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def choice(text: str, options: int) -> int | None:
    """The option a reply votes for (1-based), or None."""
    found = _VOTE.match(text.strip())
    if not found:
        return None
    n = int(found.group(1))
    return n if 1 <= n <= options else None


def count(conn: sqlite3.Connection, vote: Mapping[str, Any]) -> Json:
    """The tally of one vote, from the replies stored so far."""
    options = json.loads(vote["options"])
    me = (hand.setting("X_BOT_USERNAME", "teenydroid") or "teenydroid").lstrip("@").lower()
    ballots: dict[str, tuple[float, int]] = {}
    if _has(conn, "x_mentions"):
        rows = conn.execute(
            "SELECT author_id, author_username, text, seen_at, status FROM x_mentions"
            " WHERE conversation_id = ? AND id != ? AND seen_at >= ? AND seen_at <= ? ORDER BY seen_at",
            (vote["post_id"], vote["post_id"], vote["opened_at"], vote["closes_at"]),
        ).fetchall()
        for r in rows:
            if r["status"] in ("held", "skipped"):
                continue
            who = str(r["author_id"] or "").strip() or str(r["author_username"] or "").strip().lower()
            if not who or str(r["author_username"] or "").lower() == me:
                continue
            picked = choice(str(r["text"]), len(options))
            if picked is not None:
                ballots[who] = (r["seen_at"], picked)  # the last one counts
    votes = [0] * len(options)
    for _, picked in ballots.values():
        votes[picked - 1] += 1
    tally = [{"option": i + 1, "request": rid, "votes": votes[i]} for i, rid in enumerate(options)]
    top = max(votes) if votes else 0
    leaders = [t for t in tally if t["votes"] == top]
    winner = leaders[0]["request"] if top > 0 and len(leaders) == 1 else None
    return {"accounts": len(ballots), "tally": tally, "winner": winner,
            "why": None if winner else ("no votes" if top == 0 else "a tie")}


def settle(conn: sqlite3.Connection) -> None:
    """Closes every open vote past its close, with its final count."""
    for vote in conn.execute("SELECT * FROM votes WHERE status = 'open' AND closes_at <= ?", (hand.now(),)).fetchall():
        got = count(conn, vote)
        with hand.transaction(conn):
            conn.execute("UPDATE votes SET status = 'closed', tally = ?, winner_id = ?, note = ? WHERE id = ?",
                         (json.dumps(got), got["winner"], got["why"], vote["id"]))


def waiting_winner(conn: sqlite3.Connection) -> Json | None:
    """The winner `pick` must take next: the latest closed vote's, while that
    request is still pending."""
    settle(conn)
    row = conn.execute("SELECT * FROM votes WHERE status = 'closed' AND winner_id IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    req = conn.execute("SELECT status FROM build_requests WHERE id = ?", (row["winner_id"],)).fetchone()
    if req is None or req["status"] != "pending":
        return None
    return {"vote": row["id"], "request": row["winner_id"]}


def honour(conn: sqlite3.Connection, request_id: int) -> None:
    conn.execute("UPDATE votes SET status = 'honoured' WHERE status = 'closed' AND winner_id = ?", (request_id,))


def _view(conn: sqlite3.Connection, row: Mapping[str, Any]) -> Json:
    out: Json = {"vote": row["id"], "post_id": row["post_id"], "status": row["status"], "options": json.loads(row["options"]),
                 "opened_at": row["opened_at"], "closes_at": row["closes_at"]}
    if row["status"] == "open":
        out["so_far"] = count(conn, row)
    else:
        out["result"] = json.loads(row["tally"] or "{}")
    return out


def current() -> Json:
    """The latest vote: open with the count so far, or its result; and the
    winner pick must take next, if one waits."""
    conn = db()
    try:
        winner = waiting_winner(conn)
        row = conn.execute("SELECT * FROM votes ORDER BY id DESC LIMIT 1").fetchone()
        out: Json = {"vote": _view(conn, row) if row else None}
        if winner:
            out["next_pick"] = winner["request"]
        return out
    finally:
        conn.close()


def open_vote(body: Any) -> Json:
    """{text, options: [request ids]}: posts the vote through hands/x and
    opens it for OPEN_DAYS. Refused while a vote is open or one opened in the
    last MIN_GAP_DAYS, for options that are not 2-4 distinct pending
    requests, for a text that misses an option's number, and for any
    @mention (no unsolicited mentions). Opened only when the post went out."""
    if not isinstance(body, Mapping) or set(body) - {"text", "options"}:
        raise hand.HandError('vote-open takes {"text": ..., "options": [request ids]}')
    text, options = body.get("text"), body.get("options")
    if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX:
        raise hand.HandError(f"the vote needs a text of 1 to {TEXT_MAX} characters")
    if (not isinstance(options, list) or not MIN_OPTIONS <= len(options) <= MAX_OPTIONS
            or not all(isinstance(o, int) and not isinstance(o, bool) for o in options) or len(set(options)) != len(options)):
        raise hand.HandError(f"options are {MIN_OPTIONS} to {MAX_OPTIONS} distinct request ids")
    if "@" in text:
        raise hand.Refused("a vote post mentions no one", rule="mention")
    missing = [str(i) for i in range(1, len(options) + 1) if not re.search(rf"(?<!\d){i}(?!\d)", text)]
    if missing:
        raise hand.Refused(f"the text must number every option; it misses {', '.join(missing)}", rule="numbers")
    conn = db()
    try:
        settle(conn)
        busy = conn.execute("SELECT id FROM votes WHERE status = 'open'").fetchone()
        if busy:
            raise hand.Refused(f"vote {busy['id']} is still open", rule="open")
        last = conn.execute("SELECT max(opened_at) AS at FROM votes").fetchone()["at"]
        if last is not None and hand.now() - last < MIN_GAP_DAYS * DAY:
            raise hand.Refused("one vote a week", rule="weekly")
        for rid in options:
            row = conn.execute("SELECT status FROM build_requests WHERE id = ?", (rid,)).fetchone() if _has(conn, "build_requests") else None
            if row is None or row["status"] != "pending":
                raise hand.Refused(f"request {rid} is not a pending request", rule="option")
    finally:
        conn.close()
    posted = xpost.post(text)
    if posted.get("dry_run") or not posted.get("id"):
        return {"opened": False, "post": posted}
    now = hand.now()
    conn = db()
    try:
        with hand.transaction(conn):
            cur = conn.execute("INSERT INTO votes (post_id, options, opened_at, closes_at, status) VALUES (?, ?, ?, ?, 'open')",
                               (str(posted["id"]), json.dumps(options), now, now + OPEN_DAYS * DAY))
    finally:
        conn.close()
    return {"opened": True, "vote": cur.lastrowid, "post_id": str(posted["id"]), "closes_at": now + OPEN_DAYS * DAY}
