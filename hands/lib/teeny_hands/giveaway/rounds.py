"""What hands/giveaway does: Tiny Drops, public $TINY rounds paid by code.

The life of a round:

1. `open` (a playbook's output) checks the switch and every GIVEAWAY_* cap,
   posts the round's announcement through the x hand's `post` (guard,
   posting rules, DRY_RUN), and only once it is out records the round with
   that post as its post. The tag, `drop #N`, rides in every post about it.
2. People reply to that post. X's webhook stores each reply in `x_mentions`
   (with the author's age and followers when the delivery carries them);
   `entries` reads only those rows, never X, into one entry per account,
   and binds an address from the first of its replies that carries exactly
   one valid Solana address.
3. After the deadline `judge` (a tool-less playbook's output) records the
   best reply and threads the announcement under the round post (x's
   `follow-up`).
4. `draw` takes the seed from the first finalized Solana block at or after
   the deadline, checks every entrant (eligibility.py), and walks the draw
   order to the first `winners` that pass the late checks (the follow, and
   any age the webhook did not give: one batched `hands/x users` call, at
   most GIVEAWAY_X_LOOKUPS_PER_ROUND).
5. `settle` builds the Merkle tree, publishes the round to the diary, and
   asks the chain (chain.py) for the distribution, funded with exactly the
   round's total.
6. `clawback` closes the distribution after GIVEAWAY_CLAWBACK_DAYS, which
   returns what nobody claimed to the giveaway wallet.

Only `open` and `judge` are playbook outputs (bin/lib/playbook.py); the rest
are job verbs, run by the pinned job through `due`.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
from typing import Any, Mapping

from .. import hand
from ..wallet import solana
from . import caps, chain, draw, eligibility, merkle, publish, store

Json = dict[str, Any]
MAX_DEADLINE_DAYS = 30
#: Random winners one round may have: every candidate fits one X lookup call.
MAX_WINNERS = 99
#: How long after the deadline the draw waits for the judge before it gives
#: the best-reply prize up (`best_skipped`) and draws without it.
JUDGE_GRACE_HOURS = 24
#: When the playbooks owe a countdown before the deadline, and a "closing
#: soon" before the clawback.
COUNTDOWN_HOURS = 24
CLOSING_SOON_HOURS = 72
#: Entries a judge is shown, and how much of each.
JUDGE_ENTRIES = 30
JUDGE_TEXT_CHARS = 500
_OPEN_FIELDS = {"format", "pool_tiny", "winners", "best_reply_tiny", "prompt", "deadline", "deadline_hours", "threshold", "text"}


def _ts(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str) and re.fullmatch(r"\d+(\.\d+)?", value.strip()):
        return float(value)
    return eligibility.epoch(value.strip()) if isinstance(value, str) else None


def _round(conn: sqlite3.Connection, round_id: int) -> sqlite3.Row:
    row = store.get(conn, round_id)
    if row is None:
        raise hand.Refused(f"there is no round {round_id}", rule="unknown")
    return row


def _table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _public(row: Mapping[str, Any]) -> Json:
    out: Json = {
        "id": row["id"],
        "tag": store.tag(row["id"]),
        "status": row["status"],
        "format": row["format"],
        "pool_tiny": caps.display(row["pool_raw"]),
        "winners": row["winners"],
        "best_reply_tiny": caps.display(row["best_reply_raw"]) if row["best_reply_raw"] else "0",
        "prompt": row["prompt"],
        "deadline": publish.iso(row["deadline"]),
        "post_id": row["post_id"],
        "cluster": row["cluster"],
    }
    for key in ("threshold", "best_entry_id", "best_skipped", "outcome", "merkle_root", "distributor", "vault",
                "seed_slot", "seed_sha256", "lookups_used"):
        if row[key] is not None:
            out[key] = row[key]
    for key in ("judged_at", "drawn_at", "settled_at", "clawback_at", "clawed_back_at", "published_at"):
        if row[key] is not None:
            out[key] = publish.iso(row[key])
    return out


def _split(row: Mapping[str, Any]) -> int:
    """Each random winner's prize, atomic."""
    return (row["pool_raw"] - row["best_reply_raw"]) // row["winners"] if row["winners"] else 0


# --- the round's thread ------------------------------------------------------------------


def _thread(conn: sqlite3.Connection, post_id: str) -> list[sqlite3.Row]:
    """The round post's own thread, from the post-ledger: the post, then
    each own reply continuing it (follow-up), in order."""
    if not _table(conn, "x_posts"):
        return []
    chain_rows: list[sqlite3.Row] = []
    current = conn.execute("SELECT id, created_at FROM x_posts WHERE id = ?", (post_id,)).fetchone()
    seen: set[str] = set()
    while current is not None and current["id"] not in seen:
        seen.add(current["id"])
        chain_rows.append(current)
        current = conn.execute(
            "SELECT id, created_at FROM x_posts WHERE in_reply_to = ? AND kind = 'reply' ORDER BY created_at LIMIT 1",
            (current["id"],),
        ).fetchone()
    return chain_rows


def thread_tip(conn: sqlite3.Connection, post_id: str) -> str:
    rows = _thread(conn, post_id)
    return rows[-1]["id"] if rows else post_id


def _posted_since(conn: sqlite3.Connection, post_id: str, since: float) -> bool:
    return any(r["created_at"] >= since for r in _thread(conn, post_id)[1:])


# --- caps at open ---------------------------------------------------------------------------


def _followers() -> int:
    from ..stats import collect

    try:
        got = collect.get("x.followers")
    except hand.Refused:
        raise hand.Refused("a milestone needs today's x.followers from hands/stats, and there is none", rule="threshold") from None
    return int(got["value"])


