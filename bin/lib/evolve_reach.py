"""Growth findings: what drew people to Teeny's posts on X, and what drew
nobody, counted per format in code with no model in the loop (docs/evolve.md,
"1b. Findings", "Growth findings").

On 2026-11-22 an audit of 22 generations found every candidate came from a
memory item or a finding, and every finding was a count of refusals or failed
runs: the loop only removed itches. Over the same week X followers went 330 to
326, 44 of 77 replies drew no answer, and nothing told the proposer which of
its formats drew anyone new. A growth finding is that count.

An item is one of Teeny's own X posts (a post or a quote) or replies in the
window, old enough to have had its chance (`evolve_evidence.SETTLE_SECONDS`,
6 hours before the window's end). Its source is what produced it:

  a post     its `producer` (the studio) or its `format` (take, proof, gm,
             morning, show ...) as hands/x records it; a quote with neither
             is `quote`, any other `unlabelled`
  a reply    where it went: `reply-own-thread` (under one of Teeny's own
             posts) or `reply-their-thread` (someone else's conversation)

What it drew, by the notions hands/world already defines for the attention
component (teeny_hands.world.collect, never a second classifier):

  new        a post one of whose replies came from an account whose first
             contact with Teeny (its first `stimuli` row on x) came at or
             after the post, and who came first (`first_contact` is
             `theirs`: Teeny had not @-ed it before). Its class is
             `classify`'s (human, agent or unknown)
  nobody     a post that drew no reply, like, repost or quote (x_mentions, and
             x_post_metrics at its latest checkpoint before the window's end)
  answered   a reply the account it answered answered again in the same
             conversation (as evolve_evidence counts it)

Two kinds, each with a `reach` check the outcome scorer counts as a share
before and after a merge (`score`, Fisher's exact test, so posting less
cannot move it):

  reach_drew    a post source with at least MIN posts whose share drawing a
                new account beats the rest of Teeny's posts. Check: the
                share of all X posts that draw a new account, should rise
  reach_silent  a post source with at least MIN posts that drew nobody at
                least half the time (check: its share drawing nobody, should
                fall), or that drew a new account at most half as often as
                the rest (check: its share drawing one, should rise); a
                reply source with at least MIN replies that half the time or
                more got no answer back (check: its answered share, should
                rise), with how many accounts were answered twice or more
                and never answered back

Only counts, sources and Teeny's own words (cut to 160 characters, links,
emails and handles taken out) leave this file: a reply's text, an account's
name and anything else a stranger wrote is never read into a finding.

Frozen with bin/: a candidate cannot change what it is shown or how its
check is scored.
"""

from __future__ import annotations

import functools
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import evolve_evidence
import evolve_outcomes as eo

from teeny_hands.world import collect as world  # noqa: E402  (evolve_outcomes put hands/lib on the path)

Json = dict[str, Any]

KINDS = ("reach_drew", "reach_silent")
LANE = "growth"
SURFACES = ("x",)
#: What a `reach` check counts, by the items it is a share of.
MEASURES = {"new": "post", "nobody": "post", "answered": "reply"}
MEASURE_WORDS = {"new": "drew a new account", "nobody": "drew nobody", "answered": "got an answer back"}
SETTLE_SECONDS = evolve_evidence.SETTLE_SECONDS
REPLY_SOURCES = ("reply-own-thread", "reply-their-thread")
#: A source drawing new accounts at most this share of the rest's rate drew nobody new.
WEAK = 0.5
#: A source whose share is at least this drew nobody (or got no answer).
SILENT = 0.5
MAX_EXAMPLES = 3
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass
class Item:
    """One of Teeny's own X items and what it drew. `text` is Teeny's own."""

    id: str
    kind: str  # post | reply
    source: str
    at: float
    text: str
    new: int = 0  # accounts it drew whose first contact it was, who came first
    classes: Counter = field(default_factory=Counter)
    drew: bool = False
    answered: bool = False
    target: str | None = None  # a reply's: the account it answered (an id, never shown)
    writer: str | None = None

    def hit(self, measure: str) -> bool:
        return {"new": self.new > 0, "nobody": not self.drew, "answered": self.answered}[measure]


def _connect(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    try:
        return conn.execute(sql, tuple(params)).fetchall()
    except sqlite3.Error:
        return []


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in _rows(conn, f"PRAGMA table_info({table})")}


