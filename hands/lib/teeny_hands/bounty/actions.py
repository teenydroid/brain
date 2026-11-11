"""What hands/bounty does: open a bounty, collect its entries, pick a winner
and announce it, take the winner's claim, and pay the prize through the
wallet hand.

The life of a bounty:

1. `open` (operator) stores it with its tag, `bounty #N`. The bounty-open
   playbook posts it; any post of Teeny's carrying the tag becomes the
   bounty's X post (Moltbook: a post whose title carries it, or `link`).
2. `entries` (and `pay-due`, hourly) collects the replies to that post and
   the comments on the Moltbook one, screened by the inbound guard. A reply
   made after the deadline is kept but marked late; a held one is never
   shown to a model.
3. `pick` (the bounty-winner playbook, after the deadline) records the
   winner and posts the announcement through the x hand. The moment that
   post is out is `public_at`.
4. The winner replies with an address. `entries` takes the first reply by
   the winning account (in the bounty's thread or under the announcement)
   that carries exactly one valid address as the claim; `claim` does the
   same for a reply the operator names. A claim is set once.
5. `pay` (and `pay-due`) pays once the winner has been public for
   BOUNTY_CHALLENGE_HOURS, to the claimed address, through hands/wallet's
   `pay` with purpose `bounty`, which checks all of it again from the rows.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .. import hand
from ..wallet import caps, solana
from ..wallet import store as wallet_store
from . import store

Json = dict[str, Any]
CHANNELS = ("x", "moltbook")
MAX_DEADLINE_DAYS = 60
_TEXT_CHARS = 1000
_ADDRESS = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![1-9A-HJ-NP-Za-km-z])")


def _db() -> sqlite3.Connection:
    return wallet_store.db()  # the wallet's schema carries the bounty tables


def _ts(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    try:
        moment = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    return moment.timestamp()


def _iso(at: float | None) -> str | None:
    if at is None:
        return None
    return _dt.datetime.fromtimestamp(at, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _x_handle() -> str:
    return (hand.setting("X_BOT_USERNAME", "teenydroid") or "teenydroid").lstrip("@").lower()


def _moltbook_name() -> str:
    return (hand.setting("MOLTBOOK_AGENT_NAME", "teenydroid") or "teenydroid").lower()


def _own(channel: str, author: str) -> bool:
    return author.lower() == (_x_handle() if channel == "x" else _moltbook_name())


def _table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _bounty(conn: sqlite3.Connection, bounty_id: int) -> sqlite3.Row:
    row = store.get(conn, bounty_id)
    if row is None:
        raise hand.Refused(f"there is no bounty {bounty_id}", rule="unknown")
    return row


def _public(row: Mapping[str, Any]) -> Json:
    out = {k: row[k] for k in row.keys() if row[k] is not None}
    out["prize_usdc"] = caps.display(int(row["prize_raw"]), "USDC")
    out["channels"] = json.loads(row["channels"])
    out["tag"] = store.tag(row["id"])
    for key in ("created_at", "deadline", "picked_at", "public_at", "claimed_at", "paid_at"):
        if row[key] is not None:
            out[key] = _iso(row[key])
    if row["public_at"] is not None:
        out["payable_at"] = _iso(row["public_at"] + store.challenge_hours() * store.HOUR)
    return out


# --- open, list, get, link ------------------------------------------------------------


def open_bounty(body: Any) -> Json:
    if not isinstance(body, Mapping):
        raise hand.HandError("open takes {title, rules, prize_usdc, deadline, channels}")
    title, rules_text = body.get("title"), body.get("rules")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
        raise hand.Refused("title is 1-120 characters", rule="title")
    if not isinstance(rules_text, str) or not 1 <= len(rules_text.strip()) <= 2000:
        raise hand.Refused("rules are 1-2000 characters", rule="rules")
    try:
        prize = Decimal(str(body.get("prize_usdc")).strip())
    except (InvalidOperation, ValueError):
        raise hand.Refused("prize_usdc is a decimal number", rule="prize") from None
    if not prize.is_finite() or prize <= 0 or prize * caps.MICRO != (prize * caps.MICRO).to_integral_value():
        raise hand.Refused("prize_usdc is above zero, at most 6 decimals", rule="prize")
    prize_raw = int(prize * caps.MICRO)
    try:
        the_caps = caps.load(hand.secrets())
    except caps.CapsError as exc:
        raise hand.Refused(f"the wallet's caps are not usable, so no prize can be promised: {exc}", rule="caps") from None
    if prize_raw > the_caps.per_tx_micro:
        raise hand.Refused(f"the prize is over the ${caps.usd(the_caps.per_tx_micro)} a payment cap", rule="prize")
    deadline = _ts(body.get("deadline"))
    now = hand.now()
    if deadline is None or deadline < now + store.HOUR or deadline > now + MAX_DEADLINE_DAYS * 86400:
        raise hand.Refused(f"deadline is an ISO time or epoch seconds, at least an hour and at most {MAX_DEADLINE_DAYS} days away", rule="deadline")
    channels = body.get("channels") or ["x"]
    if not isinstance(channels, list) or not channels or any(c not in CHANNELS for c in channels):
        raise hand.Refused("channels is a list of x and moltbook", rule="channels")
    conn = _db()
    try:
        cursor = conn.execute(
            "INSERT INTO bounties (created_at, title, rules, prize_raw, deadline, channels) VALUES (?, ?, ?, ?, ?, ?)",
            (now, title.strip(), rules_text.strip(), prize_raw, deadline, json.dumps(sorted(set(channels)))),
        )
        row = store.get(conn, int(cursor.lastrowid))
    finally:
        conn.close()
    out = _public(row)
    return {"bounty": out, "next": f"run bin/act bounty-open; every post about it must carry {out['tag']!r}"}


def listing(status: str | None) -> Json:
    conn = _db()
    try:
        if status:
            rows = conn.execute("SELECT * FROM bounties WHERE status = ? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM bounties ORDER BY id DESC LIMIT 50").fetchall()
        return {"bounties": [_public(r) for r in rows]}
    finally:
        conn.close()


def get(bounty_id: int) -> Json:
    conn = _db()
    try:
        row = _bounty(conn, bounty_id)
        out = _public(row)
        counts = conn.execute(
            "SELECT channel, COUNT(*) AS n, SUM(late) AS late, SUM(held) AS held FROM bounty_entries WHERE bounty_id = ? GROUP BY channel",
            (bounty_id,),
        ).fetchall()
        out["entries"] = {r["channel"]: {"count": r["n"], "late": r["late"] or 0, "held": r["held"] or 0} for r in counts}
        if row["winner_entry_id"] is not None:
            winner = conn.execute("SELECT channel, author, source_id, text FROM bounty_entries WHERE id = ?", (row["winner_entry_id"],)).fetchone()
            if winner is not None:
                out["winner"] = dict(winner)
        return {"bounty": out}
    finally:
        conn.close()


def link(bounty_id: int, x_post: str | None, moltbook_post: str | None) -> Json:
    if not x_post and not moltbook_post:
        raise hand.HandError("give --x POST_ID and/or --moltbook POST_ID")
    conn = _db()
    try:
        _bounty(conn, bounty_id)
        if x_post:
            if not re.fullmatch(r"\d{1,25}", x_post):
                raise hand.HandError("--x is an X post id")
            conn.execute("UPDATE bounties SET x_post_id = ? WHERE id = ?", (x_post, bounty_id))
        if moltbook_post:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", moltbook_post):
                raise hand.HandError("--moltbook is a Moltbook post id")
            conn.execute("UPDATE bounties SET moltbook_post_id = ? WHERE id = ?", (moltbook_post, bounty_id))
        return {"bounty": _public(_bounty(conn, bounty_id))}
    finally:
        conn.close()


# --- finding Teeny's own posts about a bounty --------------------------------------------


def _own_x_post(conn: sqlite3.Connection, bounty_id: int, since: float) -> sqlite3.Row | None:
    """The earliest X post of Teeny's carrying the tag, made after `since`."""
    if not _table(conn, "x_posts"):
        return None
    pattern = store.tag_pattern(bounty_id)
    for row in conn.execute("SELECT id, text, created_at FROM x_posts WHERE created_at >= ? ORDER BY created_at", (since,)):
        if pattern.search(row["text"]):
            return row
    return None