def _check_open(conn: sqlite3.Connection, body: Mapping[str, Any], the_caps: caps.Caps) -> Json:
    unknown = sorted(set(body) - _OPEN_FIELDS)
    if unknown:
        raise hand.Refused(f"open takes only {', '.join(sorted(_OPEN_FIELDS))}; not {', '.join(unknown)}", rule="fields")
    fmt = body.get("format")
    if fmt not in store.FORMATS:
        raise hand.Refused("format is drop, milestone or challenge", rule="format")
    try:
        pool = caps.to_atomic(body.get("pool_tiny"), "pool_tiny")
        best_value = body.get("best_reply_tiny", "0")
        best = 0 if str(best_value).strip() in ("0", "0.0", "") else caps.to_atomic(best_value, "best_reply_tiny")
    except ValueError as exc:
        raise hand.Refused(str(exc), rule="amount") from None
    winners = body.get("winners")
    if isinstance(winners, bool) or not isinstance(winners, int) or not 0 <= winners <= MAX_WINNERS:
        raise hand.Refused(f"winners is a whole number, 0 to {MAX_WINNERS}", rule="winners")
    if fmt == "challenge":
        if winners != 0 or best != pool:
            raise hand.Refused("a challenge is judged only: winners 0, and the best reply takes the whole pool", rule="format")
    else:
        if winners < 1:
            raise hand.Refused(f"a {fmt} draws at least one winner", rule="winners")
        if best >= pool:
            raise hand.Refused("the best-reply prize leaves nothing for the random winners", rule="amount")
        if (pool - best) % winners:
            raise hand.Refused("the pool less the best-reply prize must split evenly between the winners", rule="split")
    each = (pool - best) // winners if winners else 0
    if pool > the_caps.pool_max:
        raise hand.Refused(f"the pool is over GIVEAWAY_POOL_MAX_TINY ({caps.display(the_caps.pool_max)})", rule="pool_cap")
    if max(each + best, each, best) > the_caps.max_per_winner:
        raise hand.Refused(
            f"one winner could get {caps.display(each + best)}, over GIVEAWAY_MAX_PER_WINNER_TINY "
            f"({caps.display(the_caps.max_per_winner)})", rule="per_winner_cap")
    now = hand.now()
    season = int(conn.execute("SELECT COALESCE(SUM(pool_raw), 0) FROM giveaway_rounds WHERE created_at > ?",
                              (now - 30 * store.DAY,)).fetchone()[0])
    if season + pool > the_caps.season_max:
        raise hand.Refused(
            f"{caps.display(season)} $TINY opened in the last 30 days; this pool would pass GIVEAWAY_SEASON_MAX_TINY "
            f"({caps.display(the_caps.season_max)})", rule="season_cap", left_tiny=caps.display(max(0, the_caps.season_max - season)))
    week = int(conn.execute("SELECT COUNT(*) FROM giveaway_rounds WHERE created_at > ?", (now - 7 * store.DAY,)).fetchone()[0])
    if week >= the_caps.rounds_per_week:
        raise hand.Refused(f"{week} round(s) opened in the last 7 days, the GIVEAWAY_ROUNDS_PER_WEEK cap", rule="weekly_cap")
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 1000:
        raise hand.Refused("prompt is the round's hook, 1-1000 characters", rule="prompt")
    # A drafting turn is not told the time, so a playbook names the deadline
    # as whole hours from now (`deadline_hours`); either it or `deadline`.
    hours = body.get("deadline_hours")
    if hours is not None and body.get("deadline") is not None:
        raise hand.Refused("name the deadline once: deadline or deadline_hours", rule="deadline")
    if hours is not None:
        if isinstance(hours, bool) or not isinstance(hours, int):
            raise hand.Refused("deadline_hours is a whole number of hours", rule="deadline")
        deadline = now + hours * store.HOUR
    else:
        deadline = _ts(body.get("deadline"))
    if deadline is None or deadline < now + store.HOUR or deadline > now + MAX_DEADLINE_DAYS * store.DAY:
        raise hand.Refused(f"deadline is an ISO time or epoch seconds (or deadline_hours), 1 hour to {MAX_DEADLINE_DAYS} days away", rule="deadline")
    threshold = None
    if fmt == "milestone":
        threshold = body.get("threshold")
        if isinstance(threshold, bool) or not isinstance(threshold, int):
            raise hand.Refused("a milestone names its follower threshold, a whole number", rule="threshold")
        current = _followers()
        if threshold <= current:
            raise hand.Refused(f"the threshold must be above today's {current} followers", rule="threshold", followers=current)
    elif body.get("threshold") is not None:
        raise hand.Refused("only a milestone has a threshold", rule="threshold")
    if conn.execute("SELECT 1 FROM giveaway_rounds WHERE status = 'open'").fetchone():
        raise hand.Refused("a round is already open; one at a time", rule="one_open")
    return {"format": fmt, "pool": pool, "best": best, "winners": winners, "prompt": prompt.strip(),
            "deadline": deadline, "threshold": threshold}