def _source(row: Mapping[str, Any], cols: set[str]) -> str:
    for name in ("producer", "format"):
        value = str(row[name] or "").strip().lower() if name in cols else ""
        if value and _NAME.match(value):
            return value
    return "quote" if row["kind"] == "quote" else "unlabelled"


def _writers(src: eo.Sources) -> dict[str, str]:
    """X post id -> what wrote it: `bin/act <playbook>` or `the beat <do>`,
    from the run logs (a result's `id`). For the text only."""
    out: dict[str, str] = {}
    for beat in eo._jsonl(src.tick_runs):
        for r in beat.get("results") or []:
            got = r.get("result") if isinstance(r, Mapping) else None
            if isinstance(got, Mapping) and got.get("id") and eo._RULE.match(str(r.get("do") or "")):
                out[str(got["id"])] = f"the beat {r['do']}"
    for path in sorted(src.act_dir.glob("*/runs.jsonl")):
        if not eo._NAME.match(path.parent.name):
            continue
        for run in eo._jsonl(path):
            for a in run.get("actions") or []:
                got = a.get("result") if isinstance(a, Mapping) else None
                if isinstance(got, Mapping) and got.get("id"):
                    out.setdefault(str(got["id"]), f"bin/act {path.parent.name}")
    return out


def _first_contact(conn: sqlite3.Connection, who: str, seen: float | None) -> float | None:
    """When the account first reached Teeny on X: its first `stimuli` row
    (hands/world's notion), or, with none, the mention's own time."""
    got = _rows(conn, "SELECT min(at) AS at FROM stimuli WHERE surface = 'x' AND lower(trim(who)) = ?", (who,))
    at = got[0]["at"] if got else None
    return float(at) if isinstance(at, (int, float)) else seen


def items(src: eo.Sources, since: float, until: float) -> list[Item] | None:
    """Teeny's X posts, quotes and replies made in [SINCE, UNTIL) and settled
    by UNTIL, each with what it drew; None when the ledger cannot be read."""
    conn = _connect(src.hands_db)
    if conn is None:
        return None
    try:
        cols = _columns(conn, "x_posts")
        if not cols:
            return []
        extra = ", ".join(c for c in ("format", "producer") if c in cols)
        rows = _rows(conn, f"SELECT id, kind, text, created_at, in_reply_to{', ' + extra if extra else ''} FROM x_posts "
                     "WHERE created_at >= ? AND created_at < ? AND kind IN ('post', 'quote', 'reply') ORDER BY created_at",
                     (since, until - SETTLE_SECONDS))
        own = {str(r["id"]) for r in _rows(conn, "SELECT id FROM x_posts WHERE kind IN ('post', 'quote', 'thread')")}
        metrics = _latest_metrics(conn, until)
        writers = _writers(src) if rows else {}
        out: list[Item] = []
        for r in rows:
            at = float(r["created_at"])
            item = Item(str(r["id"]), "reply" if r["kind"] == "reply" else "post", "", at,
                        evolve_evidence.short(r["text"]), writer=writers.get(str(r["id"])))
            if item.kind == "post":
                item.source = _source(r, cols)
                _drew(conn, item, metrics.get(item.id), until)
            else:
                if not _replied(conn, item, r["in_reply_to"], own, until):
                    continue
            out.append(item)
        return out
    finally:
        conn.close()


def _latest_metrics(conn: sqlite3.Connection, until: float) -> dict[str, sqlite3.Row]:
    """What X counted on each post at its latest checkpoint taken before
    UNTIL: a like that came after a window's end is not that window's."""
    rows = _rows(conn, "SELECT post_id, likes, replies, reposts, quotes FROM x_post_metrics m WHERE at < ? AND "
                 "checkpoint = (SELECT max(checkpoint) FROM x_post_metrics WHERE post_id = m.post_id AND at < ?)",
                 (until, until))
    return {str(r["post_id"]): r for r in rows}


