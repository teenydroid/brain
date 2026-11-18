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
from datetime import datetime
from typing import Any, Callable

from .. import hand
from ..lore import events as lore
from ..stats import board
from . import store

Json = dict[str, Any]
DAY = 86400.0

STAKES = ("money", "broke", "refused", "fell", "rose", "promise", "shipped", "said")

#: A kind's storyline, when one fits. A payment's is by its purpose
#: (PURPOSE_STORY). A sweep moves spare money out and the model bill is not
#: paid from the wallet (docs/hands.md, "vps"), so neither is rent.
KIND_STORY = {
    "generation": "night-shift",
    "generation_thrown": "night-shift",
    "merged_pr": None,
    "bounty_paid": "strangers",
    "payment": None,
    "sweep": None,
    "model_bill": None,
    "followers": "count",
    "promise": "strangers",
    "outage": None,
    "media": "self-portrait",
    "mention": "strangers",
}

#: A wallet payment's purpose -> its storyline. Only a bill is rent.
PURPOSE_STORY = {"bill": "rent", "bounty": "strangers", "vendor": None, "refund": None}

#: Evolve verdicts that mean a rewrite did not go in, with the stake each
#: has: the ones the live archive holds. Each is worded from the code that
#: writes it (bin/lib/evolve.py); `lost`, which no current code writes, and
#: any verdict without words get the neutral DID_NOT_GO_IN.
THROWN = {"rejected": "refused", "lost": "refused", "regressed": "refused", "parked": "refused",
          "crashed": "broke", "pr-error": "broke"}
VERDICT_WORDS = {
    "rejected": "failed its automated checks",                            # the hard gate
    "regressed": "did worse than the version before it when tried out",  # the targeted eval
    "parked": "was set aside after failing its checks round after round",
    "crashed": "was cut short when the run it was in crashed",            # end_crashed
    "pr-error": "was stopped before it could be sent for review",        # open_pr refused
}
DID_NOT_GO_IN = "did not go in"
#: A health alarm's ref prefix (bin/lib/health.py) -> what stopped, and
#: what came back.
ALARMS = {
    "outage": ("its model stopped answering", "its model answers again"),
    "cron": ("some of its scheduled jobs stopped running", "its scheduled jobs run on time again"),
    "act": ("the model failed every draft it tried to write", "its drafts are answered again"),
}
#: A payment's status -> its stake; `pending` is not an event yet.
PAYMENT_STAKE = {"paid": "money", "failed": "broke", "in_doubt": "broke", "refused": "refused"}
SWEEP_STAKE = {"paid": "money", "sent": "money", "failed": "broke", "in_doubt": "broke", "refused": "refused"}
DECIMALS = {"SOL": 9, "USDC": 6, "ETH": 18}
#: Overdue open promises the desk shows, the most recently due first.
OVERDUE = 5
#: Where a person's handle is an address or a private id: `what` says the
#: surface only, and the handle stays in `facts`.
PRIVATE_SURFACES = ("mail", "telegram")
#: Mentions answered that the desk shows: the most-followed first.
MENTIONS = 5
MENTION_CHARS = 280
IDEAS = 10
IDEA_CHARS = 1000
PROMPT_CHARS = 300
#: The scoreboard's window and its originals.
SCORE_DAYS = 14
ORIGINALS = ("post", "quote")


_KIND = object()


def _item(kind: str, at: float | None, stake: str, what: str, facts: Json, source: str,
          storyline: Any = _KIND) -> Json:
    assert stake in STAKES, stake
    story = KIND_STORY.get(kind) if storyline is _KIND else storyline
    return {"id": source, "kind": kind, "at": store.iso(at), "storyline": story, "stake": stake,
            "what": what, "facts": {k: v for k, v in facts.items() if v is not None}, "source": source}