def open_round(body: Any) -> Json:
    """A: a round, announced by the post in `text` (which must carry the
    round's tag; `status` names the next one). Nothing is recorded unless
    the post went out."""
    if not isinstance(body, Mapping):
        raise hand.HandError("open takes {format, pool_tiny, winners, best_reply_tiny, prompt, deadline | deadline_hours, threshold?, text}")
    caps.require_on("opened")
    the_caps = caps.current()
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise hand.Refused("text is the post announcing the round", rule="text")
    with hand.lock("giveaway"):
        conn = store.db()
        try:
            plan = _check_open(conn, body, the_caps)
            round_id = store.next_id(conn)
            if not store.tag_pattern(round_id).search(text):
                raise hand.Refused(f"the announcement must carry {store.tag(round_id)!r}", rule="tag", tag=store.tag(round_id))
            from ..x import publish as x_publish

            posted = x_publish.post({"text": text, "anchor": f"giveaway:{round_id}"})
            if posted.get("dry_run"):
                return {"dry_run": True, "would_open": {"id": round_id, "tag": store.tag(round_id),
                                                        "pool_tiny": caps.display(plan["pool"])},
                        "would_post": posted.get("would_post")}
            conn.execute(
                "INSERT INTO giveaway_rounds (id, created_at, format, pool_raw, winners, best_reply_raw, prompt, deadline,"
                " threshold, cluster, post_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (round_id, hand.now(), plan["format"], plan["pool"], plan["winners"], plan["best"], plan["prompt"],
                 plan["deadline"], plan["threshold"], the_caps.cluster, str(posted["id"])),
            )
            row = _round(conn, round_id)
            return {"round": _public(row), "post": posted}
        finally:
            conn.close()


# --- entries --------------------------------------------------------------------------------


def collect(conn: sqlite3.Connection, row: sqlite3.Row, exclude: set[str]) -> Json:
    """The round's entries from `x_mentions` alone: the direct replies to
    its post the webhook stored, made before the deadline, one per account
    (the first reply is the entry; its address is bound from the first of the
    account's replies that carries exactly one valid address, so a later
    reply can supply one the first lacked, and never replace one it had).
    No X call is made here, ever."""
    if not _table(conn, "x_mentions"):
        return {"entries": 0}
    columns = {r[1] for r in conn.execute("PRAGMA table_info(x_mentions)")}
    author_cols = [c for c in ("author_created_at", "author_followers", "author_verified") if c in columns]
    extra = "".join(f", {c}" for c in author_cols)
    replies = conn.execute(
        f"SELECT id, author_id, author_username, created_at, text, status, hold_id{extra} FROM x_mentions"
        " WHERE conversation_id = ? AND id != ?", (row["post_id"], row["post_id"])
    ).fetchall()
    me = publish.own_handle()
    by_author: dict[str, list[Json]] = {}
    for reply in replies:
        created = eligibility.epoch(reply["created_at"])
        if created is None or created >= row["deadline"] or not reply["author_id"] or not reply["author_username"]:
            continue
        if reply["author_username"].lower() == me:
            continue
        by_author.setdefault(str(reply["author_id"]), []).append({**dict(reply), "at": created})
    now = hand.now()
    for user_id, items in by_author.items():
        items.sort(key=lambda r: (r["at"], int(r["id"]) if str(r["id"]).isdigit() else 0))
        first = items[0]
        held = any(r["status"] == "held" or r["hold_id"] is not None for r in items)
        unscreened = any(r["status"] == "unscreened" for r in items)
        bound = eligibility.bind(((str(r["id"]), r["text"]) for r in items), exclude)
        facts = {c: next((r[c] for r in reversed(items) if r.get(c) is not None), None) for c in author_cols}
        conn.execute(
            "INSERT INTO giveaway_entries (round_id, user_id, handle, post_id, text, created_at, address, address_post_id,"
            " held, unscreened, author_created_at, author_followers, author_verified, collected_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(round_id, user_id) DO UPDATE SET handle = excluded.handle, post_id = excluded.post_id,"
            " text = excluded.text, created_at = excluded.created_at, address = excluded.address,"
            " address_post_id = excluded.address_post_id, held = excluded.held, unscreened = excluded.unscreened,"
            " author_created_at = COALESCE(excluded.author_created_at, author_created_at),"
            " author_followers = COALESCE(excluded.author_followers, author_followers),"
            " author_verified = COALESCE(excluded.author_verified, author_verified), updated_at = excluded.updated_at",
            (row["id"], user_id, first["author_username"].lower(), str(first["id"]), first["text"][:1000], first["at"],
             bound[0] if bound else None, bound[1] if bound else None, int(held), int(unscreened),
             facts.get("author_created_at"), facts.get("author_followers"), facts.get("author_verified"), now, now),
        )
    count = conn.execute("SELECT COUNT(*) FROM giveaway_entries WHERE round_id = ?", (row["id"],)).fetchone()[0]
    return {"entries": int(count)}


def _facts(conn: sqlite3.Connection, entries: list[sqlite3.Row]) -> dict[int, eligibility.Facts]:
    from ..x import follows

    state = follows.follow_state(e["user_id"] for e in entries)
    out: dict[int, eligibility.Facts] = {}
    for entry in entries:
        facts = eligibility.Facts()
        facts.merge(entry["author_created_at"], entry["author_followers"], state.get(entry["user_id"]))
        known = conn.execute("SELECT user_created_at, followers FROM x_follows WHERE user_id = ?", (entry["user_id"],)).fetchone()
        if known is not None:
            facts.merge(known["user_created_at"], known["followers"])
        out[entry["id"]] = facts
    return out