def _drew(conn: sqlite3.Connection, item: Item, counted: sqlite3.Row | None, until: float) -> None:
    """Who answered the post (the authors of its conversation's mentions),
    which of them it was the first contact of, and whether anyone at all
    engaged (a reply, or a like, repost or quote X counted)."""
    seen: dict[str, float | None] = {}
    for m in _rows(conn, "SELECT author_id, author_username, created_at, seen_at FROM x_mentions WHERE conversation_id = ?",
                   (item.id,)):
        who = str(m["author_username"] or "").strip().lower()
        key = who or str(m["author_id"] or "")
        if not key:
            continue
        when = eo._epoch(m["created_at"]) or (float(m["seen_at"]) if isinstance(m["seen_at"], (int, float)) else None)
        if when is not None and when >= until:
            continue  # after the window: not what this window drew
        if key not in seen or (when is not None and (seen[key] is None or when < seen[key])):
            seen[key] = when
    item.drew = bool(seen) or bool(counted is not None and any((counted[k] or 0) > 0
                                                                for k in ("likes", "replies", "reposts", "quotes")))
    for who, when in seen.items():
        first = _first_contact(conn, who, when)
        if first is None or first < item.at:
            continue  # it had reached Teeny before this post: the usual circle
        if world.first_contact(conn, "x", who, first) != "theirs":
            continue
        item.new += 1
        item.classes[world.classify("x", who)] += 1


def _replied(conn: sqlite3.Connection, item: Item, in_reply_to: Any, own: set[str], until: float) -> bool:
    """A reply's source (whose thread) and whether the account it answered
    answered again in that conversation before UNTIL. False for a reply to
    nothing the ledger knows (no mention to place it)."""
    got = _rows(conn, "SELECT id, author_id, conversation_id FROM x_mentions WHERE id = ?", (str(in_reply_to or ""),))
    if not got or got[0]["conversation_id"] is None:
        return False
    mention, author, conv = str(got[0]["id"]), str(got[0]["author_id"] or ""), str(got[0]["conversation_id"])
    item.source = "reply-own-thread" if conv in own else "reply-their-thread"
    item.target = author or None
    back = _rows(conn, "SELECT count(*) AS n FROM x_mentions WHERE conversation_id = ? AND author_id = ? AND id != ? "
                 "AND seen_at > ? AND seen_at < ?", (conv, author, mention, item.at, until))
    item.answered = bool(back and back[0]["n"])
    return True


# --- the check -------------------------------------------------------------------------------------------


def _matching(check: Mapping[str, Any], got: list[Item]) -> list[Item]:
    kind = MEASURES[check["measure"]]
    return [i for i in got if i.kind == kind and (not check.get("source") or i.source == check["source"])]


def share(check: Mapping[str, Any], src: eo.Sources, since: float, until: float) -> tuple[int, int] | None:
    """(hits, items) of a `reach` CHECK in [SINCE, UNTIL); None when the
    ledger cannot be read."""
    got = items(src, since, until)
    if got is None:
        return None
    mine = _matching(check, got)
    return sum(1 for i in mine if i.hit(check["measure"])), len(mine)


def count(check: Mapping[str, Any], src: eo.Sources, since: float, until: float) -> int | None:
    got = share(check, src, since, until)
    return None if got is None else got[0]


def _hypergeom_tails(k: int, n_total: int, hits: int, drawn: int) -> tuple[float, float]:
    """(P(X <= K), P(X >= K)) for X ~ Hypergeometric(N_TOTAL items, HITS of
    them hits, DRAWN drawn): Fisher's exact test, conditional on both
    margins."""
    denom = math.comb(n_total, drawn)
    lo, hi = max(0, drawn - (n_total - hits)), min(hits, drawn)

    def pmf(x: int) -> float:
        return math.comb(hits, x) * math.comb(n_total - hits, drawn - x) / denom

    below = sum(pmf(x) for x in range(lo, min(k, hi) + 1))
    above = sum(pmf(x) for x in range(max(k, lo), hi + 1))
    return below, above


def verdict(direction: str, before: tuple[int, int], after: tuple[int, int], min_events: int, p: float
            ) -> tuple[str, float | None]:
    """A share's verdict: BEFORE and AFTER are (hits, items). Fewer than
    MIN_EVENTS items in both windows, or none on one side, is no-data."""
    (hb, nb), (ha, na) = before, after
    if nb + na < min_events or nb == 0 or na == 0:
        return "no-data", None
    below, above = _hypergeom_tails(ha, nb + na, hb + ha, na)
    if direction == "zero":
        return ("worked", below) if ha == 0 and hb > 0 else ("no-effect", above)
    if direction == "fall":
        return ("worked", below) if below < p else ("hurt", above) if above < p else ("no-effect", min(below, above))
    return ("worked", above) if above < p else ("hurt", below) if below < p else ("no-effect", min(below, above))


