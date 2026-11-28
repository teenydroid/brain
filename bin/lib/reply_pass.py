"""The inbox pass of the mind's 15-minute beat (docs/mind.md, "The beat").

`bin/reply-queue` prints a short block that teeny-guardrails puts in front of
each mind turn (core/plugins/teeny-guardrails/wake.ts), so the beat's first
step does not spend tool calls listing what waits: how many X mentions are
`offered`, the few most worth answering (id, author, one line), and the mail
waiting. It is read-only apart from one tidy-up: a waiting item the mind has
already answered or marked is closed in the stimuli inbox, so the tick never
sees it either (a mention answered through `hands/x reply` is marked `replied`). The caps (3 X replies, 2 mail replies a beat) are not here:
teeny-guardrails counts them in code, per mind run.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hands" / "lib"))

from teeny_hands import hand, stimuli  # noqa: E402

#: The block never runs past this many characters (the plugin cuts it to the
#: same, so a bug here cannot grow a prompt).
MAX_CHARS = 1500
#: How many candidates each channel shows, and how long one excerpt runs.
X_SHOWN = 5
MAIL_SHOWN = 2
EXCERPT = 90
#: How many offered mentions are counted; the count says "100+" at this.
X_SCAN = 100
#: How much waiting mail is counted.
MAIL_SCAN = 200


def _line(text: str, limit: int = EXCERPT) -> str:
    one = re.sub(r"\s+", " ", text or "").strip()
    return one if len(one) <= limit else one[: limit - 1] + "…"


def worth(mention: Mapping[str, Any]) -> int:
    """A rough rank, highest first: a question, a real sentence, a name that
    is not a bare link. Ties go to the newest (the list is newest first)."""
    text = str(mention.get("text") or "")
    body = re.sub(r"@\w+|https?://\S+", "", text).strip()
    return (2 if "?" in body else 0) + (1 if len(body) >= 40 else 0) - (2 if len(body) < 8 else 0)


def candidates(mentions: list[Mapping[str, Any]], shown: int = X_SHOWN) -> list[Mapping[str, Any]]:
    ranked = sorted(enumerate(mentions), key=lambda p: (-worth(p[1]), p[0]))
    return [m for _, m in ranked[:shown]]


def render(x_total: int, x_more: bool, x_top: list[Mapping[str, Any]], mail_total: int, mail_top: list[Mapping[str, Any]],
           limit: int = MAX_CHARS) -> str:
    """The block, cut to `limit` characters at a line end."""
    if not x_total and not mail_total:
        return ""
    lines = ["[inbox pass] Your beat opens here: at most 3 X replies and 2 mail replies, then your own work."]
    if x_total:
        lines.append(f"X mentions waiting: {x_total}{'+' if x_more else ''} (hands/x mentions --status offered). Best first:")
        lines += [f"- x {m['id']} @{m.get('author_username') or '?'}: {_line(str(m.get('text') or ''))}" for m in x_top]
    if mail_total:
        lines.append(f"Mail waiting: {mail_total} (reply with hands/mail reply; close one not worth an answer with hands/stimuli close).")
        lines += [f"- mail {m['ref']} {m.get('who') or '?'}: {_line(str(m.get('text') or ''))}" for m in mail_top]
    out: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > limit:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


ANSWERED = "EXISTS (SELECT 1 FROM x_replies r WHERE r.in_reply_to = m.id AND r.reply_id IS NOT NULL)"


def _reconcile_x(conn: Any) -> None:
    """A mention answered or marked since it was queued is done for the tick too.
    `hands/x reply` records the answer in `x_replies` and leaves the mention
    `offered`, so a reply in that ledger counts as answered here: the mention
    is marked `replied` and stops being offered."""
    rows = conn.execute(f"SELECT m.id FROM x_mentions m WHERE m.status = 'offered' AND {ANSWERED}").fetchall()
    for row in rows:
        conn.execute("UPDATE x_mentions SET status = 'replied', note = 'answered', updated_at = ? WHERE id = ? AND status = 'offered'",
                     (hand.now(), row["id"]))
    rows = conn.execute("SELECT ref FROM stimuli WHERE status = 'pending' AND surface = 'x' AND kind = 'mention'").fetchall()
    for row in rows:
        got = conn.execute("SELECT status FROM x_mentions WHERE id = ?", (row["ref"],)).fetchone()
        if got is not None and got["status"] != "offered":
            stimuli.close("x", row["ref"])


def _reconcile_mail(conn: Any) -> None:
    rows = conn.execute("SELECT ref FROM stimuli WHERE status = 'pending' AND surface = 'mail' AND kind = 'message'").fetchall()
    for row in rows:
        if conn.execute("SELECT 1 FROM mail_sent WHERE in_reply_to = ?", (row["ref"],)).fetchone():
            stimuli.close("mail", row["ref"])


def _offered(conn: Any) -> list[Mapping[str, Any]]:
    """The offered mentions not yet answered and X will take a reply to
    (`mentions.replyable`), newest first."""
    from teeny_hands.x import mentions

    rows = conn.execute(
        f"SELECT m.id, m.author_username, m.text, m.conversation_id FROM x_mentions m WHERE m.status = 'offered' AND NOT {ANSWERED}"
        " ORDER BY CAST(m.id AS INTEGER) DESC").fetchall()
    out = []
    for r in rows:
        if not mentions.replyable(conn, r["text"], r["conversation_id"], r["id"]):
            continue
        out.append({k: r[k] for k in ("id", "author_username", "text") if r[k] is not None})
        if len(out) >= X_SCAN:
            break
    return out


def _mail(conn: Any) -> list[Mapping[str, Any]]:
    """The mail waiting, oldest first. Chosen by surface and kind in the query,
    so a long X backlog cannot push it out of view."""
    rows = conn.execute(
        "SELECT * FROM stimuli WHERE status = 'pending' AND surface = 'mail' AND kind = 'message' ORDER BY at, rowid LIMIT ?",
        (MAIL_SCAN,)).fetchall()
    return [stimuli._row(r) for r in rows]


def build() -> str:
    """The block for this beat: empty (and nothing is added to the turn) when
    the heartbeat is not draining, the inbox is empty, or anything fails."""
    if not stimuli.heartbeat_drains():
        return ""
    from teeny_hands.x import mentions

    stimuli.db()  # makes sure the inbox table exists
    conn = mentions._db()  # and the x tables
    for step in (_reconcile_x, _reconcile_mail):
        try:
            step(conn)
        except Exception:  # a table that is not there yet
            pass
    offered = _offered(conn)
    mail = _mail(conn)
    return render(len(offered), len(offered) >= X_SCAN, candidates(offered), len(mail), mail[:MAIL_SHOWN])


def main() -> int:
    try:
        text = build()
    except Exception as exc:  # fail open: the beat goes on without the block
        print(f"reply-queue: {exc}", file=sys.stderr)
        return 0
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