def screen(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[list[sqlite3.Row], dict[int, str | None], dict[int, eligibility.Facts]]:
    """Every entry, its first failed local rule (None: eligible so far), and
    what is known of its account. Reads hands.db only."""
    from ..world.collect import classify

    entries = conn.execute("SELECT * FROM giveaway_entries WHERE round_id = ? ORDER BY created_at, id", (row["id"],)).fetchall()
    facts = _facts(conn, entries)
    dupes = eligibility.duplicates(dict(e) for e in entries)
    first_by_address: dict[str, int] = {}
    reasons: dict[int, str | None] = {}
    me = publish.own_handle()
    for entry in entries:
        reason = eligibility.local_reason(
            entry, own_handle=me, agent=classify("x", entry["handle"]) == "agent", duplicate=entry["id"] in dupes,
            facts=facts[entry["id"]], deadline=row["deadline"],
        )
        if reason is None:
            owner = first_by_address.setdefault(entry["address"], entry["id"])
            if owner != entry["id"]:
                reason = "address already entered by another account"
        reasons[entry["id"]] = reason
    return entries, reasons, facts


def _sync_quietly(conn: sqlite3.Connection, round_id: int) -> Json:
    try:
        return publish.sync(conn, round_id)
    except (hand.HandError, hand.Refused) as exc:
        return {"error": getattr(exc, "reason", None) or str(exc)}


def entries(round_id: int | None = None) -> Json:
    """J: every round not drawn yet (or `round_id`): its entries from
    hands.db, and an open round's file in the diary once."""
    with hand.lock("giveaway"):
        conn = store.db()
        try:
            if round_id is not None:
                rows = [_round(conn, round_id)]
            else:
                rows = conn.execute("SELECT * FROM giveaway_rounds WHERE status = 'open' ORDER BY id").fetchall()
            report = []
            for row in rows:
                item: Json = {"round_id": row["id"], **collect(conn, row, eligibility.never_bound())}
                if row["status"] == "open":
                    item["publish"] = _sync_quietly(conn, row["id"])
                report.append(item)
            return {"rounds": report}
        finally:
            conn.close()


# --- judge ----------------------------------------------------------------------------------


def judge(body: Any) -> Json:
    """A: the best reply, after the deadline, announced by `text` (which must
    carry the tag) threaded under the round post. Nothing is recorded unless
    the announcement went out."""
    if not isinstance(body, Mapping):
        raise hand.HandError("judge takes {round_id, entry_id, reasons, text}")
    try:
        round_id, entry_id = int(body.get("round_id")), int(body.get("entry_id"))
    except (TypeError, ValueError):
        raise hand.Refused("round_id and entry_id are numbers from the inputs", rule="ids") from None
    reasons = body.get("reasons")
    if isinstance(reasons, list):
        reasons = "; ".join(str(r) for r in reasons)
    if not isinstance(reasons, str) or not 1 <= len(reasons.strip()) <= 1000:
        raise hand.Refused("reasons say why this reply is the best (1-1000 characters)", rule="reasons")
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise hand.Refused("text is the post announcing the best reply", rule="text")
    if not store.tag_pattern(round_id).search(text):
        raise hand.Refused(f"the announcement must carry {store.tag(round_id)!r}", rule="tag")
    caps.require_on("judged")
    with hand.lock("giveaway"):
        conn = store.db()
        try:
            row = _round(conn, round_id)
            if row["status"] != "open":
                raise hand.Refused(f"round {round_id} is {row['status']}; the judge comes before the draw", rule="status")
            if row["best_entry_id"] is not None or row["best_skipped"]:
                raise hand.Refused(f"round {round_id}'s best reply is already settled", rule="judged")
            if not row["best_reply_raw"]:
                raise hand.Refused(f"round {round_id} has no best-reply prize", rule="no_prize")
            if hand.now() < row["deadline"]:
                raise hand.Refused("the deadline has not passed", rule="deadline", wait_seconds=int(row["deadline"] - hand.now()) + 1)
            collect(conn, row, eligibility.never_bound())
            entry = conn.execute("SELECT * FROM giveaway_entries WHERE id = ? AND round_id = ?", (entry_id, round_id)).fetchone()
            if entry is None:
                raise hand.Refused("no such entry in this round", rule="entry")
            if entry["handle"].lower() == publish.own_handle():
                raise hand.Refused("Teeny cannot win its own round", rule="own")
            _, reasons_by_id, _ = screen(conn, row)
            why = reasons_by_id.get(entry_id)
            if why:
                raise hand.Refused(f"that entry is not eligible: {why}", rule="ineligible", why=why)
            from ..x import publish as x_publish

            posted = x_publish.follow_up(thread_tip(conn, row["post_id"]), {"text": text})
            if posted.get("dry_run"):
                return {"dry_run": True, "would_judge": {"round_id": round_id, "entry_id": entry_id, "handle": entry["handle"],
                                                         "reasons": reasons.strip()}, "would_post": posted.get("would_post")}
            conn.execute(
                "UPDATE giveaway_rounds SET best_entry_id = ?, judge_reasons = ?, judged_at = ?, judge_post_id = ?"
                " WHERE id = ? AND best_entry_id IS NULL",
                (entry_id, reasons.strip(), hand.now(), posted.get("id"), round_id),
            )
            return {"judged": _public(_round(conn, round_id)), "winner": entry["handle"], "post": posted}
        finally:
            conn.close()


# --- draw -----------------------------------------------------------------------------------


def _own_address() -> str | None:
    """Teeny's own Solana address (index 0), which no entry may bind."""
    from ..wallet import pay

    return pay.own_address()


def _lookup(ids: list[str], x: Any) -> dict[str, Json]:
    from ..x import cache

    return {u["id"]: u for u in cache.reads(x).users_by_ids(ids)}


def _judge_gate(conn: sqlite3.Connection, row: sqlite3.Row, reasons: dict[int, str | None]) -> None:
    """The draw waits for the judge, up to JUDGE_GRACE_HOURS past the
    deadline; with nobody eligible to judge, or no prize, it does not wait."""
    if not row["best_reply_raw"] or row["best_entry_id"] is not None or row["best_skipped"]:
        return
    why = None
    if not any(r is None for r in reasons.values()):
        why = "no eligible reply to judge"
    elif hand.now() >= row["deadline"] + JUDGE_GRACE_HOURS * store.HOUR:
        why = f"not judged within {JUDGE_GRACE_HOURS} hours of the deadline"
    if why is None:
        wait = int(row["deadline"] + JUDGE_GRACE_HOURS * store.HOUR - hand.now()) + 1
        raise hand.Refused("waiting for the judge's best-reply pick", rule="waiting_for_judge", wait_seconds=wait)
    conn.execute("UPDATE giveaway_rounds SET best_skipped = ? WHERE id = ?", (why, row["id"]))


def draw_round(round_id: int, x: Any = None) -> Json:
    """J: the draw of one round past its deadline."""
    caps.require_on("drawn")
    the_caps = caps.current()
    with hand.lock("giveaway"):
        conn = store.db()
        try:
            row = _round(conn, round_id)
            if row["status"] != "open":
                raise hand.Refused(f"round {round_id} is already {row['status']}", rule="status")
            if hand.now() < row["deadline"]:
                raise hand.Refused("the deadline has not passed", rule="deadline", wait_seconds=int(row["deadline"] - hand.now()) + 1)
            exclude = eligibility.never_bound()
            own = _own_address()
            if own:
                exclude.add(own)
            collect(conn, row, exclude)
            entries_rows, reasons, facts = screen(conn, row)
            _judge_gate(conn, row, reasons)
            row = _round(conn, round_id)
            seed = draw.seed_slot(row["deadline"])
            seed_sha = draw.seed_hash(round_id, seed["blockhash"])
            by_id = {e["id"]: e for e in entries_rows}
            by_handle = {e["handle"]: e for e in entries_rows}
            outcome = None
            if row["format"] == "milestone":
                followers = _followers()
                if followers < int(row["threshold"]):
                    outcome = f"milestone not reached: {followers} of {row['threshold']} followers"

            best_id = row["best_entry_id"]
            best_skipped = row["best_skipped"]
            if best_id is not None and reasons.get(best_id):
                best_skipped = f"the best reply is no longer eligible: {reasons[best_id]}"
                best_id = None
            lookups = int(row["lookups_used"])
            looked: set[int] = set()

            def verify(handles: list[str]) -> None:
                """One batched lookup for the candidates the webhook told
                us too little about, while the round's lookups last."""
                nonlocal lookups
                pending = [by_handle[h] for h in handles
                           if facts[by_handle[h]["id"]].needs_lookup() and by_handle[h]["id"] not in looked]
                if not pending or lookups >= the_caps.x_lookups:
                    return
                lookups += 1
                conn.execute("UPDATE giveaway_rounds SET lookups_used = ? WHERE id = ?", (lookups, round_id))
                found = _lookup([e["user_id"] for e in pending], x)
                for entry in pending:
                    looked.add(entry["id"])
                    user = found.get(entry["user_id"])
                    if user is None:
                        facts[entry["id"]].found = False
                    else:
                        facts[entry["id"]].merge(user.get("createdAt"), user.get("followers"), user.get("followsMe"))

            def final_reason(handle: str) -> str | None:
                entry = by_handle[handle]
                f = facts[entry["id"]]
                if f.needs_lookup() and entry["id"] not in looked:
                    return eligibility.account_reason(f, row["deadline"], final=False) or eligibility.UNVERIFIABLE
                return eligibility.account_reason(f, row["deadline"], final=True)

            eligible = [e["handle"] for e in entries_rows if reasons[e["id"]] is None]
            order = draw.order(seed_sha, eligible)
            chosen: list[str] = []
            failed: dict[str, str] = {}
            best_handle = by_id[best_id]["handle"] if best_id is not None else None
            need_best = best_handle is not None and outcome is None
            at = 0
            while outcome is None:
                want = int(row["winners"]) - len(chosen)
                batch = order[at:at + want] if want > 0 else []
                at += len(batch)
                check = batch + ([best_handle] if need_best and best_handle not in batch else [])
                if not check:
                    break
                verify(check)
                for handle in batch:
                    why = failed.get(handle) or final_reason(handle)
                    if why:
                        failed[handle] = why
                    else:
                        chosen.append(handle)
                if need_best:
                    need_best = False
                    why = failed.get(best_handle) or final_reason(best_handle)
                    if why:
                        failed[best_handle] = why
                        best_skipped = f"the best reply is no longer eligible: {why}"
                        best_id, best_handle = None, None
            best_ok = best_handle is not None and outcome is None

            final_order = [h for h in order if h not in failed]
            each = _split(row)
            prizes: dict[str, list[tuple[str, int]]] = {}
            if outcome is None:
                for handle in chosen:
                    prizes.setdefault(handle, []).append(("random", each))
                if best_ok:
                    prizes.setdefault(best_handle, []).append(("best_reply", int(row["best_reply_raw"])))
            if outcome is None and not prizes:
                outcome = "nobody eligible"

            conn.execute("DELETE FROM giveaway_draws WHERE round_id = ?", (round_id,))
            position = {h: i for i, h in enumerate(final_order)}
            for entry in entries_rows:
                handle = entry["handle"]
                why = reasons[entry["id"]] or failed.get(handle)
                won = prizes.get(handle, [])
                kind = None
                if won:
                    kind = "best_reply" if any(k == "best_reply" for k, _ in won) else "random"
                conn.execute(
                    "INSERT INTO giveaway_draws (round_id, entry_id, handle, eligible, reason, position, kind, address, amount_raw)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (round_id, entry["id"], handle, 0 if why else 1, why or eligibility.OK,
                     None if why else position.get(handle), kind, entry["address"] if won else None,
                     sum(a for _, a in won) if won else None),
                )
            status = "void" if outcome else "drawn"
            conn.execute(
                "UPDATE giveaway_rounds SET status = ?, outcome = ?, drawn_at = ?, seed_slot = ?, seed_blockhash = ?,"
                " seed_sha256 = ?, best_entry_id = ?, best_skipped = ? WHERE id = ?",
                (status, outcome, hand.now(), seed["slot"], seed["blockhash"], seed_sha, best_id, best_skipped, round_id),
            )
            out: Json = {"round": _public(_round(conn, round_id)), "winners": chosen if not outcome else [],
                         "best_reply": best_handle if best_ok and not outcome else None,
                         "lookups_used": lookups}
            out["publish"] = _sync_quietly(conn, round_id)
            return out
        finally:
            conn.close()