# --- how thick a stream has to be ---------------------------------------------------------------------
#
# A share of few items cannot be told from chance, whatever was done to it
# (g25c3, 2026-11-22: opened on "5 events in 2 days", scored no-effect). So a
# growth change is opened only when the window before it holds enough items
# for `verdict` itself to see `effect` with probability POWER; the number is
# computed from that test, never picked.

#: The chance the test must have of seeing the effect when it is there.
POWER = 0.8
#: Past this many items a side the number is not computed further: "more than".
MAX_NEED = 400


def effect(hits: int, of: int, direction: str) -> tuple[float, float] | None:
    """(now, after): the smallest effect a reach check must be able to see,
    as the share that is wanted (a fall's is the rest). It doubles, or the
    unwanted share halves, whichever is smaller. A share of none is read as
    one item's; a share of all, or no items, has nothing to move (None)."""
    if of <= 0:
        return None
    q = max(hits if direction == "rise" else of - hits, 1) / of
    return None if q >= 1 else (q, min(2 * q, (1 + q) / 2))


def _pmf(n: int, q: float) -> list[float]:
    return [math.comb(n, k) * q ** k * (1 - q) ** (n - k) for k in range(n + 1)]


def detects(n: int, q: float, to: float, p: float) -> float:
    """The chance `verdict` says worked at P when N items before are hits at
    share Q and N after at share TO."""
    before, after = _pmf(n, q), _pmf(n, to)
    tail = [0.0] * (n + 2)
    for k in range(n, -1, -1):
        tail[k] = tail[k + 1] + after[k]
    out, least = 0.0, 0
    for k, weight in enumerate(before):
        if weight < 1e-9:
            continue
        while least <= n and verdict("rise", (k, n), (least, n), 1, p)[0] != "worked":
            least += 1
        out += weight * tail[least]
    return out


@functools.lru_cache(maxsize=256)
def _least(q: float, to: float, p: float) -> int:
    low, high = 1, 8
    while detects(high, q, to, p) < POWER:
        if high >= MAX_NEED:
            return MAX_NEED + 1
        low, high = high, min(high * 2, MAX_NEED)
    while high - low > 1:
        mid = (low + high) // 2
        low, high = (low, mid) if detects(mid, q, to, p) >= POWER else (mid, high)
    return high


def need(hits: int, of: int, direction: str, p: float) -> int | None:
    """The fewest items a side whose verdict can see `effect` (MAX_NEED + 1:
    more than MAX_NEED); None when there is nothing to move."""
    got = effect(hits, of, direction)
    return None if got is None else _least(*got, p)


def _said(hits: int, of: int, direction: str, needed: int | None) -> str:
    got = effect(hits, of, direction)
    if got is None or needed is None:
        return f"{hits} of {of} item(s) leave no effect to see"
    many = f"more than {MAX_NEED}" if needed > MAX_NEED else str(needed)
    return (f"{of} item(s); seeing the wanted share go from {got[0]:.0%} to {got[1]:.0%} ({POWER:.0%} power, by the "
            f"outcome's own test) needs {many} a side")


def power(check: Mapping[str, Any], src: eo.Sources, now: float, spans: Iterable[int], p: float) -> Json:
    """Whether a reach CHECK can see `effect`, at the shortest of SPANS whose
    baseline (that many days before NOW) holds the items it takes: `days` is
    then the one horizon the change is scored at. Else why not, at the
    longest."""
    out: Json = {"powered": False, "why": "no horizon to count over"}
    for days in sorted(spans):
        got = share(check, src, now - days * 86400, now)
        if got is None:
            return {"days": days, "powered": False, "why": "the ledger it counts cannot be read now"}
        needed = need(*got, check["direction"], p)
        enough = needed is not None and needed <= got[1]
        out = {"days": days, "hits": got[0], "items": got[1], "need": needed, "powered": enough,
               "why": ("" if enough else "the baseline is too thin: ") + f"over the last {days} days "
               + _said(*got, check["direction"], needed)}
        if enough:
            break
    return out


