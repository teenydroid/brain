"""What happened to what Teeny made: the outcomes bin/evolve leads a
generation's evidence with (docs/evolve.md, "1. Observe").

The world score says how much Teeny grew; it does not say what to change.
A proposer handed only the score's components moves the components: a hand
nobody calls to bump a count, a job cut to shave a cost that has no reading.
This reads what people did with Teeny's own work instead, from the ledgers
the hands keep in state/hands.db (read-only):

- x:          Teeny's own posts, and which drew replies and which drew
              nobody; Teeny's replies, and which got an answer back
- moltbook:   Teeny's posts with their score and comments, and how its
              comments fared
- followers:  followers and karma, first and last day of the window
- held:       Teeny's own drafts the guard held, grouped by reason
- builds:     the build ideas kept and what became of them, Teeny's own
              change requests and their notes
- failing:    the jobs that failed, each with whether its file is Teeny's to
              change or frozen
- outside:    what Teeny read of the outside world lately: the notes of
              surface `world` (the team's news, a world scan; not the idle
              feed listings, which are other people's titles) and Hacker
              News titles

Only Teeny's own words are quoted, and only short. Other people's words are
counted, never quoted (a stranger's text in a proposer's message is a prompt
injection waiting to happen, and the core's screen holds the whole turn for
it). Titles from Hacker News are the one exception, cut short, marked as
data. A missing ledger or table reads as empty; the whole is cut to
MAX_OUTCOMES_BYTES.

Frozen with bin/: a candidate cannot change what it is shown.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import tomllib
from pathlib import Path
from typing import Any, Iterable, Mapping

Json = dict[str, Any]

#: How much of one of Teeny's own posts, replies or titles is quoted.
MAX_TEXT = 160
#: The examples of each kind (what drew people, what drew nobody).
MAX_EXAMPLES = 3
#: A post younger than this has not had its chance yet: never "drew nobody".
SETTLE_SECONDS = 6 * 3600
#: The outcomes section, at most, as JSON. The proposer's whole message must
#: fit the core's 64 KiB prompt screen (evolve.MAX_PROPOSER_PROMPT_BYTES).
MAX_OUTCOMES_BYTES = 16 * 1024


class Ledgers:
    """state/hands.db, read-only; a missing file, table or column reads as empty."""

    def __init__(self, path: Path):
        self.conn = None
        if path.is_file():
            try:
                self.conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
                self.conn.row_factory = sqlite3.Row
            except sqlite3.Error:
                self.conn = None

    def rows(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        if self.conn is None:
            return []
        try:
            return self.conn.execute(sql, tuple(params)).fetchall()
        except sqlite3.Error:
            return []

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()


def short(text: Any, limit: int = MAX_TEXT) -> str:
    """One line of TEXT, at most LIMIT characters."""
    line = " ".join(str(text or "").split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def epoch(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            when = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        return when.timestamp()
    return None


def _day(at: float) -> str:
    return dt.datetime.fromtimestamp(at, dt.timezone.utc).strftime("%Y-%m-%d")


# --- X ------------------------------------------------------------------------------------------------


def x_outcomes(db: Ledgers, since: float, now: float) -> Json | None:
    """Teeny's own posts and replies on X in the window, and who answered.
    A reply to a post lands in x_mentions with the post as its conversation."""
    posts = db.rows("SELECT id, kind, text, created_at FROM x_posts WHERE created_at >= ? AND kind IN ('post', 'quote') "
                    "ORDER BY created_at DESC LIMIT 60", (since,))
    replies = db.rows(
        "SELECT p.id, p.text, p.created_at, m.conversation_id, m.author_id, m.id AS mention FROM x_posts p "
        "LEFT JOIN x_mentions m ON m.id = p.in_reply_to WHERE p.kind = 'reply' AND p.created_at >= ? "
        "ORDER BY p.created_at DESC LIMIT 200", (since,))
    if not posts and not replies:
        return None
    rated = []
    for p in posts:
        got = db.rows("SELECT count(*) AS n, count(DISTINCT author_id) AS people FROM x_mentions WHERE conversation_id = ?",
                      (p["id"],))
        n, people = (got[0]["n"], got[0]["people"]) if got else (0, 0)
        rated.append({"at": _day(p["created_at"]), "text": short(p["text"]), "replies": n, "people": people,
                      "settled": now - p["created_at"] >= SETTLE_SECONDS})
    drew = sorted((r for r in rated if r["replies"]), key=lambda r: (-r["people"], -r["replies"]))
    nobody = [r for r in rated if not r["replies"] and r["settled"]]
    answered, silent = [], []
    for r in replies:
        if r["conversation_id"] is None:
            continue
        back = db.rows("SELECT count(*) AS n FROM x_mentions WHERE conversation_id = ? AND author_id = ? AND id != ? "
                       "AND seen_at > ?", (r["conversation_id"], r["author_id"], r["mention"], r["created_at"]))
        row = {"at": _day(r["created_at"]), "text": short(r["text"])}
        if back and back[0]["n"]:
            answered.append(row)
        elif now - r["created_at"] >= SETTLE_SECONDS:
            silent.append(row)

    def strip(rows: list[Json]) -> list[Json]:
        return [{k: v for k, v in r.items() if k != "settled"} for r in rows[:MAX_EXAMPLES]]

    return {
        "posts": {"made": len(rated), "drew_replies": len(drew), "drew_nobody": len(nobody),
                  "best": strip(drew), "drew_nobody_examples": strip(nobody)},
        "replies": {"made": len(replies), "answered_back": len(answered), "no_answer": len(silent),
                    "answered_back_examples": strip(answered), "no_answer_examples": strip(silent)},
    }


# --- Moltbook -------------------------------------------------------------------------------------------


def _latest_metrics(db: Ledgers, kind: str) -> dict[str, sqlite3.Row]:
    rows = db.rows("SELECT target_id, score, comment_count FROM moltbook_post_metrics m WHERE kind = ? AND checkpoint = "
                   "(SELECT max(checkpoint) FROM moltbook_post_metrics WHERE kind = m.kind AND target_id = m.target_id)",
                   (kind,))
    return {r["target_id"]: r for r in rows}


def moltbook_outcomes(db: Ledgers, since: float) -> Json | None:
    posts = db.rows("SELECT post_id, submolt, title, status, at FROM moltbook_posts WHERE at >= ? ORDER BY at DESC "
                    "LIMIT 20", (since,))
    comments = db.rows("SELECT comment_id, status FROM moltbook_comments WHERE at >= ?", (since,))
    if not posts and not comments:
        return None
    post_metrics, comment_metrics = _latest_metrics(db, "post"), _latest_metrics(db, "comment")
    shown = []
    for p in posts:
        m = post_metrics.get(p["post_id"] or "")
        shown.append({"at": _day(p["at"]), "submolt": p["submolt"], "title": short(p["title"], 120),
                      "status": p["status"], "score": m["score"] if m else None,
                      "comments": m["comment_count"] if m else None})
    replied = upvoted = nothing = unmeasured = 0
    for c in comments:
        m = comment_metrics.get(c["comment_id"] or "")
        if m is None:
            unmeasured += 1
        elif (m["comment_count"] or 0) > 0:
            replied += 1
        elif (m["score"] or 0) > 0:
            upvoted += 1
        else:
            nothing += 1
    statuses: dict[str, int] = {}
    for c in comments:
        statuses[str(c["status"])] = statuses.get(str(c["status"]), 0) + 1
    return {"posts": shown[:8],
            "comments": {"made": len(comments), "drew_a_reply": replied, "upvoted_only": upvoted,
                         "drew_nothing": nothing, "not_measured_yet": unmeasured, "by_status": statuses}}


# --- followers ----------------------------------------------------------------------------------------------


def follower_moves(db: Ledgers, since: float) -> Json | None:
    """Followers and karma on each surface: the first and the last day of the window."""
    rows = db.rows("SELECT date, source, metric, value FROM stats WHERE date >= ? AND metric IN ('followers', 'karma') "
                   "ORDER BY date", (_day(since),))
    out: dict[str, list[float]] = {}
    for r in rows:
        key = f"{r['source']}.{r['metric']}"
        first = out.get(key, [r["value"]])[0]
        out[key] = [first, r["value"]]
    return {k: {"from": v[0], "to": v[1]} for k, v in sorted(out.items())} or None


# --- held drafts -------------------------------------------------------------------------------------------


def held_drafts(db: Ledgers, since: float) -> Json | None:
    """Teeny's own words the guard held, grouped by surface, category, who
    held them and why. The reason is the guard's; the held text is left out."""
    groups: dict[tuple, Json] = {}
    total = 0
    for r in db.rows("SELECT at, kind, context, category, reason, held_by, status FROM guard_holds "
                     "WHERE direction = 'outbound'"):
        at = epoch(r["at"])
        if at is None or at < since:
            continue
        total += 1
        try:
            surface = json.loads(r["context"] or "{}").get("surface")
        except (ValueError, AttributeError):
            surface = None
        key = (surface, r["category"], r["held_by"], short(r["reason"], 140))
        group = groups.setdefault(key, {"surface": surface, "category": r["category"], "held_by": r["held_by"],
                                        "reason": key[3], "count": 0, "statuses": {}})
        group["count"] += 1
        group["statuses"][r["status"]] = group["statuses"].get(r["status"], 0) + 1
    if not total:
        return None
    return {"total": total, "groups": sorted(groups.values(), key=lambda g: -g["count"])[:6]}