# --- settle ---------------------------------------------------------------------------------


def _tree(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[list[Json], str, int]:
    """The leaves in order (random winners in draw order, then a best reply
    that was not drawn), the root, and the total: one leaf per address."""
    won = conn.execute(
        "SELECT entry_id, handle, kind, address, amount_raw, position FROM giveaway_draws"
        " WHERE round_id = ? AND kind IS NOT NULL", (row["id"],)
    ).fetchall()
    ordered = sorted(won, key=lambda d: (d["position"] is None, d["position"] if d["position"] is not None else 0, d["handle"]))
    if len({d["address"] for d in ordered}) != len(ordered):
        raise hand.HandError(f"round {row['id']} has two winners on one address; the draw must not allow it")
    leaves = [{"entry_id": d["entry_id"], "address": d["address"], "amount": int(d["amount_raw"]),
               "leaf": merkle.leaf_hash(d["address"], int(d["amount_raw"]))} for d in ordered]
    root, proofs = merkle.build([leaf["leaf"] for leaf in leaves])
    for index, (leaf, proof) in enumerate(zip(leaves, proofs)):
        leaf["index"] = index
        leaf["proof"] = [p.hex() for p in proof]
    total = sum(leaf["amount"] for leaf in leaves)
    if total > row["pool_raw"]:
        raise hand.HandError(f"round {row['id']}'s claims add up to more than its pool")
    return leaves, root.hex(), total


def _authority() -> str:
    value = publish.authority()
    if not value:
        raise hand.Refused("GIVEAWAY_AUTHORITY (the giveaway wallet, index 1) is not a Solana address in secrets/teeny.env",
                           rule="authority")
    return value


def _check_chain(result: Any, keys: tuple[str, ...]) -> Json:
    if not isinstance(result, Mapping) or any(not solana.is_address(result.get(k)) for k in keys):
        raise hand.HandError("the chain step answered without the accounts it owes; nothing recorded")
    txs = result.get("txs") or {}
    sig = re.compile(r"[1-9A-HJ-NP-Za-km-z]{64,90}")
    if not isinstance(txs, Mapping) or any(not isinstance(v, str) or not sig.fullmatch(v) for v in txs.values()):
        raise hand.HandError("the chain step answered with a transaction that is not a signature")
    return {**{k: result[k] for k in keys}, "txs": dict(txs)}


def settle_round(round_id: int) -> Json:
    """J: the tree, the round file in the diary, then the distribution."""
    caps.require_on("settled")
    the_caps = caps.current()
    with hand.lock("giveaway"):
        conn = store.db()
        try:
            row = _round(conn, round_id)
            if row["status"] != "drawn":
                raise hand.Refused(f"round {round_id} is {row['status']}, not drawn", rule="status")
            leaves, root, total = _tree(conn, row)
            authority = _authority()
            clawback_at = row["clawback_at"] or hand.now() + the_caps.clawback_days * store.DAY
            if hand.dry_run():
                by_entry = {leaf["entry_id"]: leaf for leaf in leaves}
                draws = conn.execute("SELECT entry_id, handle, kind, address, amount_raw FROM giveaway_draws"
                                     " WHERE round_id = ? AND kind IS NOT NULL", (round_id,)).fetchall()
                overlay = {
                    "merkle_root": root, "clawback_at": publish.iso(clawback_at), "authority": authority,
                    "claims": sorted(({"index": by_entry[d["entry_id"]]["index"], "handle": d["handle"], "kind": d["kind"],
                                       "address": d["address"], "amount_atomic": str(int(d["amount_raw"])),
                                       "proof": by_entry[d["entry_id"]]["proof"]} for d in draws),
                                     key=lambda c: c["index"]),
                }
                return {"dry_run": True,
                        "would_settle": {"round_id": round_id, "merkle_root": root, "total_tiny": caps.display(total),
                                         "leaves": len(leaves)},
                        "publish": publish.sync(conn, round_id, overlay)}
            if row["merkle_root"] is None:
                with hand.transaction(conn):
                    for leaf in leaves:
                        conn.execute("UPDATE giveaway_draws SET leaf_index = ?, proof = ? WHERE round_id = ? AND entry_id = ?",
                                     (leaf["index"], json.dumps(leaf["proof"]), round_id, leaf["entry_id"]))
                    conn.execute("UPDATE giveaway_rounds SET merkle_root = ?, total_raw = ?, clawback_at = ?, authority = ?"
                                 " WHERE id = ?", (root, total, clawback_at, authority, round_id))
            elif row["merkle_root"] != root:
                raise hand.HandError(f"round {round_id}'s stored root no longer matches its draw; a person has to look")
            row = _round(conn, round_id)
            published = publish.sync(conn, round_id)
            plan = {"round_id": round_id, "cluster": row["cluster"], "mint": caps.TINY_MINT, "decimals": caps.TINY_DECIMALS,
                    "token_program": caps.TOKEN_2022, "program_id": caps.REWARDS_PROGRAM, "authority": row["authority"],
                    "merkle_root": row["merkle_root"], "total_amount": int(row["total_raw"]),
                    "clawback_ts": int(row["clawback_at"]), "leaves": len(leaves)}
            try:
                result = chain.create_distribution(plan)
            except hand.Refused as refused:
                return {"round": _public(row), "publish": published, "chain": {"refused": refused.reason, **refused.detail}}
            made = _check_chain(result, ("distribution_seed", "distributor", "vault"))
            conn.execute(
                "UPDATE giveaway_rounds SET status = 'settled', distribution_seed = ?, distributor = ?, vault = ?, tx_create = ?,"
                " tx_fund = ?, settled_at = ? WHERE id = ? AND status = 'drawn'",
                (made["distribution_seed"], made["distributor"], made["vault"], made["txs"].get("create"),
                 made["txs"].get("fund"), hand.now(), round_id),
            )
            return {"round": _public(_round(conn, round_id)), "publish": publish.sync(conn, round_id), "chain": made}
        finally:
            conn.close()


def clawback_round(round_id: int) -> Json:
    """J: after clawback_at, close the distribution: what nobody claimed goes
    back to the giveaway wallet. Runs with the switch off too: it only
    returns tokens to the pool."""
    with hand.lock("giveaway"):
        conn = store.db()
        try:
            row = _round(conn, round_id)
            if row["status"] != "settled":
                raise hand.Refused(f"round {round_id} is {row['status']}, not settled", rule="status")
            if hand.now() < row["clawback_at"]:
                raise hand.Refused("claims are still open", rule="clawback_at", wait_seconds=int(row["clawback_at"] - hand.now()) + 1)
            plan = {"round_id": round_id, "cluster": row["cluster"], "mint": caps.TINY_MINT, "token_program": caps.TOKEN_2022,
                    "program_id": caps.REWARDS_PROGRAM, "authority": row["authority"], "distributor": row["distributor"],
                    "vault": row["vault"], "distribution_seed": row["distribution_seed"]}
            if hand.dry_run():
                return {"dry_run": True, "would_clawback": plan}
            result = chain.close_distribution(plan)
            tx = result.get("tx") if isinstance(result, Mapping) else None
            if not isinstance(tx, str) or not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{64,90}", tx):
                raise hand.HandError("the chain step answered without the clawback's signature; nothing recorded")
            conn.execute("UPDATE giveaway_rounds SET status = 'clawed_back', tx_clawback = ?, clawed_back_at = ? WHERE id = ?",
                         (tx, hand.now(), round_id))
            return {"round": _public(_round(conn, round_id)), "publish": _sync_quietly(conn, round_id), "tx": tx}
        finally:
            conn.close()


def _each(verb: Any, statuses: tuple[str, ...], due: Any) -> list[Json]:
    conn = store.db()
    try:
        rows = conn.execute(
            f"SELECT * FROM giveaway_rounds WHERE status IN ({','.join('?' for _ in statuses)}) ORDER BY id", statuses
        ).fetchall()
    finally:
        conn.close()
    report = []
    for row in rows:
        if not due(row):
            continue
        try:
            report.append({"round_id": row["id"], **verb(row["id"])})
        except hand.Refused as refused:
            report.append({"round_id": row["id"], "refused": refused.reason, **refused.detail})
        except hand.HandError as exc:
            report.append({"round_id": row["id"], "error": str(exc)})
    return report


def draw_due(round_id: int | None = None) -> Json:
    if round_id is not None:
        return draw_round(round_id)
    return {"rounds": _each(draw_round, ("open",), lambda r: hand.now() >= r["deadline"])}


def settle_due(round_id: int | None = None) -> Json:
    if round_id is not None:
        return settle_round(round_id)
    return {"rounds": _each(settle_round, ("drawn",), lambda r: True)}


def clawback_due(round_id: int | None = None) -> Json:
    if round_id is not None:
        return clawback_round(round_id)
    return {"rounds": _each(clawback_round, ("settled",), lambda r: r["clawback_at"] is not None and hand.now() >= r["clawback_at"])}


def due() -> Json:
    """J (the pinned giveaway job): entries, then the draw, the settle and the
    clawback of every round they are due for. One round's refusal or error
    never stops the others."""
    out: Json = {}
    for name, step in (("entries", entries), ("draw", draw_due), ("settle", settle_due), ("clawback", clawback_due)):
        try:
            out[name] = step()
        except hand.Refused as refused:
            out[name] = {"refused": refused.reason, **refused.detail}
        except hand.HandError as exc:
            out[name] = {"error": str(exc)}
    return out


# --- what the playbooks read ----------------------------------------------------------------


def _season(conn: sqlite3.Connection) -> Json:
    now = hand.now()
    season = int(conn.execute("SELECT COALESCE(SUM(pool_raw), 0) FROM giveaway_rounds WHERE created_at > ?",
                              (now - 30 * store.DAY,)).fetchone()[0])
    week = int(conn.execute("SELECT COUNT(*) FROM giveaway_rounds WHERE created_at > ?", (now - 7 * store.DAY,)).fetchone()[0])
    out: Json = {"on": caps.on(), "next_tag": store.tag(store.next_id(conn)), "opened_30d_tiny": caps.display(season),
                 "rounds_7d": week}
    try:
        the_caps = caps.load(hand.secrets())
    except caps.CapsError as exc:
        out["caps_error"] = str(exc)
        return out
    out.update({
        "cluster": the_caps.cluster,
        "pool_max_tiny": caps.display(the_caps.pool_max),
        "season_left_tiny": caps.display(max(0, the_caps.season_max - season)),
        "rounds_left_this_week": max(0, the_caps.rounds_per_week - week),
        "max_per_winner_tiny": caps.display(the_caps.max_per_winner),
        "clawback_days": the_caps.clawback_days,
    })
    return out


def announcements() -> Json:
    """R: what the giveaway playbooks owe a post for.

      open          the room to open a round (switch on, caps sane, none open)
      countdown     an open round within COUNTDOWN_HOURS of its deadline
      to_judge      past the deadline, a best-reply prize, no pick yet: the
                    eligible-so-far entries (never a held one)
      winners       drawn (or void), not yet announced in its thread
      claim_open    settled, not yet announced
      closing_soon  settled, within CLOSING_SOON_HOURS of the clawback

    Every item but `open` and `to_judge` goes out as `hands/x follow-up
    --to <thread_to>`, the newest post of the round's own thread, so it is a
    reply under the round post and never an own post; `to_judge` goes out
    through `hand:giveaway judge`."""
    conn = store.db()
    try:
        now = hand.now()
        out: Json = {"open": [], "countdown": [], "to_judge": [], "winners": [], "claim_open": [], "closing_soon": []}
        season = _season(conn)
        if season["on"] and "caps_error" not in season and season["rounds_left_this_week"] > 0 and not conn.execute(
                "SELECT 1 FROM giveaway_rounds WHERE status = 'open'").fetchone():
            out["open"].append(season)
        for row in conn.execute("SELECT * FROM giveaway_rounds WHERE status != 'clawed_back' ORDER BY id").fetchall():
            brief = {"id": row["id"], "tag": store.tag(row["id"]), "format": row["format"],
                     "pool_tiny": caps.display(row["pool_raw"]), "deadline": publish.iso(row["deadline"]),
                     "post_id": row["post_id"], "thread_to": thread_tip(conn, row["post_id"])}
            status = row["status"]
            if status == "open" and now < row["deadline"] <= now + COUNTDOWN_HOURS * store.HOUR:
                if not _posted_since(conn, row["post_id"], row["deadline"] - COUNTDOWN_HOURS * store.HOUR):
                    count = conn.execute("SELECT COUNT(*) FROM giveaway_entries WHERE round_id = ?", (row["id"],)).fetchone()[0]
                    out["countdown"].append({**brief, "entries": int(count)})
            if (status == "open" and now >= row["deadline"] and row["best_reply_raw"] and row["best_entry_id"] is None
                    and not row["best_skipped"] and now < row["deadline"] + JUDGE_GRACE_HOURS * store.HOUR):
                entries_rows, reasons, _ = screen(conn, row)
                picks = [{"entry_id": e["id"], "handle": e["handle"], "text": e["text"][:JUDGE_TEXT_CHARS]}
                         for e in entries_rows if reasons[e["id"]] is None][:JUDGE_ENTRIES]
                if picks:
                    out["to_judge"].append({**brief, "prompt": row["prompt"],
                                            "best_reply_tiny": caps.display(row["best_reply_raw"]), "entries": picks})
            if status in ("drawn", "void", "settled") and row["drawn_at"] is not None \
                    and not _posted_since(conn, row["post_id"], row["drawn_at"]):
                won = conn.execute("SELECT handle, kind, amount_raw FROM giveaway_draws WHERE round_id = ? AND kind IS NOT NULL"
                                   " ORDER BY position IS NULL, position", (row["id"],)).fetchall()
                out["winners"].append({**brief, "outcome": row["outcome"],
                                       "winners": [{"handle": w["handle"], "kind": w["kind"],
                                                    "tiny": caps.display(w["amount_raw"])} for w in won]})
            if status == "settled" and row["settled_at"] is not None \
                    and not _posted_since(conn, row["post_id"], row["settled_at"]):
                out["claim_open"].append({**brief, "clawback_at": publish.iso(row["clawback_at"]),
                                          "distributor_url": solana.explorer_account(row["distributor"], row["cluster"])})
            if status == "settled" and row["clawback_at"] and now < row["clawback_at"] <= now + CLOSING_SOON_HOURS * store.HOUR:
                if not _posted_since(conn, row["post_id"], row["clawback_at"] - CLOSING_SOON_HOURS * store.HOUR):
                    out["closing_soon"].append({**brief, "clawback_at": publish.iso(row["clawback_at"])})
        return out
    finally:
        conn.close()


def status(round_id: int | None = None) -> Json:
    """R: the season against the caps, and the rounds (or one)."""
    conn = store.db()
    try:
        if round_id is not None:
            row = _round(conn, round_id)
            out = _public(row)
            counts = conn.execute("SELECT COUNT(*) AS n, SUM(address IS NOT NULL) AS bound, SUM(held) AS held"
                                  " FROM giveaway_entries WHERE round_id = ?", (round_id,)).fetchone()
            out["entries"] = {"count": counts["n"], "bound": counts["bound"] or 0, "held": counts["held"] or 0}
            return {"round": out}
        rows = conn.execute("SELECT * FROM giveaway_rounds ORDER BY id DESC LIMIT 20").fetchall()
        return {"season": _season(conn), "rounds": [_public(r) for r in rows]}
    finally:
        conn.close()