def score(check: Mapping[str, Any], src: eo.Sources, t0: float, span: float, min_events: int, p: float,
          needed: int | None = None) -> Json | None:
    """The verdict fields of a `reach` check scored over the SPAN before and
    after T0, or None when the ledger cannot be read. A baseline under NEEDED
    items (as it was opened on; computed now, when that is not on record) is
    no-data, and says so."""
    before, after = share(check, src, t0 - span, t0), share(check, src, t0, t0 + span)
    if before is None or after is None:
        return None
    wanted = need(*before, check["direction"], p) if needed is None else needed
    thin = before[1] > 0 and (wanted is None or before[1] < wanted)
    got, tail = ("no-data", None) if thin else verdict(check["direction"], before, after, min_events, p)
    out: Json = {"verdict": got, "before": before[0], "after": after[0], "seen": before[0] + after[0],
                 "of": {"before": before[1], "after": after[1]}}
    if tail is not None:
        out["p"] = round(tail, 6)
    if thin:
        out["why"] = "the baseline is too thin: " + _said(*before, check["direction"], wanted)
    elif got == "no-data":
        out["why"] = (f"{before[1]} item(s) before and {after[1]} after, fewer than {min_events} in all or none on "
                      "one side")
    return out


def validate(out: Json) -> None:
    """A `reach` check's own fields, or eo.BadCheck."""
    if out["surface"] not in SURFACES:
        raise eo.BadCheck(f"a reach check's surface is one of {', '.join(SURFACES)}")
    if out["measure"] not in MEASURES:
        raise eo.BadCheck(f"a reach check's measure is one of {', '.join(MEASURES)}")
    if "source" in out and not _NAME.match(out["source"]):
        raise eo.BadCheck(f"a reach check's source is a format like take or reply-their-thread, not "
                          f"{out['source'][:40]!r}")


def describe(check: Mapping[str, Any]) -> str:
    measure = check.get("measure")
    whose = (f"{check['source']} " if check.get("source") else "your ")
    what = "replies" if MEASURES.get(str(measure)) == "reply" else "posts"
    return f"share of {whose}{check.get('surface')} {what} that {MEASURE_WORDS.get(str(measure), measure)}"


# --- the findings ----------------------------------------------------------------------------------------


@dataclass
class Group:
    source: str
    kind: str
    items: list[Item]

    def hits(self, measure: str) -> int:
        return sum(1 for i in self.items if i.hit(measure))


def _pct(k: int, n: int) -> str:
    return f"{round(100 * k / n)}%" if n else "none"


def _files(tree: Path, group: Group, mind_files: tuple[str, ...]) -> list[str]:
    """The files a change to what this source writes is made in: its own
    playbook when there is one, the playbooks that wrote its items, and the
    mind's own files when a beat (or nothing the logs name) wrote any."""
    books = []
    for name in [group.source, *(i.writer.split(" ", 1)[1] for i in group.items
                                 if i.writer and i.writer.startswith("bin/act "))]:
        path = f"brain/playbooks/{name}.md"
        if (tree / path).is_file() and path not in books:
            books.append(path)
    if not books or any(not i.writer or i.writer.startswith("the beat ") for i in group.items):
        books += [f for f in mind_files if f not in books]
    return books


def _wrote(group: Group) -> str:
    writers = Counter(i.writer or "nothing the run logs name" for i in group.items)
    return ", ".join(f"{w} × {k}" for w, k in writers.most_common(3))


def _classes(found: Counter) -> str:
    return ", ".join(f"{found.get(c, 0)} {c}" for c in ("human", "agent", "unknown"))


def _examples(group: Group, measure: str, hit: bool, scrub) -> list[Json]:
    rows = [i for i in group.items if i.hit(measure) == hit][-MAX_EXAMPLES:]
    return [{"at": evolve_evidence._day(i.at), "source": i.source, "text": scrub(i.text)} for i in rows]