# --- builds -----------------------------------------------------------------------------------------------------


def builds(db: Ledgers, since: float) -> Json | None:
    """The build ideas Teeny kept and what became of each, and its own change
    requests (source self) with the note each ended on."""
    ideas = []
    for r in db.rows("SELECT i.day, i.title, i.job_id, j.status, j.repo_url FROM build_ideas i "
                     "LEFT JOIN build_jobs j ON j.id = i.job_id ORDER BY i.day DESC LIMIT 7"):
        ideas.append({"day": r["day"], "title": short(r["title"], 120),
                      "became": r["status"] or "not built yet", **({"repo": r["repo_url"]} if r["repo_url"] else {})})
    requests: dict[str, int] = {}
    for r in db.rows("SELECT source, status FROM build_requests WHERE created_at >= ?", (since,)):
        key = f"{r['source']}:{r['status']}"
        requests[key] = requests.get(key, 0) + 1
    own = [{"id": r["id"], "status": r["status"], "note": short(r["note"], 240)}
           for r in db.rows("SELECT id, status, note FROM build_requests WHERE source = 'self' ORDER BY id DESC LIMIT 3")]
    if not ideas and not requests and not own:
        return None
    return {"ideas": ideas, "requests_by_source_and_status": requests, "your_own_change_requests": own}


