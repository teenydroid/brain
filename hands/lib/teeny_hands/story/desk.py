"""The desk: what really happened in the last hours, each thing with its
stake, for the studio to pitch from (docs/hands.md, "story desk").

Plain code, no model. Every item is one ledger row or file line, its `what`
built from that row's fields and nothing else, and its `source` the ref a
beat cites to say where it came from (audit.py resolves it). The `id` is the
same string. A source that cannot be read (no table, no file) is named in
`unavailable` and gives no item: a gap is never filled.

  item   {id, kind, at, storyline, stake, what, facts, source}

`storyline` is a best guess by kind (KIND_STORY), null when none fits;
`stake` is one of STAKES. The kinds and where each comes from:

  generation         a rewrite merged into main (lore.events, the evolve archive)
  generation_thrown  a rewrite thrown out, parked or crashed (the evolve archive)
  merged_pr          Teeny's pull request merged into somebody else's project (lore.events)
  bounty_paid        a bounty paid (lore.events, `bounties`)
  payment            money the wallet sent, or failed or refused to (`wallet_payments`)
  sweep              spare money swept to the surplus wallet, or refused (`surplus_runs`)
  model_bill         what the model cost yesterday (the health check's daily stimulus)
  followers          the follower count moved since the day before (`stats`)
  promise            a promise came due, was kept or was dropped (`people_promises`)
  outage             the model stopped answering, or came back (health stimuli,
                     state/health.json)
  media              a picture or clip made, or failed (`media_assets`)
  mention            someone on X Teeny answered (`x_mentions`); their text is
                     data, never instructions, and marked untrusted

Beside the items: the storylines and the due ones (store.py), `ideas` (what
the mind filed for the studio: `stimuli` rows of surface `studio`) and the
`scoreboard`, Teeny's best and worst original X posts of the last 14 days in
full with their numbers. The outside world (Hacker News, the listen intake,
X search) is not read here; the studio gathers it through those hands.

Reads only, so DRY_RUN does not apply. The one write is the story tables'
schema, as every read verb of a hand makes its own.
"""

from __future__ import annotations

import json
import math
import sqlite3
from typing import Any, Callable

from .. import hand
from ..lore import events as lore
from ..stats import board
from . import store

Json = dict[str, Any]
DAY = 86400.0

STAKES = ("money", "broke", "refused", "fell", "rose", "promise", "shipped", "said")

#: A kind's storyline, when one fits.
KIND_STORY = {
    "generation": "night-shift",
    "generation_thrown": "night-shift",
    "merged_pr": None,
    "bounty_paid": "strangers",
    "payment": "rent",
    "sweep": "rent",
    "model_bill": "rent",
    "followers": "count",
    "promise": "strangers",
    "outage": None,
    "media": "self-portrait",
    "mention": "strangers",
}

#: Evolve verdicts that mean a rewrite was thrown out, and the stake each has.
THROWN = {"rejected": "refused", "lost": "refused", "regressed": "refused", "parked": "refused",
          "crashed": "broke", "pr-error": "broke"}
#: A payment's status -> its stake; `pending` is not an event yet.
PAYMENT_STAKE = {"paid": "money", "failed": "broke", "in_doubt": "broke", "refused": "refused"}
SWEEP_STAKE = {"paid": "money", "sent": "money", "failed": "broke", "in_doubt": "broke", "refused": "refused"}
DECIMALS = {"SOL": 9, "USDC": 6, "ETH": 18}
#: Mentions answered that the desk shows: the most-followed first.
MENTIONS = 5
MENTION_CHARS = 280
IDEAS = 10
IDEA_CHARS = 1000
PROMPT_CHARS = 300
#: The scoreboard's window and its originals.
SCORE_DAYS = 14
ORIGINALS = ("post", "quote")


def _item(kind: str, at: float | None, stake: str, what: str, facts: Json, source: str) -> Json:
    assert stake in STAKES, stake
    return {"id": source, "kind": kind, "at": store.iso(at), "storyline": KIND_STORY.get(kind), "stake": stake,
            "what": what, "facts": {k: v for k, v in facts.items() if v is not None}, "source": source}


def _usd(micro: Any) -> str:
    return f"${int(micro or 0) / 1_000_000:.2f}"


def _amount(asset: str, raw: Any) -> str:
    places = DECIMALS.get(asset, 0)
    value = int(raw or 0) / (10 ** places)
    return f"{value:.{min(places, 6)}f}".rstrip("0").rstrip(".") + f" {asset}"