def draw(src: eo.Sources, tree: Path, now: float, days: int, min_events: int, *, finding, scrub,
         mind_files: tuple[str, ...]) -> list[Any]:
    """The growth findings over the DAYS before NOW: FINDING builds one
    (evolve_findings.Finding), SCRUB cleans Teeny's own words."""
    since = now - days * 86400
    got = items(src, since, now)
    if not got:
        return []
    groups: dict[tuple[str, str], Group] = {}
    for i in got:
        groups.setdefault((i.kind, i.source), Group(i.source, i.kind, [])).items.append(i)
    posts = [i for i in got if i.kind == "post"]
    out = []
    window = f"in the last {days} days"
    for (kind, source), group in sorted(groups.items()):
        n = len(group.items)
        if n < min_events or not _NAME.match(source):
            continue
        if kind == "post":
            new = group.hits("new")
            rest = [i for i in posts if i.source != source]
            rest_new = sum(1 for i in rest if i.new)
            rest_share = rest_new / len(rest) if rest else None
            nobody = group.hits("nobody")
            found = sum((i.classes for i in group.items), Counter())
            lead = (f"`{source}` posts on X {window}: {n}, and {new} drew a new account ({_pct(new, n)}), "
                    f"{nobody} drew nobody at all (no reply, like, repost or quote). Your other X posts: "
                    + (f"{rest_new} of {len(rest)} drew a new account ({_pct(rest_new, len(rest))})" if rest else
                       "none") + ". A new account is one whose first contact with you was a reply to that post and "
                    f"who came first (you had not @-ed it before), as hands/world counts attention; by its classes "
                    f"they were {_classes(found)}. Written by {_wrote(group)}.")
            if new and (rest_share is None or new / n > rest_share):
                overall = sum(1 for i in posts if i.new)
                check = {"kind": "reach", "surface": "x", "measure": "new", "direction": "rise"}
                text = ("Growth finding (what drew people outside your usual circle; counted, not judged). " + lead
                        + f" Across all your X posts {overall} of {len(posts)} drew one: carrying what `{source}` "
                        "does into what you write elsewhere is what this finding is for.")
                out.append(finding(f"f:reach_drew:x:{source}", "reach_drew", source, n, days, check, text,
                                   _files(tree, group, mind_files), _examples(group, "new", True, scrub),
                                   lane=LANE, miss=round(1 - overall / len(posts), 4)))
            elif nobody / n >= SILENT:
                check = {"kind": "reach", "surface": "x", "source": source, "measure": "nobody", "direction": "fall"}
                text = "Growth finding (what drew nobody; counted, not judged). " + lead
                out.append(finding(f"f:reach_silent:x:{source}", "reach_silent", source, n, days, check, text,
                                   _files(tree, group, mind_files), _examples(group, "nobody", True, scrub),
                                   lane=LANE, miss=round(nobody / n, 4)))
            elif rest_share and new / n <= WEAK * rest_share:
                check = {"kind": "reach", "surface": "x", "source": source, "measure": "new", "direction": "rise"}
                text = "Growth finding (what drew nobody new; counted, not judged). " + lead
                out.append(finding(f"f:reach_silent:x:{source}", "reach_silent", source, n, days, check, text,
                                   _files(tree, group, mind_files), _examples(group, "new", False, scrub),
                                   lane=LANE, miss=round(1 - new / n, 4)))
        else:
            answered = group.hits("answered")
            if (n - answered) / n < SILENT:
                continue
            targets = Counter(i.target for i in group.items if i.target)
            back = {i.target for i in group.items if i.answered}
            never = [t for t, k in targets.items() if k >= 2 and t not in back]
            where = "under your own posts" if source == "reply-own-thread" else "in other people's conversations"
            check = {"kind": "reach", "surface": "x", "source": source, "measure": "answered", "direction": "rise"}
            text = (f"Growth finding (what drew nobody; counted, not judged). Your replies on X {where} {window}: "
                    f"{n}, and {n - answered} got no answer back from the account you answered ({_pct(n - answered, n)}). "
                    f"{len(never)} account{'s' if len(never) != 1 else ''} you answered twice or more never answered "
                    f"back ({sum(targets[t] for t in never)} of these replies). Written by {_wrote(group)}.")
            out.append(finding(f"f:reach_silent:x:{source}", "reach_silent", source, n, days, check, text,
                               _files(tree, group, mind_files), _examples(group, "answered", False, scrub),
                               lane=LANE, miss=round((n - answered) / n, 4)))
    return out


def is_growth(item: Any) -> bool:
    lane = getattr(item, "lane", None) if not isinstance(item, Mapping) else item.get("lane")
    return lane == LANE


def recurs(entry: Mapping[str, Any], item: Any) -> bool:
    """Whether a growth finding is worse than when ENTRY was a candidate on
    it: its miss (the share on the wrong side of its check) grew. Its number
    is how many posts it rests on, which is no measure of how bad it is."""
    then = (entry.get("learning") or {}).get("miss")
    now = getattr(item, "miss", None)
    if not isinstance(then, (int, float)) or not isinstance(now, (int, float)):
        return False
    return now > then