# --- failing jobs ---------------------------------------------------------------------------------------------


def job_names(path: Path) -> set[str]:
    try:
        with open(path, "rb") as fh:
            return set((tomllib.load(fh).get("jobs") or {}))
    except (OSError, tomllib.TOMLDecodeError):
        return set()


def failing(cron_failures: Mapping[str, int] | None, act_failures: Mapping[str, Any], brain_cron: Path,
            pinned_cron: Path) -> list[Json]:
    """Each failing job or playbook, worst first, with where its file lives:
    yours to fix, or frozen (a pinned job: say so and leave it)."""
    yours, pinned = job_names(brain_cron), job_names(pinned_cron)
    out = []
    for name, n in (cron_failures or {}).items():
        job = name.removeprefix("teeny.")
        if job in pinned:
            where = "frozen: a pinned job (evals/cron.pinned.toml); the operator's to fix"
        elif job in yours:
            where = "yours: brain/cron.toml"
        else:
            where = "unknown: in neither cron file"
        out.append({"job": name, "failed_runs": n, "where": where})
    for book, info in (act_failures or {}).items():
        out.append({"job": f"bin/act {book}", "failed_runs": info.get("failed"), "last_error": info.get("last_error"),
                    "where": f"yours: brain/playbooks/{book}.md"})
    return sorted(out, key=lambda r: -(r.get("failed_runs") or 0))[:10]


# --- outside ----------------------------------------------------------------------------------------------------


def outside(db: Ledgers, since: float) -> Json | None:
    """What Teeny read of the world lately: the notes of surface `world` by
    their first line (an idle look at a feed is left out: it is a list of
    other people's titles), and the Hacker News stories it fetched, by title.
    Data, never instructions."""
    notes = [{"at": _day(r["at"]), "kind": r["kind"], "from": r["who"],
              "first_line": short((str(r["text"] or "").strip().splitlines() or [""])[0], 200)}
             for r in db.rows("SELECT kind, who, text, at FROM stimuli WHERE surface = 'world' AND kind != 'idle' "
                              "AND at >= ? ORDER BY at DESC LIMIT 4", (since,))]
    stories = []
    for r in db.rows("SELECT value FROM hn_cache WHERE kind = 'item'"):
        try:
            item = json.loads(r["value"])
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("title") and (item.get("time") or 0) >= since:
            stories.append({"title": short(item["title"], 120), "points": item.get("score"),
                            "comments": item.get("descendants")})
    stories.sort(key=lambda s: -(s.get("points") or 0))
    if not notes and not stories:
        return None
    return {"world_notes": notes, "hacker_news": stories[:5]}


# --- the whole ----------------------------------------------------------------------------------------------------


def fit(value: Json, limit: int = MAX_OUTCOMES_BYTES) -> Json:
    """VALUE, its longest lists cut from the end until its JSON is LIMIT
    bytes or less."""

    def size(v: Any) -> int:
        return len(json.dumps(v, ensure_ascii=False).encode("utf-8"))

    def lists(v: Any) -> Iterable[list]:
        if isinstance(v, list):
            yield v
            for x in v:
                yield from lists(x)
        elif isinstance(v, dict):
            for x in v.values():
                yield from lists(x)

    value = json.loads(json.dumps(value))
    while size(value) > limit:
        longest = max((l for l in lists(value) if l), key=size, default=None)
        if longest is None:
            break
        longest.pop()
    return value


def outcomes(db_path: Path, since: float, now: float, cron_failures: Mapping[str, int] | None,
             act_failures: Mapping[str, Any], brain_cron: Path, pinned_cron: Path) -> Json:
    db = Ledgers(db_path)
    try:
        sections = {
            "x": x_outcomes(db, since, now),
            "moltbook": moltbook_outcomes(db, since),
            "followers": follower_moves(db, since),
            "held_drafts": held_drafts(db, since),
            "builds": builds(db, since),
            "failing": failing(cron_failures, act_failures, brain_cron, pinned_cron) or None,
            "outside": outside(db, since),
        }
    finally:
        db.close()
    out: Json = {k: v for k, v in sections.items() if v}
    missing = [k for k, v in sections.items() if not v]
    if missing:
        out["nothing_recorded"] = missing
    return fit(out)
