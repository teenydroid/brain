"""How far each countable proof in DIRECTIVE.md has got (docs/roadmap.md, A0).

    python3 bin/lib/proofs.py [--text] [--only ID ...]

DIRECTIVE.md ticks an item only when its proof exists on the box. Most of
those proofs are rows the hands already keep, so how far along each one is
can be counted instead of guessed. This reads, never writes:

- state/hands.db, the hands' ledgers, opened read-only (a table that does
  not exist yet counts as nothing);
- state/diary/<date>.md, the nightly diary pages (bin/reflect);
- state/evolve/archive.jsonl, bin/evolve's archive of generations;
- brain/narrative/chronicle/gen-<n>.md, the chronicle pages.

Nothing under secrets/ is read, no hand is called and no model is asked: a
report is the same whoever runs it. Every proof is one entry in PROOFS: an
id (`L<level>.<name>`), the DIRECTIVE item it belongs to, what it counts,
and `need`, the number the proof asks for. The report says for each one
what it `has`, and a `status`:

- `met`: the ledger shows the proof (the operator still ticks the item, in
  a PR that links the proof);
- `progress`: some of it, not all;
- `none`: nothing yet.

A proof whose last step lives outside the ledgers (a PR merged upstream, a
human reading the result) is never `met` here: its `note` says what is left
to look at. The DIRECTIVE items no ledger can count yet are listed under
`not_counted`, with what they wait for.

Other frozen code reads the counts through `report()` and `count()` (the
autonomy ladder, docs/roadmap.md G2; the firsts calendar, P3).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hands" / "lib"))
#: The one near-duplicate measure, shared with the posting rule that refuses
#: a repeat (docs/roadmap.md, A2).
from teeny_hands.guard import repeats  # noqa: E402

DAY = 86400
NEAR_DUPLICATE = repeats.NEAR_DUPLICATE
#: The follower-like numbers bin/stats-snapshot keeps whose sum is the
#: audience (DIRECTIVE.md, level 2, "Grows an audience").
AUDIENCE = (("x", "followers"), ("moltbook", "followers"), ("moltbook", "karma"),
            ("github", "followers"), ("github", "stars"))
#: Moltbook writes that went out and can be seen (teeny_hands.moltbook.rules.ACCEPTED).
MOLTBOOK_VISIBLE = ("posted", "verified")
#: The upstream repository Teeny contributes to (DIRECTIVE.md, level 3).
UPSTREAM = "tinyhumansai/openhuman"
GEN_BRANCH = "teeny/gen-"
REVERT_BRANCH = "teeny/revert-gen-"

Json = dict[str, Any]


# --- where the ledgers are ---------------------------------------------------------------


def root() -> Path:
    return Path(os.environ.get("TEENY_ROOT") or Path(__file__).resolve().parents[2])


def state_dir() -> Path:
    return Path(os.environ.get("TEENY_STATE") or root() / "state")


def brain_dir() -> Path:
    return Path(os.environ.get("OPENHUMAN_WORKSPACE") or root() / "brain")


def now() -> float:
    """TEENY_NOW pins the clock, as it does for the hands."""
    pinned = os.environ.get("TEENY_NOW")
    return float(pinned) if pinned else dt.datetime.now(dt.timezone.utc).timestamp()


class Ledgers:
    """state/hands.db, read-only. A missing file or table reads as empty."""

    def __init__(self, path: Path):
        self.conn: sqlite3.Connection | None = None
        if path.is_file():
            self.conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
            self.conn.row_factory = sqlite3.Row

    def rows(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        if self.conn is None:
            return []
        try:
            return self.conn.execute(sql, tuple(params)).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc) or "no such column" in str(exc):
                return []
            raise

    def one(self, sql: str, params: Iterable[Any] = ()) -> Any:
        got = self.rows(sql, params)
        return got[0][0] if got else None

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()


# --- small helpers -------------------------------------------------------------------------


def _day(at: float) -> dt.date:
    return dt.datetime.fromtimestamp(at, dt.timezone.utc).date()


def _streak(days: set[dt.date], today: dt.date) -> int:
    """Days in a row with something, ending today or yesterday (today may
    still be under way)."""
    start = today if today in days else today - dt.timedelta(days=1)
    n = 0
    while start - dt.timedelta(days=n) in days:
        n += 1
    return n


similarity = repeats.similarity


def near_duplicates(texts: list[tuple[float, str]], threshold: float = NEAR_DUPLICATE) -> list[tuple[float, float]]:
    """Each text at least `threshold` alike to an earlier one, as (its time,
    the earlier one's time)."""
    seen: list[tuple[float, str]] = []
    hits = []
    for at, text in sorted(texts):
        for before, old in seen:
            if similarity(old, text) >= threshold:
                hits.append((at, before))
                break
        seen.append((at, text))
    return hits


def _detail(raw: Any) -> Json:
    try:
        value = json.loads(raw) if isinstance(raw, str) else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _archive() -> list[Json]:
    """bin/evolve's archive, each entry as its latest line (append-only)."""
    path = state_dir() / "evolve" / "archive.jsonl"
    entries: dict[str, Json] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("id") is not None:
            entries[str(entry["id"])] = {**entries.get(str(entry["id"]), {}), **entry}
    return list(entries.values())


def _weekly_rises(points: list[tuple[dt.date, float]], today: dt.date) -> int:
    """Weeks in a row, back from today, whose last value beat the week
    before's. A week with no value ends the run."""
    def at(day: dt.date) -> float | None:
        before = [v for d, v in points if d <= day]
        return before[-1] if before else None

    points = sorted(points)
    n = 0
    end = today
    while True:
        this, last = at(end), at(end - dt.timedelta(days=7))
        if this is None or last is None or not this > last:
            return n
        if not any(end - dt.timedelta(days=7) < d <= end for d, _ in points):
            return n
        n += 1
        end -= dt.timedelta(days=7)


# --- the proofs ------------------------------------------------------------------------------
#
# Each counter takes (ledgers, now) and returns (has, note). `note` says
# what the number is, or what the ledger cannot show.

Counter = Callable[[Ledgers, float], tuple[float, str]]


def health_alarm(db: Ledgers, t: float) -> tuple[float, str]:
    rows = db.rows("SELECT payload FROM stimuli WHERE surface = 'health' AND kind = 'recovered'")
    closed = sum(1 for r in rows if isinstance(_detail(r["payload"]).get("issue"), int))
    return closed, "alarms whose issue the health check opened and closed again (state/hands.db stimuli)"


def gh_inbox(db: Ledgers, t: float) -> tuple[float, str]:
    came = db.one("SELECT count(*) FROM stimuli WHERE surface = 'gh'") or 0
    answered = db.one("SELECT count(*) FROM gh_actions WHERE verb = 'comment' AND external = 0") or 0
    return min(came, answered), f"{came} GitHub stimuli in the inbox, {answered} comment(s) on Teeny's own repos"


def no_repeats(db: Ledgers, t: float) -> tuple[float, str]:
    since = t - 30 * DAY
    rows = db.rows("SELECT created_at, text FROM x_posts WHERE kind = 'post' AND created_at >= ?", (since,))
    if not rows:
        return 0, "no posts in the last 30 days"
    first = db.one("SELECT min(created_at) FROM x_posts WHERE kind = 'post'") or t
    days = min(30, int((t - first) // DAY))
    dupes = near_duplicates([(r["created_at"], r["text"]) for r in rows])
    if dupes:
        return 0, f"{len(dupes)} near-duplicate post(s) in the last 30 days; the run starts again after the last"
    return days, f"days of posting with no near-duplicate (Jaccard >= {NEAR_DUPLICATE}), of the last 30"


def x_replies(db: Ledgers, t: float) -> tuple[float, str]:
    n = db.one("SELECT count(*) FROM x_posts WHERE kind = 'reply'") or 0
    return n, "replies on X in the ledger (x_posts; a dry run writes none). Guard escapes are read by a person"


def mail_started(db: Ledgers, t: float) -> tuple[float, str]:
    n = db.one("SELECT count(*) FROM mail_sent WHERE kind = 'send'") or 0
    return n, "mails Teeny started (mail_sent, kind send); that one began a thread with a vendor is read by a person"


def moltbook_week(db: Ledgers, t: float) -> tuple[float, str]:
    marks = ",".join("?" * len(MOLTBOOK_VISIBLE))
    days: set[dt.date] = set()
    for table in ("moltbook_posts", "moltbook_comments"):
        for r in db.rows(f"SELECT at FROM {table} WHERE status IN ({marks})", MOLTBOOK_VISIBLE):
            days.add(_day(r["at"]))
    return _streak(days, _day(t)), "days in a row with a visible Moltbook post or comment"


def audience(db: Ledgers, t: float) -> tuple[float, str]:
    totals: dict[dt.date, float] = {}
    keys = {f"{s}.{m}" for s, m in AUDIENCE}
    for r in db.rows("SELECT date, source, metric, value FROM stats"):
        if f"{r['source']}.{r['metric']}" in keys:
            day = dt.date.fromisoformat(r["date"])
            totals[day] = totals.get(day, 0.0) + float(r["value"])
    if not totals:
        return 0, "no stats rows yet (bin/stats-snapshot)"
    rises = _weekly_rises(list(totals.items()), _day(t))
    return rises, "weeks in a row the audience total rose (" + ", ".join(sorted(keys)) + ")"


def browser_week(db: Ledgers, t: float) -> tuple[float, str]:
    days = {_day(r["finished_at"]) for r in db.rows(
        "SELECT finished_at FROM browser_tasks WHERE status = 'ok' AND finished_at IS NOT NULL")}
    return _streak(days, _day(t)), "days in a row with a browser task that finished ok"


def teeny_tasks(db: Ledgers, t: float) -> tuple[float, str]:
    n = db.one("SELECT count(*) FROM browser_tasks WHERE source = 'teeny' AND status = 'ok'") or 0
    return n, "browser tasks Teeny wrote itself (--source teeny) that finished ok"


def code_login(db: Ledgers, t: float) -> tuple[float, str]:
    n = db.one("SELECT count(*) FROM browser_tasks WHERE code_asked_at IS NOT NULL AND status = 'ok'") or 0
    return n, "tasks that waited for a mailed code and then finished ok"


def needs_human_rate(db: Ledgers, t: float) -> tuple[float, str]:
    rows = db.rows("SELECT status FROM browser_tasks WHERE finished_at >= ? AND status IN ('ok', 'failed', 'needs_human')",
                   (t - 30 * DAY,))
    if len(rows) < 20:
        return 0, f"{len(rows)} task(s) finished in the last 30 days; the rate is read over at least 20"
    rate = sum(1 for r in rows if r["status"] == "needs_human") / len(rows)
    return (1 if rate < 0.10 else 0), f"needs_human rate {rate:.0%} over {len(rows)} tasks in the last 30 days (under 10% passes)"


def shipped_requests(db: Ledgers, t: float) -> tuple[float, str]:
    n = db.one("SELECT count(*) FROM build_jobs WHERE status = 'shipped' AND request_id IS NOT NULL AND shipped_at >= ?",
               (t - 30 * DAY,)) or 0
    return n, "build jobs for people's requests shipped in the last 30 days"


def issues_closed(db: Ledgers, t: float) -> tuple[float, str]:
    opened = {(r["repo"], str(r["ref"])) for r in db.rows("SELECT repo, ref FROM gh_actions WHERE verb = 'open-issue'")}
    closed = {(r["repo"], str(r["ref"])) for r in db.rows("SELECT repo, ref FROM gh_actions WHERE verb = 'close-issue'")}
    n = len(opened & closed)
    return n, f"issues Teeny opened and later closed itself ({len(opened)} opened); closing by its own PR is checked on GitHub"


def upstream_pr(db: Ledgers, t: float) -> tuple[float, str]:
    n = db.one("SELECT count(*) FROM gh_actions WHERE verb = 'open-pr' AND repo = ?", (UPSTREAM,)) or 0
    return n, f"PRs opened on {UPSTREAM}; whether one merged is checked on GitHub"


def outside_pr(db: Ledgers, t: float) -> tuple[float, str]:
    n = db.one("SELECT count(*) FROM gh_actions WHERE verb = 'open-pr' AND external = 1 AND repo != ?", (UPSTREAM,)) or 0
    return n, "PRs opened on repositories Teeny does not own; whether one merged is checked on GitHub"


def wallet_mainnet(db: Ledgers, t: float) -> tuple[float, str]:
    n = db.one("SELECT count(*) FROM wallet_payments WHERE cluster = 'mainnet' AND status = 'paid'") or 0
    return n, "payments settled on mainnet (wallet_payments)"


def _paid(purpose: str) -> Counter:
    def count(db: Ledgers, t: float) -> tuple[float, str]:
        n = db.one("SELECT count(*) FROM wallet_payments WHERE cluster = 'mainnet' AND status = 'paid' AND purpose = ?",
                   (purpose,)) or 0
        return n, f"{purpose} payments settled on mainnet"
    return count


def diary_days(db: Ledgers, t: float) -> tuple[float, str]:
    days = set()
    for page in (state_dir() / "diary").glob("*.md"):
        try:
            days.add(dt.date.fromisoformat(page.stem))
        except ValueError:
            continue
    return _streak(days, _day(t)), "nightly diary pages in a row (state/diary); the public copy is A14"


def generation_prs(db: Ledgers, t: float) -> tuple[float, str]:
    n = sum(1 for e in _archive() if e.get("pr_url") or e.get("pr_number"))
    return n, "generations bin/evolve opened a pull request for (state/evolve/archive.jsonl)"


def promoted(db: Ledgers, t: float) -> tuple[float, str]:
    n = sum(1 for e in _archive() if e.get("promoted_at"))
    return n, "generations the loop promoted itself"


def rolled_back(db: Ledgers, t: float) -> tuple[float, str]:
    rows = db.rows("SELECT detail FROM gh_actions WHERE verb = 'open-pr'")
    n = sum(1 for r in rows if str(_detail(r["detail"]).get("head") or "").startswith(REVERT_BRANCH))
    return n, "revert pull requests evolve-watch opened"


def chronicle(db: Ledgers, t: float) -> tuple[float, str]:
    pages = len(list((brain_dir() / "narrative" / "chronicle").glob("gen-*.md")))
    return pages, "chronicle pages (brain/narrative/chronicle/gen-<n>.md)"


#: (id, DIRECTIVE level, the item, what the proof asks for, need, counter)
PROOFS: tuple[tuple[str, int, str, str, int, Counter], ...] = (
    ("L0.health_alarm", 0, "Notices when its own brain is dead", "an alarm issue opened and closed around a real outage", 1, health_alarm),
    ("L1.gh_inbox", 1, "GitHub notifications as stimuli", "a GitHub event in the inbox that got an answer", 1, gh_inbox),
    ("L1.no_repeats", 1, "Its own recent posts fed back in", "30 days with no near-duplicate post", 30, no_repeats),
    ("L2.x_replies", 2, "Answers mentions, replies and quotes on X live", "50 real replies in the ledger", 50, x_replies),
    ("L2.mail_started", 2, "Answers mail live, and starts mail to known contacts", "a thread it began", 1, mail_started),
    ("L2.moltbook_week", 2, "Lives on Moltbook", "a week of Moltbook activity", 7, moltbook_week),
    ("L2.audience", 2, "Grows an audience", "four weeks in a row the total went up", 4, audience),
    ("L3.browser_week", 3, "Headless browsing proven on the box", "a week of ok tasks", 7, browser_week),
    ("L3.teeny_tasks", 3, "Teeny writes its own browser tasks", "tasks with --source teeny that finished ok", 1, teeny_tasks),
    ("L3.code_login", 3, "Logs into sites that mail it a code", "a task that went needs_code then ok", 1, code_login),
    ("L3.needs_human", 3, "needs_human rate under 10% over a month", "the rate under 10% over 30 days", 1, needs_human_rate),
    ("L3.shipped", 3, "Ships one thing a week from requests", "4 shipped requests in a month", 4, shipped_requests),
    ("L3.own_issues", 3, "Opens issues on its own repos", "an issue it opened and later closed", 1, issues_closed),
    ("L3.upstream", 3, "Contributes upstream to OpenHuman", "a merged openhuman PR by teenydroid", 1, upstream_pr),
    ("L3.outside", 3, "Contributes to outside open source", "a merged PR in a repo we don't own", 1, outside_pr),
    ("L3.mainnet", 3, "Wallet on mainnet with real caps", "a first receipt on mainnet", 1, wallet_mainnet),
    ("L3.bounty", 3, "Bounties live", "a paid bounty", 1, _paid("bounty")),
    ("L3.vendor", 3, "Pays a vendor for something it decided it needs", "a vendor payment", 1, _paid("vendor")),
    ("L4.diary", 4, "Public diary, one page a day for 30 days", "30 diary pages in a row", 30, diary_days),
    ("L5.generation_pr", 5, "bin/evolve, human-gated first", "a generation PR with a compare report", 1, generation_prs),
    ("L5.promoted", 5, "Promotion and rollback", "one promoted generation", 1, promoted),
    ("L5.rollback", 5, "Promotion and rollback", "one automatic rollback", 1, rolled_back),
    ("L5.chronicle", 5, "Chronicle", "a chronicle page per generation", 1, chronicle),
    ("L5.ten_promoted", 5, "Ten generations promoted with no human", "ten promoted generations", 10, promoted),
)

#: The proofs whose last step is outside the ledgers: never `met` here.
OUTSIDE = {"L1.gh_inbox", "L2.mail_started", "L3.own_issues", "L3.upstream", "L3.outside", "L5.ten_promoted"}

#: DIRECTIVE items no ledger counts yet, and what they wait for.
NOT_COUNTED: tuple[tuple[str, str], ...] = (
    ("L0 tracing to Langfuse", "traces are not exported (A16)"),
    ("L0 a week with no human restart", "no restart ledger"),
    ("L1 reads the web on its own schedule", "a post citing what it found is read by a person"),
    ("L1 on-chain senses", "no deposit watcher (A5)"),
    ("L1 notices its own health", "health stimuli as counts (A4); the issue filed first is read by a person"),
    ("L2 the audience is people", "W does not split human and agent reach (P2)"),
    ("L2 says what it is", "no profile check (P1) or rejection eval (P4) yet"),
    ("L2 remembers people", "no people records (A9)"),
    ("L2 runs the lore", "no episode trigger (A13)"),
    ("L2 more channels", "a conversation on a second platform is read by a person"),
    ("L3 signs itself up", "no accounts ledger (A12)"),
    ("L3 babysits its own PRs", "no CI reads (A7)"),
    ("L3 maintains what it shipped", "outside issues are not in a ledger"),
    ("L3 deploys what it builds", "no deploy hand (A11)"),
    ("L3 builds its own hands", "a merged hand used by a playbook is read on GitHub"),
    ("L3 runs scheduled data jobs", "not built (A17)"),
    ("L3 pays for its own APIs with x402, earns, sells, pays its own bills", "no inflow ledger yet (S1, S3, S5)"),
    ("L4 labelled memory, knows its own history, weekly goal pages", "gap G10; A10; A15"),
    ("L5 the frozen-path gate, the lab spine, fence proposals", "read on GitHub and the box"),
    ("L6 the human leaves", "no audit or autonomy ladder yet (G2, G6)"),
)


def count(proof_id: str, db: Ledgers | None = None, at: float | None = None) -> Json:
    """One proof's entry, by id (KeyError for an unknown one)."""
    for pid, level, item, proof, need, counter in PROOFS:
        if pid == proof_id:
            break
    else:
        raise KeyError(proof_id)
    own = db is None
    db = db or Ledgers(state_dir() / "hands.db")
    try:
        t = now() if at is None else at
        has, note = counter(db, t)
    finally:
        if own:
            db.close()
    has = int(has) if float(has).is_integer() else round(float(has), 3)
    if has >= need and pid not in OUTSIDE:
        status = "met"
    elif has > 0:
        status = "progress"
    else:
        status = "none"
    return {"id": pid, "level": level, "item": item, "proof": proof, "need": need, "has": has,
            "status": status, "note": note}


def report(only: Iterable[str] = (), at: float | None = None) -> Json:
    wanted = set(only)
    unknown = wanted - {p[0] for p in PROOFS}
    if unknown:
        raise KeyError(", ".join(sorted(unknown)))
    t = now() if at is None else at
    db = Ledgers(state_dir() / "hands.db")
    try:
        items = [count(p[0], db, t) for p in PROOFS if not wanted or p[0] in wanted]
    finally:
        db.close()
    summary = {s: sum(1 for i in items if i["status"] == s) for s in ("met", "progress", "none")}
    return {"ok": True, "at": dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "summary": summary, "items": items,
            "not_counted": [] if wanted else [{"item": i, "why": w} for i, w in NOT_COUNTED]}


def text(out: Mapping[str, Any]) -> str:
    lines = [f"proofs at {out['at']}: " + ", ".join(f"{n} {s}" for s, n in out["summary"].items()), ""]
    for i in out["items"]:
        lines.append(f"{i['status']:<9} {i['id']:<18} {i['has']:>5}/{i['need']:<3} {i['proof']}")
        lines.append(f"{'':<34}{i['note']}")
    if out["not_counted"]:
        lines += ["", "not counted:"]
        lines += [f"- {n['item']}: {n['why']}" for n in out["not_counted"]]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bin/proofs", description="How far each countable DIRECTIVE.md proof has got.")
    parser.add_argument("--text", action="store_true", help="a table for a person instead of JSON")
    parser.add_argument("--only", nargs="+", default=(), metavar="ID", help="just these proofs, by id")
    args = parser.parse_args(argv)
    try:
        out = report(args.only)
    except KeyError as exc:
        print(json.dumps({"ok": False, "error": f"no such proof: {exc.args[0]}"}))
        return 2
    except sqlite3.Error as exc:
        print(json.dumps({"ok": False, "error": f"the ledger could not be read: {exc}"}))
        return 1
    print(text(out) if args.text else json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