def _link_posts(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    if row["x_post_id"] is None:
        found = _own_x_post(conn, row["id"], row["created_at"])
        if found is not None:
            conn.execute("UPDATE bounties SET x_post_id = ? WHERE id = ?", (found["id"], row["id"]))
    if row["moltbook_post_id"] is None and _table(conn, "moltbook_posts"):
        pattern = store.tag_pattern(row["id"])
        for post in conn.execute(
            "SELECT post_id, title FROM moltbook_posts WHERE post_id IS NOT NULL AND at >= ? ORDER BY at", (row["created_at"],)
        ):
            if pattern.search(post["title"]):
                conn.execute("UPDATE bounties SET moltbook_post_id = ? WHERE id = ?", (post["post_id"], row["id"]))
                break


def _find_public(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    """A winner announced by hand (a tagged post after the pick) is public
    from that post."""
    if row["status"] in ("picked", "claimed") and row["public_at"] is None and row["picked_at"] is not None:
        found = _own_x_post(conn, row["id"], row["picked_at"])
        if found is not None and found["id"] != row["x_post_id"]:
            conn.execute("UPDATE bounties SET public_at = ?, winner_post_id = ? WHERE id = ?", (found["created_at"], found["id"], row["id"]))


# --- entries ------------------------------------------------------------------------------


def _screen(channel: str, text: str, source_id: str, author: str) -> bool:
    """True when the inbound guard holds it: a held entry never reaches a model."""
    from ..guard.gate import inbound

    verdict = inbound(text, surface="x" if channel == "x" else "moltbook", context={"source_id": source_id, "author": author})
    return verdict.kind == "hold"


def _store_entry(conn: sqlite3.Connection, row: sqlite3.Row, channel: str, source_id: str, author: str, text: str, created: float | None) -> bool:
    if conn.execute(
        "SELECT 1 FROM bounty_entries WHERE bounty_id = ? AND channel = ? AND source_id = ?", (row["id"], channel, source_id)
    ).fetchone():
        return False
    late = 1 if created is None or created > row["deadline"] else 0
    held = 1 if _screen(channel, text, source_id, author) else 0
    conn.execute(
        "INSERT INTO bounty_entries (bounty_id, channel, source_id, author, text, created_at, collected_at, late, held)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (row["id"], channel, source_id, author, text[:_TEXT_CHARS], created, hand.now(), late, held),
    )
    return True


def _x_replies(conversation: str, since_id: str | None = None) -> list[Json]:
    from ..x.client import XApi

    return XApi.from_settings().get_conversation(conversation, 100, since_id=since_id)


def _newest_x_entry(conn: sqlite3.Connection, bounty_id: int) -> str | None:
    """The newest X entry already collected for a bounty. `pay-due` runs
    hourly and X bills every post a search returns, so each later read asks
    only for the replies after it instead of paying for the same 100 again."""
    ids = [r["source_id"] for r in conn.execute(
        "SELECT source_id FROM bounty_entries WHERE bounty_id = ? AND channel = 'x'", (bounty_id,)
    ).fetchall()]
    numeric = [i for i in ids if str(i).isdigit()]
    return max(numeric, key=int) if numeric else None


def _moltbook_comments(post_id: str) -> list[Json]:
    from ..moltbook import reads

    out: list[Json] = []

    def walk(nodes: Any) -> None:
        for node in nodes if isinstance(nodes, list) else []:
            if isinstance(node, Mapping):
                out.append(dict(node))
                walk(node.get("replies"))

    walk(reads.comments(post_id, "new", 100, fresh=True).get("comments"))
    return out


def _author(node: Mapping[str, Any]) -> str | None:
    author = node.get("author")
    if isinstance(author, Mapping):
        author = author.get("name")
    if author is None:
        author = node.get("author_name")
    return author if isinstance(author, str) and author else None


def _collect(conn: sqlite3.Connection, row: sqlite3.Row) -> Json:
    """New replies and comments into bounty_entries. A channel that fails is
    reported; the other still lands."""
    added: dict[str, int] = {}
    failed: dict[str, str] = {}
    channels = json.loads(row["channels"])
    conversations = [c for c in (row["x_post_id"], row["winner_post_id"]) if c]
    if "x" in channels and conversations:
        try:
            since_id = _newest_x_entry(conn, row["id"])
            for conversation in conversations:
                for post in _x_replies(conversation, since_id):
                    author = post.get("authorUsername")
                    if not author or _own("x", author) or post.get("id") in conversations:
                        continue
                    if _store_entry(conn, row, "x", str(post["id"]), author, str(post.get("text", "")), _ts(post.get("createdAt"))):
                        added["x"] = added.get("x", 0) + 1
        except hand.HandError as exc:
            failed["x"] = str(exc)
    if "moltbook" in channels and row["moltbook_post_id"]:
        try:
            for node in _moltbook_comments(row["moltbook_post_id"]):
                author = _author(node)
                if not author or node.get("id") is None or _own("moltbook", author):
                    continue
                text = node.get("content") or node.get("body") or ""
                if _store_entry(conn, row, "moltbook", str(node["id"]), author, str(text), _ts(node.get("created_at"))):
                    added["moltbook"] = added.get("moltbook", 0) + 1
        except hand.HandError as exc:
            failed["moltbook"] = str(exc)
    out: Json = {"added": added}
    if failed:
        out["failed"] = failed
    return out


def _addresses(text: str) -> set[str]:
    found = {m for m in _ADDRESS.findall(text or "") if solana.is_address(m)}
    return {a for a in found if a != caps.TINY_MINT and a not in caps.USDC_MINTS.values()}


def _winner(conn: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row:
    winner = conn.execute("SELECT * FROM bounty_entries WHERE id = ?", (row["winner_entry_id"],)).fetchone()
    if winner is None:
        raise hand.HandError(f"bounty {row['id']}'s winning entry is missing")
    return winner


def _set_claim(conn: sqlite3.Connection, row: sqlite3.Row, address: str, source_id: str) -> None:
    conn.execute(
        "UPDATE bounties SET claim_address = ?, claim_source_id = ?, claimed_at = ?, status = 'claimed' WHERE id = ? AND status = 'picked'",
        (address, source_id, hand.now(), row["id"]),
    )


def _auto_claim(conn: sqlite3.Connection, row: sqlite3.Row) -> str | None:
    """The first reply by the winning account, after the pick, that carries
    exactly one valid address."""
    if row["status"] != "picked" or row["claim_address"]:
        return None
    winner = _winner(conn, row)
    for entry in conn.execute(
        "SELECT * FROM bounty_entries WHERE bounty_id = ? AND channel = ? AND lower(author) = lower(?) AND held = 0 ORDER BY created_at, id",
        (row["id"], winner["channel"], winner["author"]),
    ):
        if entry["created_at"] is None or entry["created_at"] < row["picked_at"]:
            continue
        found = _addresses(entry["text"])
        if len(found) == 1:
            address = found.pop()
            _set_claim(conn, row, address, entry["source_id"])
            return address
    return None


def refresh(conn: sqlite3.Connection, bounty_id: int, collect: bool = True) -> Json:
    row = _bounty(conn, bounty_id)
    _link_posts(conn, row)
    row = _bounty(conn, bounty_id)
    _find_public(conn, row)
    row = _bounty(conn, bounty_id)
    out: Json = {"bounty_id": bounty_id}
    if collect:
        out.update(_collect(conn, row))
    claimed = _auto_claim(conn, _bounty(conn, bounty_id))
    if claimed:
        out["claimed"] = claimed
    out["status"] = _bounty(conn, bounty_id)["status"]
    return out


def entries(bounty_id: int) -> Json:
    """J: link the bounty's posts, collect entries, notice a claim."""
    with hand.lock("bounty"):
        conn = _db()
        try:
            return refresh(conn, bounty_id)
        finally:
            conn.close()


# --- pick, claim -------------------------------------------------------------------------


def pick(body: Any) -> Json:
    """A: the winner, after the deadline, announced in public by the post in
    `text` (it must carry the bounty's tag). Nothing is recorded unless the
    announcement went out."""
    if not isinstance(body, Mapping):
        raise hand.HandError("pick takes {bounty_id, entry_id, reasons, text}")
    try:
        bounty_id, entry_id = int(body.get("bounty_id")), int(body.get("entry_id"))
    except (TypeError, ValueError):
        raise hand.Refused("bounty_id and entry_id are numbers from the inputs", rule="ids") from None
    reasons = body.get("reasons")
    if isinstance(reasons, list):
        reasons = "; ".join(str(r) for r in reasons)
    if not isinstance(reasons, str) or not 1 <= len(reasons.strip()) <= 1000:
        raise hand.Refused("reasons say why this entry won (1-1000 characters)", rule="reasons")
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise hand.Refused("text is the post announcing the winner", rule="text")
    if not store.tag_pattern(bounty_id).search(text):
        raise hand.Refused(f"the announcement must carry {store.tag(bounty_id)!r}", rule="tag")
    with hand.lock("bounty"):
        conn = _db()
        try:
            row = _bounty(conn, bounty_id)
            if row["status"] != "open":
                raise hand.Refused(f"bounty {bounty_id} already has a winner", rule="picked", status=row["status"])
            if hand.now() < row["deadline"]:
                raise hand.Refused("the deadline has not passed", rule="deadline", wait_seconds=int(row["deadline"] - hand.now()) + 1)
            entry = conn.execute("SELECT * FROM bounty_entries WHERE id = ? AND bounty_id = ?", (entry_id, bounty_id)).fetchone()
            if entry is None:
                raise hand.Refused("no such entry for this bounty", rule="entry")
            if entry["late"]:
                raise hand.Refused("that entry came after the deadline", rule="late")
            if entry["held"]:
                raise hand.Refused("the guard held that entry", rule="held")
            if _own(entry["channel"], entry["author"]):
                raise hand.Refused("Teeny cannot win its own bounty", rule="own")
            from ..x import publish

            posted = publish.post({"text": text})
            if posted.get("dry_run"):
                return {"dry_run": True, "would_pick": {"bounty_id": bounty_id, "entry_id": entry_id, "author": entry["author"], "reasons": reasons.strip()}, "would_post": posted.get("would_post")}
            now = hand.now()
            conn.execute(
                "UPDATE bounties SET status = 'picked', winner_entry_id = ?, reasons = ?, picked_at = ?, public_at = ?, winner_post_id = ? WHERE id = ? AND status = 'open'",
                (entry_id, reasons.strip(), now, now, posted.get("id"), bounty_id),
            )
            return {"picked": _public(_bounty(conn, bounty_id)), "winner": entry["author"], "post": posted}
        finally:
            conn.close()


def claim(body: Any) -> Json:
    """The winner's address, from a reply or comment the winning account made."""
    if not isinstance(body, Mapping):
        raise hand.HandError("claim takes {bounty_id, address, source_id}")
    try:
        bounty_id = int(body.get("bounty_id"))
    except (TypeError, ValueError):
        raise hand.Refused("bounty_id is a number", rule="ids") from None
    address, source_id = body.get("address"), str(body.get("source_id") or "")
    if not solana.is_address(address) or address == caps.TINY_MINT or address in caps.USDC_MINTS.values():
        raise hand.Refused("address is not a valid Solana wallet address", rule="address")
    with hand.lock("bounty"):
        conn = _db()
        try:
            row = _bounty(conn, bounty_id)
            if row["status"] != "picked" or row["claim_address"]:
                raise hand.Refused(f"bounty {bounty_id} is not waiting for a claim", rule="status", status=row["status"])
            winner = _winner(conn, row)
            entry = conn.execute(
                "SELECT * FROM bounty_entries WHERE bounty_id = ? AND source_id = ?", (bounty_id, source_id)
            ).fetchone()
            if entry is None:
                refresh(conn, bounty_id)
                row = _bounty(conn, bounty_id)
                if row["claim_address"]:
                    raise hand.Refused("the winner's claim was just found in the replies", rule="status", claim_address=row["claim_address"])
                entry = conn.execute("SELECT * FROM bounty_entries WHERE bounty_id = ? AND source_id = ?", (bounty_id, source_id)).fetchone()
            if entry is None:
                raise hand.Refused("that reply is not one the bounty has collected", rule="source")
            if entry["channel"] != winner["channel"] or entry["author"].lower() != winner["author"].lower():
                raise hand.Refused("that reply is not from the winning account", rule="author")
            if address not in _addresses(entry["text"]):
                raise hand.Refused("that reply does not carry this address", rule="address")
            _set_claim(conn, row, address, source_id)
            return {"claimed": _public(_bounty(conn, bounty_id))}
        finally:
            conn.close()


# --- pay ----------------------------------------------------------------------------------


def pay(bounty_id: int) -> Json:
    """J: the prize to the claimed address, through the wallet's pay."""
    from ..wallet import pay as wallet_pay

    conn = _db()
    try:
        row = _bounty(conn, bounty_id)
    finally:
        conn.close()
    if row["status"] != "claimed":
        raise hand.Refused(f"bounty {bounty_id} is {row['status']}, not claimed", rule="status", status=row["status"])
    body = {"to": row["claim_address"], "amount_usdc": caps.display(int(row["prize_raw"]), "USDC"), "purpose": "bounty", "ref": str(bounty_id)}
    try:
        result = wallet_pay.pay(body)
    except hand.HandError:
        conn = _db()
        try:
            prior = wallet_store.active_for_ref(conn, "bounty", str(bounty_id))
            if prior is not None and prior["status"] == "in_doubt":
                conn.execute("UPDATE bounties SET status = 'in_doubt', payment_id = ? WHERE id = ?", (prior["id"], bounty_id))
        finally:
            conn.close()
        raise
    if result.get("dry_run"):
        return result
    paid = result["paid"]
    conn = _db()
    try:
        conn.execute("UPDATE bounties SET status = 'paid', payment_id = ?, paid_at = ? WHERE id = ?", (paid["payment_id"], hand.now(), bounty_id))
    finally:
        conn.close()
    return result


def pay_due() -> Json:
    """J (bin/bounty-pay, hourly): collect for every bounty past its deadline
    that is not paid, notice claims and announcements, then pay each one
    whose challenge window has passed. One bounty's failure never stops the
    others."""
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT id, status, deadline FROM bounties WHERE status IN ('open', 'picked', 'claimed') ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    report = []
    for row in rows:
        item: Json = {"bounty_id": row["id"]}
        if row["status"] == "open" and hand.now() < row["deadline"]:
            continue
        try:
            item.update(entries(row["id"]))
        except hand.HandError as exc:
            item["error"] = str(exc)
        if item.get("status") == "claimed":
            try:
                result = pay(row["id"])
                item["pay"] = result
            except hand.Refused as refused:
                item["pay"] = {"refused": refused.reason, **refused.detail}
            except hand.HandError as exc:
                item["pay"] = {"error": str(exc)}
        report.append(item)
    return {"bounties": report}


# --- what the playbooks read ----------------------------------------------------------------


def announcements() -> Json:
    """R: what the bounty playbooks owe a post for. `open`: bounties with no
    tagged post yet. `to_pick`: past the deadline with entries to choose
    from (held and late ones left out). `paid`: prizes whose signature no X
    post of Teeny's has carried yet."""
    conn = _db()
    try:
        has_posts = _table(conn, "x_posts")
        out: Json = {"open": [], "to_pick": [], "paid": []}
        for row in conn.execute("SELECT * FROM bounties WHERE status IN ('open', 'paid') ORDER BY id"):
            public = _public(row)
            brief = {k: public[k] for k in ("id", "tag", "title", "rules", "prize_usdc", "deadline", "channels")}
            if row["status"] == "open":
                if row["x_post_id"] is None and _own_x_post(conn, row["id"], row["created_at"]) is None and hand.now() < row["deadline"]:
                    out["open"].append(brief)
                if hand.now() >= row["deadline"]:
                    picks = conn.execute(
                        "SELECT id AS entry_id, channel, author, text FROM bounty_entries"
                        " WHERE bounty_id = ? AND late = 0 AND held = 0 ORDER BY created_at, id LIMIT 30",
                        (row["id"],),
                    ).fetchall()
                    picks = [{**dict(p), "text": p["text"][:500]} for p in picks if not _own(p["channel"], p["author"])]
                    if picks:
                        out["to_pick"].append({**brief, "entries": picks})
            elif row["payment_id"] is not None:
                payment = wallet_store.payment(conn, row["payment_id"])
                if not payment or not payment.get("signature"):
                    continue
                if has_posts and conn.execute("SELECT 1 FROM x_posts WHERE instr(text, ?) > 0", (payment["signature"],)).fetchone():
                    continue
                winner = conn.execute("SELECT author, channel FROM bounty_entries WHERE id = ?", (row["winner_entry_id"],)).fetchone()
                out["paid"].append(
                    {
                        **brief,
                        "winner": dict(winner) if winner else None,
                        "amount": caps.display(int(payment["amount_raw"]), payment["asset"]) + " " + payment["asset"],
                        "url": solana.explorer_tx(payment["signature"], payment["cluster"]),
                    }
                )
        return out
    finally:
        conn.close()