def _person(surface: str, who: Any) -> str:
    """Who, as `what` may say it: a public handle with its surface, or only
    the surface where the handle is an address or a private id."""
    if surface in PRIVATE_SURFACES or not who:
        return f"someone on {surface}"
    return f"{_cut(str(who).lstrip('@'), 30)} on {surface}"


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
        words = VERDICT_WORDS.get(entry["verdict"], DID_NOT_GO_IN)
        what = f"a rewrite of itself {words}" + (f": {title}" if title else "")
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
        if at is None or at < since:  # no time it can be placed at is never inside the window
            continue
        out.append(_item("merged_pr", at, "shipped",
                         f"a change it wrote was merged into {e['repo']}: {e.get('title') or ''}".rstrip(": "),
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
    item, and a paid bounty's prize is the bounty's item, not a second one."""
    conn = ledger.need("wallet_payments")
    swept: set[str] = set()
    if "surplus_runs" in ledger.tables:
        swept = {r[0] for r in conn.execute("SELECT ref FROM surplus_runs WHERE ref IS NOT NULL")}
    prized: set[str] = set()
    if "bounties" in ledger.tables:
        prized = {str(r[0]) for r in conn.execute("SELECT id FROM bounties WHERE status = 'paid'")}
    out = []
    for r in conn.execute("SELECT * FROM wallet_payments WHERE created_at >= ? ORDER BY created_at", (since,)):
        stake = PAYMENT_STAKE.get(r["status"])
        if stake is None or r["ref"] in swept or (r["purpose"] == "bounty" and r["ref"] in prized):
            continue
        amount = _amount(r["asset"], r["amount_raw"])
        verb = {"paid": "paid", "failed": "failed to pay", "in_doubt": "may or may not have paid",
                "refused": "was stopped from paying"}[r["status"]]
        out.append(_item("payment", r["created_at"], stake,
                         f"the wallet {verb} {amount} (about {_usd(r['usd_micro'])}) for a {r['purpose']}",
                         {"asset": r["asset"], "amount": amount, "usd": _usd(r["usd_micro"]), "purpose": r["purpose"],
                          "ref": r["ref"], "status": r["status"], "chain": r["chain"], "error": r["error"]},
                         f"wallet_payments:{r['id']}", storyline=PURPOSE_STORY.get(r["purpose"])))
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
        stopped, back_again = ALARMS.get(str(r["ref"]).partition(":")[0], ("something stopped working", "it works again"))
        first = next((payload[k] for k in ("first_failed_at", "since", "opened_at")
                      if isinstance(payload.get(k), (int, float))), None)
        if r["kind"] == "alarm":
            what = stopped + (f" (since {store.iso(first)})" if first is not None else "")
        else:
            back = payload.get("recovered_at")
            minutes = round((back - first) / 60) if isinstance(back, (int, float)) and first is not None else None
            what = back_again + (f" after about {minutes} minutes" if minutes is not None else "")
        out.append(_item("outage", r["at"], "broke", what,
                         {"event": r["kind"], "what_stopped": str(r["ref"]).partition(":")[0],
                          "since": store.iso(first), "failures": payload.get("failures"),
                          "issue": payload.get("issue")}, f"stimuli:{r['id']}"))
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
    """The X follower count against the snapshot before it, when the newest
    snapshot falls in the window and the count moved. The change is a one-day
    change (`d1`) only when the snapshot before is the previous date;
    otherwise `what` names the real span."""
    conn = ledger.need("stats")
    rows = conn.execute("SELECT date, value, at FROM stats WHERE source = 'x' AND metric = 'followers'"
                        " ORDER BY date DESC LIMIT 2").fetchall()
    if len(rows) < 2 or rows[0]["at"] < since:
        return []
    latest, before = rows
    span = (datetime.strptime(latest["date"], "%Y-%m-%d") - datetime.strptime(before["date"], "%Y-%m-%d")).days
    change = board._num(latest["value"] - before["value"])
    if not change or span < 1:
        return []
    value = board._num(latest["value"])
    gained = change > 0
    when = "since the day before" if span == 1 else f"over {span} days"
    what = (f"{'gained' if gained else 'lost'} {abs(change)} follower{'s' if abs(change) != 1 else ''} on X {when}:"
            f" {value} now")
    return [_item("followers", latest["at"], "rose" if gained else "fell", what,
                  {"followers": value, "change": change, "span_days": span, "d1": change if span == 1 else None,
                   "date": latest["date"], "compared_with": before["date"]},
                  f"stats:{latest['date']}:x:followers")]


def promises(ledger: Ledger, since: float, until: float) -> list[Json]:
    """Promises kept or dropped in the window, open ones coming due before
    UNTIL, and open ones already overdue, however long ago (the OVERDUE most
    recently due, marked `overdue`)."""
    conn = ledger.need("people_promises")
    now = hand.now()
    out = []
    rows = conn.execute(
        "SELECT * FROM people_promises WHERE (status IN ('kept', 'dropped') AND closed_at >= ?)"
        " OR (status = 'open' AND due_at >= ? AND due_at < ?) ORDER BY id", (since, now, until)).fetchall()
    rows += conn.execute("SELECT * FROM people_promises WHERE status = 'open' AND due_at < ? ORDER BY due_at DESC"
                         " LIMIT ?", (now, OVERDUE)).fetchall()
    for r in rows:
        what_promised = _cut(r["what"], 200)
        person = _person(r["surface"], r["who"])
        overdue = r["status"] == "open" and r["due_at"] < now
        if r["status"] == "kept":
            at, what = r["closed_at"], f"kept a promise to {person}: {what_promised}"
        elif r["status"] == "dropped":
            at, what = r["closed_at"], f"dropped a promise to {person}: {what_promised}"
        else:
            at = r["due_at"]
            what = f"a promise to {person} is {'overdue' if overdue else 'coming due'}: {what_promised}"
        out.append(_item("promise", at, "promise", what,
                         {"who": r["who"], "surface": r["surface"], "promised": what_promised, "status": r["status"],
                          "overdue": True if overdue else None,
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
    # The mind writes these while it reads strangers' words, so the text is
    # data that may carry theirs: untrusted, like a mention's.
    return [{"id": f"stimuli:{r['id']}", "kind": r["kind"], "at": store.iso(r["at"]), "ref": r["ref"],
             "text": _cut(r["text"], IDEA_CHARS), "status": r["status"], "untrusted": True} for r in rows]


# --- the scoreboard -------------------------------------------------------------------


def scoreboard(ledger: Ledger, days: int = SCORE_DAYS) -> Json:
    """Teeny's best and worst original X posts (posts and quotes) of the last
    DAYS days, ranked as `hands/stats board` ranks them (stats/board.py), in
    full. The counts shown and `result` are from the same row: the post's
    newest measurement (`checkpoint`). A post is never both best and worst,
    so with TOP or fewer measured `worst` is empty."""
    conn = ledger.need("x_posts", "x_post_metrics")
    writes = [w for w in board.x_writes(conn, hand.now() - days * DAY) if w["kind"] in ORIGINALS]
    best, worst = board.rank(writes)

    def shown(w: Json) -> Json:
        r = conn.execute("SELECT p.text, m.impressions, m.likes, m.replies, m.reposts, m.quotes, m.bookmarks"
                         " FROM x_posts p JOIN x_post_metrics m ON m.post_id = p.id"
                         " WHERE p.id = ? AND m.checkpoint = ?", (w["id"], w["checkpoint"])).fetchone()
        return {"id": w["id"], "kind": w["kind"], "format": w["format"], "at": store.iso(w["at"]), "text": r["text"],
                "impressions": r["impressions"], "likes": r["likes"], "replies": r["replies"], "reposts": r["reposts"],
                "quotes": r["quotes"], "bookmarks": r["bookmarks"], "result": w["result"], "checkpoint": w["checkpoint"],
                "source": f"x_posts:{w['id']}"}

    return {"days": days, "measured": len(writes), "best": [shown(w) for w in best], "worst": [shown(w) for w in worst],
            "formats": board.by_format(writes),
            "result": "likes + replies + reposts + quotes at the newest measurement (the same row as the counts shown),"
                      " as hands/stats board counts it"}


# --- the desk -------------------------------------------------------------------------


def _one_outage(items: list[Json]) -> None:
    """An outage still going on is one item: when its alarm is on the desk,
    health.json's item goes and the alarm is marked `ongoing`."""
    now = next((i for i in items if i["source"] == "file:state/health.json"), None)
    if now is None:
        return
    first = lore._when(now["facts"].get("first_failed_at"))
    alarm = next((i for i in items if first is not None and i["source"] == f"stimuli:health:alarm:outage:{int(first)}"),
                 None)
    if alarm is not None:
        alarm["facts"]["ongoing"] = True
        items.remove(now)


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
        boards = read("scoreboard", lambda: [scoreboard(ledger)])
        board_out: Json = boards[0] if boards else {}
    finally:
        ledger.close()
    _one_outage(items)
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