def _cut(text: Any, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


class Missing(Exception):
    """A source is not there to read: named in `unavailable`, no item."""


class Ledger:
    """state/hands.db opened read-only, or None when there is none."""

    def __init__(self) -> None:
        path = hand.state_dir() / "hands.db"
        self.conn: sqlite3.Connection | None = None
        self.tables: set[str] = set()
        if path.is_file():
            self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            self.conn.row_factory = sqlite3.Row
            self.tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    def need(self, *tables: str) -> sqlite3.Connection:
        if self.conn is None:
            raise Missing("no state/hands.db")
        lacking = [t for t in tables if t not in self.tables]
        if lacking:
            raise Missing(f"no {', '.join(lacking)} table")
        return self.conn

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()


# --- the sources ----------------------------------------------------------------------


def generations(since: float) -> list[Json]:
    out = []
    for e in lore.generations(since):
        out.append(_item("generation", lore._when(e["at"]), "shipped",
                         f"a rewrite was kept and merged ({e['merged_by']}): {e.get('title') or 'untitled'}",
                         {"gen": e["gen"], "title": e.get("title"), "component": e.get("component"),
                          "merged_by": e["merged_by"], "pr_url": e.get("pr_url"), "chronicle": e.get("chronicle")},
                         f"evolve:{e['id']}"))
    return out


def thrown(since: float) -> list[Json]:
    """Rewrites thrown out: a verdict in THROWN recorded since SINCE."""
    path = hand.state_dir() / "evolve" / "archive.jsonl"
    if not path.is_file():
        raise Missing("no state/evolve/archive.jsonl")
    out = []
    for entry in lore.archive().values():
        stake = THROWN.get(str(entry.get("verdict")))
        at = lore._when(entry.get("recorded_at"))
        if stake is None or at is None or at < since or entry.get("merged_at"):
            continue
        why = entry.get("reason") or entry.get("eval_reason")
        rounds = entry.get("rounds")
        title = entry.get("title")
        what = f"a rewrite was {entry['verdict']}" + (f": {title}" if title else "")
        out.append(_item("generation_thrown", at, stake, what,
                         {"gen": entry.get("gen"), "verdict": entry["verdict"], "title": title,
                          "component": entry.get("component"), "reason": _cut(why, 300) if why else None,
                          "last_round": _cut(rounds[-1], 300) if isinstance(rounds, list) and rounds else None},
                         f"evolve:{entry['id']}"))
    return out


def merged_prs(hours: int) -> list[Json]:
    since = hand.now() - hours * 3600
    out = []
    for e in lore.merged_prs(max(1, math.ceil(hours / 24))):
        at = lore._when(e.get("at"))
        if at is not None and at < since:
            continue
        out.append(_item("merged_pr", at, "shipped",
                         f"a fix it wrote was merged into {e['repo']}: {e.get('title') or ''}".rstrip(": "),
                         {"repo": e["repo"], "number": e.get("number"), "title": e.get("title"),
                          "url": e.get("html_url")}, f"gh:{e['repo']}#{e.get('number')}"))
    return out


def bounties(ledger: Ledger, since: float) -> list[Json]:
    ledger.need("bounties")
    return [_item("bounty_paid", lore._when(e["at"]), "money", f"a bounty was paid: {e['title']}",
                  {"bounty_id": e["bounty_id"], "title": e["title"]}, f"bounties:{e['bounty_id']}")
            for e in lore.bounties_paid(since)]


def payments(ledger: Ledger, since: float) -> list[Json]:
    """What the wallet sent. A payment a surplus sweep made is the sweep's
    item, not a second one."""
    conn = ledger.need("wallet_payments")
    swept = set()
    if "surplus_runs" in ledger.tables:
        swept = {r[0] for r in conn.execute("SELECT ref FROM surplus_runs WHERE ref IS NOT NULL")}
    out = []
    for r in conn.execute("SELECT * FROM wallet_payments WHERE created_at >= ? ORDER BY created_at", (since,)):
        stake = PAYMENT_STAKE.get(r["status"])
        if stake is None or r["ref"] in swept:
            continue
        amount = _amount(r["asset"], r["amount_raw"])
        verb = {"paid": "paid", "failed": "failed to pay", "in_doubt": "may or may not have paid",
                "refused": "was stopped from paying"}[r["status"]]
        out.append(_item("payment", r["created_at"], stake,
                         f"the wallet {verb} {amount} (about {_usd(r['usd_micro'])}) for a {r['purpose']}",
                         {"asset": r["asset"], "amount": amount, "usd": _usd(r["usd_micro"]), "purpose": r["purpose"],
                          "ref": r["ref"], "status": r["status"], "chain": r["chain"], "error": r["error"]},
                         f"wallet_payments:{r['id']}"))
    return out


def sweeps(ledger: Ledger, since: float) -> list[Json]:
    conn = ledger.need("surplus_runs")
    out = []
    for r in conn.execute("SELECT * FROM surplus_runs WHERE at >= ? ORDER BY at", (since,)):
        stake = SWEEP_STAKE.get(r["outcome"])
        if stake is None:
            continue
        amount = f"{r['amount']} {r['asset']}" if r["amount"] and r["asset"] else _usd(r["usd_micro"])
        if stake == "money":
            what = f"spare money swept out of its wallet: {amount} (about {_usd(r['usd_micro'])})"
        elif stake == "refused":
            what = f"a sweep of {amount} was stopped" + (f": {_cut(r['detail'], 200)}" if r["detail"] else "")
        else:
            what = f"a sweep of {amount} went wrong ({r['outcome']})"
        out.append(_item("sweep", r["at"], stake, what,
                         {"step": r["step"], "outcome": r["outcome"], "asset": r["asset"], "amount": r["amount"],
                          "usd": _usd(r["usd_micro"]), "rule": r["rule"], "detail": _cut(r["detail"], 300) if r["detail"] else None},
                         f"surplus_runs:{r['id']}"))
    return out


def _stimuli(ledger: Ledger, surface: str, kinds: tuple[str, ...], since: float) -> list[sqlite3.Row]:
    conn = ledger.need("stimuli")
    marks = ", ".join("?" for _ in kinds)
    return conn.execute(f"SELECT * FROM stimuli WHERE surface = ? AND kind IN ({marks}) AND at >= ? ORDER BY at",
                        (surface, *kinds, since)).fetchall()


def _payload(row: sqlite3.Row) -> Json:
    try:
        value = json.loads(row["payload"] or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def model_bills(ledger: Ledger, since: float) -> list[Json]:
    out = []
    for r in _stimuli(ledger, "health", ("daily",), since):
        cost = _payload(r).get("cost")
        if not isinstance(cost, dict) or not isinstance(cost.get("usd"), (int, float)):
            continue
        day = _payload(r).get("day")
        out.append(_item("model_bill", r["at"], "money",
                         f"thinking cost ${cost['usd']:.2f} on {day} over {cost.get('calls')} model calls",
                         {"day": day, "usd": cost["usd"], "calls": cost.get("calls")}, f"stimuli:{r['id']}"))
    return out


def outages(ledger: Ledger, since: float) -> list[Json]:
    out = []
    for r in _stimuli(ledger, "health", ("alarm", "recovered"), since):
        payload = _payload(r)
        first = payload.get("first_failed_at")
        if r["kind"] == "alarm":
            what = "its model stopped answering" + (f" (since {store.iso(first)})" if isinstance(first, (int, float)) else "")
        else:
            back = payload.get("recovered_at")
            minutes = round((back - first) / 60) if isinstance(back, (int, float)) and isinstance(first, (int, float)) else None
            what = "its model answers again" + (f" after about {minutes} minutes down" if minutes is not None else "")
        out.append(_item("outage", r["at"], "broke", what,
                         {"event": r["kind"], "first_failed_at": store.iso(first) if isinstance(first, (int, float)) else None,
                          "failures": payload.get("failures"), "issue": payload.get("issue")}, f"stimuli:{r['id']}"))
    return out


def health_now() -> list[Json]:
    """An outage still going on, from state/health.json."""
    path = hand.state_dir() / "health.json"
    try:
        health = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise Missing("no state/health.json") from exc
    except ValueError as exc:
        raise Missing("state/health.json is not JSON") from exc
    if not isinstance(health, dict) or not health.get("failures") or not isinstance(health.get("first_failed_at"), (int, float)):
        return []
    first = health["first_failed_at"]
    return [_item("outage", first, "broke", f"its model is not answering right now (since {store.iso(first)})",
                  {"event": "ongoing", "failures": health.get("failures"), "first_failed_at": store.iso(first),
                   "alarm": bool(health.get("alarm"))}, "file:state/health.json")]


def followers(ledger: Ledger, since: float) -> list[Json]:
    """The X follower count against the day before, when the newest snapshot
    falls in the window and the count moved."""
    conn = ledger.need("stats")
    latest = conn.execute("SELECT date, value, at FROM stats WHERE source = 'x' AND metric = 'followers'"
                          " ORDER BY date DESC LIMIT 1").fetchone()
    if latest is None or latest["at"] < since:
        return []
    row = next((a for a in board.audience(conn) if a["metric"] == "x.followers"), None)
    change = row.get("d1") if row else None
    if not change:
        return []
    value = board._num(latest["value"])
    gained = change > 0
    what = f"{'gained' if gained else 'lost'} {abs(change)} follower{'s' if abs(change) != 1 else ''} on X: {value} now"
    return [_item("followers", latest["at"], "rose" if gained else "fell", what,
                  {"followers": value, "d1": change, "d7": row.get("d7"), "date": latest["date"]},
                  f"stats:{latest['date']}:x:followers")]


def promises(ledger: Ledger, since: float, until: float) -> list[Json]:
    """Promises kept or dropped in the window, and open ones that came due
    in it or come due before UNTIL."""
    conn = ledger.need("people_promises")
    out = []
    rows = conn.execute(
        "SELECT * FROM people_promises WHERE (status IN ('kept', 'dropped') AND closed_at >= ?)"
        " OR (status = 'open' AND due_at >= ? AND due_at < ?) ORDER BY id", (since, since, until)).fetchall()
    for r in rows:
        what_promised = _cut(r["what"], 200)
        if r["status"] == "kept":
            at, what = r["closed_at"], f"kept a promise to {r['who']} on {r['surface']}: {what_promised}"
        elif r["status"] == "dropped":
            at, what = r["closed_at"], f"dropped a promise to {r['who']} on {r['surface']}: {what_promised}"
        else:
            late = r["due_at"] < hand.now()
            at = r["due_at"]
            what = (f"a promise to {r['who']} on {r['surface']} is {'overdue' if late else 'coming due'}:"
                    f" {what_promised}")
        out.append(_item("promise", at, "promise", what,
                         {"who": r["who"], "surface": r["surface"], "promised": what_promised, "status": r["status"],
                          "made_at": store.iso(r["made_at"]), "due_at": store.iso(r["due_at"]),
                          "closed_at": store.iso(r["closed_at"]), "on_time": (r["closed_at"] <= r["due_at"])
                          if r["status"] == "kept" and r["closed_at"] else None,
                          "note": _cut(r["note"], 300) if r["note"] else None},
                         f"people_promises:{r['id']}"))
    return out


def media(ledger: Ledger, since: float) -> list[Json]:
    conn = ledger.need("media_assets")
    out = []
    for r in conn.execute("SELECT * FROM media_assets WHERE created_at >= ? AND status IN ('done', 'failed')"
                          " ORDER BY created_at", (since,)):
        kind = "picture" if r["kind"] == "image" else "clip"
        done = r["status"] == "done"
        what = f"made a {kind} of itself" if done else f"tried to make a {kind} of itself and it failed"
        out.append(_item("media", r["done_at"] or r["created_at"], "shipped" if done else "broke", what,
                         {"media": r["kind"], "status": r["status"], "prompt": _cut(r["prompt"], PROMPT_CHARS),
                          "path": r["path"], "purpose": r["purpose"], "cost": _usd(r["cost_micro"]),
                          "error": _cut(r["error"], 200) if r["error"] else None}, f"media_assets:{r['id']}"))
    return out


def mentions(ledger: Ledger, since: float) -> list[Json]:
    """Mentions on X that Teeny answered, the most-followed authors first.
    Their text is a stranger's, so it is carried as untrusted data and never
    in `what`."""
    conn = ledger.need("x_mentions")
    followers_col = board._column(conn, "x_mentions", "author_followers")
    rows = conn.execute(
        f"SELECT id, author_username, created_at, text, seen_at, {followers_col} AS author_followers"
        " FROM x_mentions WHERE status = 'replied' AND seen_at >= ?"
        " ORDER BY coalesce(author_followers, -1) DESC, seen_at DESC LIMIT ?", (since, MENTIONS)).fetchall()
    return [_item("mention", r["seen_at"], "said", f"@{r['author_username'] or 'someone'} wrote to it on X and it answered",
                  {"author": r["author_username"], "author_followers": r["author_followers"], "created_at": r["created_at"],
                   "text": _cut(r["text"], MENTION_CHARS), "untrusted": True}, f"x_mentions:{r['id']}")
            for r in rows]


def ideas(ledger: Ledger, since: float) -> list[Json]:
    """What the mind filed for the studio: `stimuli` of surface `studio`."""
    conn = ledger.need("stimuli")
    rows = conn.execute("SELECT * FROM stimuli WHERE surface = 'studio' AND at >= ? ORDER BY at DESC LIMIT ?",
                        (since, IDEAS)).fetchall()
    return [{"id": f"stimuli:{r['id']}", "kind": r["kind"], "at": store.iso(r["at"]), "ref": r["ref"],
             "text": _cut(r["text"], IDEA_CHARS), "status": r["status"]} for r in rows]


# --- the scoreboard -------------------------------------------------------------------


def scoreboard(ledger: Ledger, days: int = SCORE_DAYS) -> Json:
    """Teeny's best and worst original X posts (posts and quotes) of the last
    DAYS days, ranked as `hands/stats board` ranks them (stats/board.py), in
    full, with the most each count reached over every measurement."""
    conn = ledger.need("x_posts", "x_post_metrics")
    writes = [w for w in board.x_writes(conn, hand.now() - days * DAY) if w["kind"] in ORIGINALS]
    best, worst = board.rank(writes)
    ids = [w["id"] for w in best + worst]
    full: dict[str, sqlite3.Row] = {}
    if ids:
        marks = ", ".join("?" for _ in ids)
        full = {r["id"]: r for r in conn.execute(
            "SELECT p.id, p.text, max(m.impressions) AS impressions, max(m.likes) AS likes, max(m.replies) AS replies,"
            " max(m.reposts) AS reposts, max(m.quotes) AS quotes, max(m.bookmarks) AS bookmarks"
            f" FROM x_posts p JOIN x_post_metrics m ON m.post_id = p.id WHERE p.id IN ({marks}) GROUP BY p.id", ids)}

    def shown(w: Json) -> Json:
        r = full[w["id"]]
        return {"id": w["id"], "kind": w["kind"], "format": w["format"], "at": store.iso(w["at"]), "text": r["text"],
                "impressions": r["impressions"], "likes": r["likes"], "replies": r["replies"], "reposts": r["reposts"],
                "quotes": r["quotes"], "bookmarks": r["bookmarks"], "result": w["result"], "source": f"x_posts:{w['id']}"}

    return {"days": days, "measured": len(writes), "best": [shown(w) for w in best], "worst": [shown(w) for w in worst],
            "formats": board.by_format(writes),
            "result": "likes + replies + reposts + quotes at the newest measurement, as hands/stats board counts it"}


# --- the desk -------------------------------------------------------------------------


def desk(hours: int = 24, due_days: int = store.DUE_DAYS) -> Json:
    now = hand.now()
    since = now - hours * 3600
    items: list[Json] = []
    unavailable: list[str] = []
    ledger = Ledger()

    def read(name: str, get: Callable[[], list[Json]]) -> list[Json]:
        try:
            return get()
        except Missing as exc:
            unavailable.append(f"{name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - one unreadable source never hides the others
            unavailable.append(f"{name}: {type(exc).__name__}")
        return []

    try:
        for name, get in (
            ("evolve", lambda: generations(since) + thrown(since)),
            ("gh", lambda: merged_prs(hours)),
            ("bounties", lambda: bounties(ledger, since)),
            ("wallet_payments", lambda: payments(ledger, since)),
            ("surplus_runs", lambda: sweeps(ledger, since)),
            ("model_bill", lambda: model_bills(ledger, since)),
            ("followers", lambda: followers(ledger, since)),
            ("people_promises", lambda: promises(ledger, since, now + hours * 3600)),
            ("health", lambda: outages(ledger, since)),
            ("health.json", health_now),
            ("media_assets", lambda: media(ledger, since)),
            ("x_mentions", lambda: mentions(ledger, since)),
        ):
            items.extend(read(name, get))
        filed = read("ideas", lambda: ideas(ledger, since))
        board_out: Json = {}
        try:
            board_out = scoreboard(ledger)
        except Missing as exc:
            unavailable.append(f"scoreboard: {exc}")
    finally:
        ledger.close()
    conn = store.db()
    try:
        stories = store.state(conn)["storylines"]
        waiting = store.due(due_days, conn)["due"]
    finally:
        conn.close()
    items.sort(key=lambda i: i["at"] or "", reverse=True)
    out: Json = {"hours": hours, "since": store.iso(since), "at": store.iso(now), "items": items,
                 "storylines": stories, "due": waiting, "scoreboard": board_out, "ideas": filed,
                 "unavailable": unavailable}
    return out
