"""Today's W from data other hands already keep, and the `world_scores`
table it lands in.

  attention   version 2, people-weighted reach: stats rows (latest per
              metric, up to today): x.followers, moltbook.followers,
              moltbook.karma, github.followers; plus the distinct accounts in
              `stimuli` (x, moltbook, mail, telegram, gh) over the 7 UTC days
              before today. Each is classed human, agent or unknown by a rule
              over the surface (`classify`), weighed by score.py, and an
              engaged account whose first contact came before Teeny addressed
              it counts double (`first_contact`). The unweighted version-1
              sum rides along as `v1_value`
  tiny        hands/wallet tiny (one token-account read; nothing signed)
  money       hands/wallet balance (USDC, and SOL at WALLET_SOL_USD) minus
              WORLD_TEAM_USD (what the team put in, the operator's running
              total, 0 when unset) minus hands/wallet costs --days 30
  capability  hands/<name> executables with tests in hands/tests, plus the
              channels whose keys are set (x, moltbook, github, mail,
              telegram; the wallet when its caps load)
  shipped     build_jobs shipped, plus stats github.stars, github.forks and
              github.open_issues where stats keeps them
  world       brain/narrative/ARCS.md: each arc 0 (not started), 1 (any
              other status), 2 (done, complete, reached, achieved)
  penalty     on the UTC day before today: guard holds on Teeny's own text
              (not inbound, not approved, not cleared), and wallet payments
              now in doubt

Every source is optional. A source that is missing is skipped and said so,
one that fails is reported, and the rest still land. A component whose
sources are all missing writes no row that day, so it neither gains nor
loses. One row per (date, component): a second snapshot the same UTC day
replaces the day's value. Nothing here writes anywhere but `world_scores`,
and nothing calls a verb that acts.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .. import hand
from . import score

SCHEMA = """
CREATE TABLE IF NOT EXISTS world_scores (
    date      TEXT NOT NULL,
    component TEXT NOT NULL,
    value     REAL NOT NULL,
    detail    TEXT NOT NULL DEFAULT '{}',
    at        REAL NOT NULL,
    PRIMARY KEY (date, component)
);
"""

Json = dict[str, Any]

#: Stats metrics that count as attention, and as shipped.
ATTENTION_STATS = (("x", "followers"), ("moltbook", "followers"), ("moltbook", "karma"), ("github", "followers"))
SHIPPED_STATS = (("github", "stars"), ("github", "forks"), ("github", "open_issues"))
#: Stimuli surfaces where a person reached Teeny, and the window counted.
ENGAGED_SURFACES = ("x", "moltbook", "mail", "telegram", "gh")
#: The surfaces version 1 counted, for `v1_value`.
V1_SURFACES = ("x", "moltbook", "mail", "telegram")
ENGAGED_DAYS = 7
#: Hand executables whose tests live under another name.
TEST_ALIASES = {"image": "media", "video": "media"}
#: Channel -> the keys that must all be set for Teeny to act on it.
CHANNELS = {
    "x": ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"),
    "moltbook": ("MOLTBOOK_API_KEY",),
    "github": ("GITHUB_TOKEN",),
    "mail": ("AGENTMAIL_API_KEY", "AGENTMAIL_INBOX"),
    "telegram": ("TELEGRAM_BOT_TOKEN",),
}
COSTS_DAYS = 30


class Skip(Exception):
    """This source (or component) has nothing to read: say why, move on."""


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def today() -> str:
    return datetime.fromtimestamp(hand.now(), timezone.utc).strftime("%Y-%m-%d")


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _stats_latest(conn: sqlite3.Connection, date: str, wanted: tuple[tuple[str, str], ...]) -> dict[str, float]:
    """The latest stats value per wanted metric on or before `date`. A
    source that failed today still counts at its last known value."""
    if not _has_table(conn, "stats"):
        raise Skip("no stats table yet (bin/stats-snapshot has not run)")
    out = {}
    for source, metric in wanted:
        row = conn.execute(
            "SELECT value FROM stats WHERE source = ? AND metric = ? AND date <= ? ORDER BY date DESC LIMIT 1",
            (source, metric, date),
        ).fetchone()
        if row is not None:
            out[f"{source}.{metric}"] = float(row["value"])
    return out


# --- the components -------------------------------------------------------------
# Each takes the open connection and today's date and answers (value, detail),
# or raises Skip (nothing to read) or HandError (a source failed).


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+")


def classify(surface: str, who: str) -> str:
    """Human, agent or unknown, by a rule over the surface and the handle
    (score.CLASS_RULES says why). Never a guess: what no rule settles is
    unknown."""
    if surface == "moltbook":
        return "agent"
    if surface == "telegram":
        return "human"
    if surface == "gh" and who.strip().lower().endswith("[bot]"):
        return "agent"
    return "unknown"


def first_contact(conn: sqlite3.Connection, surface: str, who: str, first_at: float) -> str:
    """Whose was the first contact: `theirs` when Teeny had not addressed the
    account before its first event on this surface, `teenys` when it had,
    `unknown` when no ledger says. Read from the hands' own ledgers: Teeny's
    X posts (an @handle in one), its sent mail (the address), and Telegram,
    where a bot can't write to anyone first."""
    if surface == "telegram":
        return "theirs"
    if surface == "x":
        handle = who.strip().lstrip("@").lower()
        if not re.fullmatch(r"[a-z0-9_]{1,15}", handle):
            return "unknown"
        if not _has_table(conn, "x_posts"):
            return "theirs"
        mention = re.compile(rf"(?<![a-z0-9_])@{handle}(?![a-z0-9_])")
        for row in conn.execute("SELECT text FROM x_posts WHERE created_at < ?", (first_at,)):
            if mention.search(str(row["text"]).lower()):
                return "teenys"
        return "theirs"
    if surface == "mail":
        found = _EMAIL.search(who)
        if not found:
            return "unknown"
        if not _has_table(conn, "mail_sent"):
            return "theirs"
        row = conn.execute(
            "SELECT 1 FROM mail_sent WHERE lower(to_addr) LIKE ? AND at < ? LIMIT 1", (f"%{found.group(0).lower()}%", first_at)
        ).fetchone()
        return "teenys" if row else "theirs"
    return "unknown"


def attention(conn: sqlite3.Connection, date: str) -> tuple[float, Json]:
    """Version 2: people-weighted reach. `parts` keeps the raw counts under
    version 1's names; `split` says who they are; the value is the weighted
    sum."""
    parts: Json = {}
    notes: list[str] = []
    split: Json = {k: {"followers": 0.0, "accounts": 0, "first_contact_theirs": 0, "weighted": 0.0} for k in score.REACH_CLASSES}
    weighted = 0.0
    v1 = 0.0
    try:
        stats = _stats_latest(conn, date, ATTENTION_STATS)
        parts.update(stats)
        for name, value in stats.items():
            kind = score.STAT_CLASSES[name]
            split[kind]["followers"] += value
            split[kind]["weighted"] += score.FOLLOWER_WEIGHTS[kind] * value
            weighted += score.FOLLOWER_WEIGHTS[kind] * value
            v1 += value
        if not stats:
            notes.append("no follower or karma rows in stats yet")
    except Skip as exc:
        notes.append(str(exc))
    if _has_table(conn, "stimuli"):
        end = hand.day_start()
        marks = ",".join("?" * len(ENGAGED_SURFACES))
        accounts = conn.execute(
            f"SELECT surface, lower(trim(who)) AS who FROM stimuli"
            f" WHERE surface IN ({marks}) AND who IS NOT NULL AND trim(who) != '' AND at >= ? AND at < ?"
            f" GROUP BY surface, lower(trim(who)) ORDER BY surface, lower(trim(who))",
            (*ENGAGED_SURFACES, end - ENGAGED_DAYS * 86400, end),
        ).fetchall()
        engaged = 0
        for row in accounts:
            surface, who = row["surface"], row["who"]
            if surface in V1_SURFACES:
                v1 += 1
            engaged += 1
            first = conn.execute(
                "SELECT min(at) AS at FROM stimuli WHERE surface = ? AND lower(trim(who)) = ?", (surface, who)
            ).fetchone()["at"]
            theirs = first_contact(conn, surface, who, float(first)) == "theirs"
            kind = classify(surface, who)
            add = score.engaged_weight(kind, theirs)
            split[kind]["accounts"] += 1
            split[kind]["first_contact_theirs"] += int(theirs)
            split[kind]["weighted"] += add
            weighted += add
        parts["engaged_accounts_7d"] = float(engaged)
    else:
        notes.append("no stimuli table yet, so no mentions or replies counted")
    if not parts:
        raise Skip("; ".join(notes))
    for item in split.values():
        item["weighted"] = round(item["weighted"], 6)
    detail: Json = {"version": score.ATTENTION_VERSION, "parts": parts, "split": split, "v1_value": v1}
    if notes:
        detail["notes"] = notes
    return weighted, detail


def tiny(conn: sqlite3.Connection, date: str) -> tuple[float, Json]:
    from ..wallet import reads

    held = reads.tiny()
    return float(held["amount"]), {"parts": {"tokens": held["amount"]}, "mint": held["mint"], "cluster": held["cluster"]}


def money(conn: sqlite3.Connection, date: str) -> tuple[float, Json]:
    from ..wallet import caps, reads

    notes: list[str] = []
    got = reads.balance()
    usdc = int(got["usdc"]["raw"]) / 10 ** caps.DECIMALS["USDC"]
    sol_tokens = int(got["sol"]["raw"]) / 10 ** caps.DECIMALS["SOL"]
    sol_usd_text = hand.setting(caps.SOL_USD)
    try:
        sol_price = caps.dollars_to_micro(sol_usd_text, caps.SOL_USD) / caps.MICRO if sol_usd_text else 0.0
    except caps.CapsError:
        sol_price = 0.0
    if not sol_price:
        notes.append("SOL not counted: WALLET_SOL_USD is unset or unreadable")
    team_text = hand.setting("WORLD_TEAM_USD")
    try:
        team = caps.dollars_to_micro(team_text, "WORLD_TEAM_USD") / caps.MICRO if team_text else 0.0
    except caps.CapsError:
        raise hand.HandError("WORLD_TEAM_USD is not a number") from None
    costs = reads.costs(COSTS_DAYS)
    if not costs.get("complete", False):
        notes.append("costs incomplete: a cost source could not be read")
    cost_usd = float(costs.get("total_usd") or 0)
    parts = {"usdc": usdc, "sol_usd": round(sol_tokens * sol_price, 6), "team_usd": -team, f"costs_{COSTS_DAYS}d_usd": -cost_usd}
    return sum(parts.values()), {"parts": parts, **({"notes": notes} if notes else {})}


def _hands_with_tests(base: Path) -> list[str]:
    hands_dir = base / "hands"
    tests = hands_dir / "tests"
    found = []
    for entry in sorted(hands_dir.iterdir()) if hands_dir.is_dir() else []:
        if not entry.is_file() or not os.access(entry, os.X_OK):
            continue
        name = TEST_ALIASES.get(entry.name, entry.name)
        suite = tests / name
        if (suite.is_dir() and any(suite.glob("test_*.py"))) or (tests / f"test_{name}.py").is_file():
            found.append(entry.name)
    return found


def capability(conn: sqlite3.Connection, date: str) -> tuple[float, Json]:
    from ..wallet import caps

    hands_tested = _hands_with_tests(hand.root())
    channels = [name for name, keys in CHANNELS.items() if all(hand.setting(k) for k in keys)]
    try:
        caps.load(hand.secrets())
        channels.append("wallet")
    except caps.CapsError:
        pass
    if not hands_tested and not channels:
        raise Skip("no hands with tests found and no channel keys set")
    return float(len(hands_tested) + len(channels)), {
        "parts": {"hands_with_tests": float(len(hands_tested)), "channels": float(len(channels))},
        "hands": hands_tested,
        "channels": channels,
    }


def shipped(conn: sqlite3.Connection, date: str) -> tuple[float, Json]:
    parts: Json = {}
    notes: list[str] = []
    if _has_table(conn, "build_jobs"):
        row = conn.execute("SELECT count(*) AS n FROM build_jobs WHERE status = 'shipped'").fetchone()
        parts["build_jobs_shipped"] = float(row["n"])
    else:
        notes.append("no build_jobs table yet")
    try:
        parts.update(_stats_latest(conn, date, SHIPPED_STATS))
    except Skip as exc:
        notes.append(str(exc))
    if not parts:
        raise Skip("; ".join(notes))
    return sum(parts.values()), {"parts": parts, **({"notes": notes} if notes else {})}


_DONE = re.compile(r"\b(done|complete|completed|reached|achieved)\b")


def arc_points(text: str) -> dict[str, int]:
    """Each `## ` arc in ARCS.md and its points, from its `- status:` line:
    0 not started (or no status), 2 done, 1 anything else."""
    arcs: dict[str, int] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            arcs[current] = 0
            continue
        match = re.match(r"\s*[-*]\s*status:\s*(.*)", line, re.IGNORECASE)
        if current is not None and match:
            status = match.group(1).strip().lower()
            arcs[current] = 0 if status.startswith("not started") or not status else (2 if _DONE.search(status) else 1)
    return arcs


def world(conn: sqlite3.Connection, date: str) -> tuple[float, Json]:
    path = hand.root() / "brain" / "narrative" / "ARCS.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise Skip("no brain/narrative/ARCS.md") from None
    arcs = arc_points(text)
    if not arcs:
        raise Skip("ARCS.md has no arcs")
    return float(sum(arcs.values())), {"parts": {k: float(v) for k, v in arcs.items()}}


def penalty(conn: sqlite3.Connection, date: str) -> tuple[float, Json]:
    end = hand.day_start()
    start = end - 86400
    yesterday = datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%d")
    parts: Json = {}
    if _has_table(conn, "guard_holds"):
        row = conn.execute(
            "SELECT count(*) AS n FROM guard_holds WHERE direction != 'inbound'"
            " AND status NOT IN ('approved', 'cleared') AND substr(at, 1, 10) = ?",
            (yesterday,),
        ).fetchone()
        parts["guard_holds_own"] = float(row["n"])
    if _has_table(conn, "wallet_payments"):
        row = conn.execute(
            "SELECT count(*) AS n FROM wallet_payments WHERE status = 'in_doubt' AND created_at >= ? AND created_at < ?",
            (start, end),
        ).fetchone()
        parts["payments_in_doubt"] = float(row["n"])
    # No table means nothing was ever held or paid: zero events, not a gap.
    return sum(parts.values()), {"parts": parts, "day": yesterday}


COMPONENT_READS: dict[str, Callable[[sqlite3.Connection, str], tuple[float, Json]]] = {
    "attention": attention,
    "tiny": tiny,
    "money": money,
    "capability": capability,
    "shipped": shipped,
    "world": world,
    score.PENALTY: penalty,
}


def snapshot() -> Json:
    date = today()
    written: dict[str, float] = {}
    details: dict[str, Json] = {}
    skipped: dict[str, str] = {}
    failed: dict[str, str] = {}
    with hand.lock("world-snapshot", timeout=30):
        conn = db()
        try:
            for name, read in COMPONENT_READS.items():
                try:
                    value, detail = read(conn, date)
                except Skip as exc:
                    skipped[name] = str(exc)
                    continue
                except hand.HandError as exc:
                    failed[name] = str(exc)
                    continue
                written[name] = round(value, 6)
                details[name] = detail
            with hand.transaction(conn):
                for name, value in written.items():
                    conn.execute(
                        "INSERT INTO world_scores (date, component, value, detail, at) VALUES (?, ?, ?, ?, ?)"
                        " ON CONFLICT(date, component) DO UPDATE SET value = excluded.value, detail = excluded.detail, at = excluded.at",
                        (date, name, value, json.dumps(details[name], sort_keys=True), hand.now()),
                    )
        finally:
            conn.close()
    out: Json = {"date": date, "written": written}
    if skipped:
        out["skipped"] = skipped
    if failed:
        out["failed"] = failed
    return out


# --- reading it back -----------------------------------------------------------------


def _num(value: float, places: int = 4) -> int | float:
    value = round(float(value), places)
    return int(value) if value.is_integer() else value


def _shift(date: str, days: int) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")


def report(days: int) -> Json:
    conn = db()
    try:
        latest = conn.execute("SELECT max(date) AS d FROM world_scores").fetchone()["d"]
        if latest is None:
            return {
                "days": days,
                "as_of": None,
                "J": None,
                "reading": ["There is no world snapshot yet (hands/world snapshot, the pinned world-snapshot job). Quote no W or J number."],
            }
        since = _shift(latest, days)
        rows: dict[str, list[tuple[str, float, int]]] = {}
        latest_detail: dict[str, Json] = {}
        for row in conn.execute("SELECT date, component, value, detail FROM world_scores WHERE date <= ? ORDER BY date", (latest,)):
            detail = json.loads(row["detail"] or "{}")
            rows.setdefault(row["component"], []).append((row["date"], float(row["value"]), _version(detail)))
            latest_detail[row["component"]] = {"date": row["date"], **detail}
    finally:
        conn.close()

    components = []
    credits: dict[str, float] = {}
    reading: list[str] = []
    for comp in score.COMPONENTS:
        series = rows.get(comp.name, [])
        before = [r for r in series if r[0] <= since]
        window = ([before[-1]] if before else []) + [r for r in series if r[0] > since]
        item: Json = {
            "component": comp.name,
            "counts": comp.counts,
            "unit": comp.unit,
            "weight": comp.weight,
            "c": comp.offset,
            "daily_cap": comp.daily_cap,
        }
        if not window:
            item.update({"value": None, "credit": 0, "contribution": 0, "note": "no rows yet: this source has never been read"})
            reading.append(f"{comp.name}: no data yet, counts 0.")
            components.append(item)
            credits[comp.name] = 0.0
            continue
        got = score.credit(window, comp)
        last_date, last_value, last_version = series[-1]
        first_date, first_value, _ = window[0]
        credits[comp.name] = got["credit"]
        item.update(
            {
                "value": _num(last_value),
                "date": last_date,
                "was": _num(first_value),
                "since": first_date,
                "delta_log": _num(got["delta_log"]),
                "credit": _num(got["credit"]),
                "capped_days": got["capped_days"],
                "contribution": _num(comp.weight * got["credit"]),
                "parts": latest_detail.get(comp.name, {}).get("parts", {}),
            }
        )
        if last_version != 1:
            item["version"] = last_version
        if got["rebased"]:
            item["rebased"] = got["rebased"]
        split = latest_detail.get(comp.name, {}).get("split")
        if split:
            item["split"] = split
        if last_date != latest:
            item["note"] = f"no row on {latest} (the source was skipped or failed); the last value is from {last_date}"
        elif first_date > since and len(window) > 1:
            item["note"] = f"rows start on {first_date}, so the window is shorter than {days} days"
        elif len(window) < 2:
            item["note"] = "only one row so far: growth needs two days"
        line = f"{comp.name}: {item['was']} on {first_date} to {item['value']} on {last_date} ({comp.unit}), Δlog {item['delta_log']}"
        if got["capped_days"]:
            line += f", capped on {got['capped_days']} day(s) to a credit of {item['credit']}"
        if got["rebased"]:
            line += f", the step into version {last_version} counted zero"
        line += f"; weight {comp.weight}, adds {item['contribution']} to J."
        reading.append(line)
        if split:
            reading.append(_reach_line(split))
        components.append(item)

    events = sum(v for d, v, _ in rows.get(score.PENALTY, []) if since < d <= latest)
    cost = score.PENALTY_WEIGHT * events
    j = score.j_score(credits, events)
    reading.append(f"penalty: {_num(events)} side-effect event(s) in the window, minus {_num(cost)}.")
    best = max(components, key=lambda c: c["contribution"])
    worst = min(components, key=lambda c: c["contribution"])
    reading.append(f"J over {days} days is {_num(j)}. Most growth: {best['component']}. Least: {worst['component']}.")
    return {
        "days": days,
        "as_of": latest,
        "since": since,
        "J": _num(j),
        "formula": "J = sum over components of weight * credit - penalty_weight * penalty events; credit is the change in log(W + c) "
        "over the window, each day's step clipped to +/- daily_cap",
        "components": components,
        "penalty": {"events": _num(events), "weight": score.PENALTY_WEIGHT, "cost": _num(cost), "counts": score.PENALTY_COUNTS},
        "reading": reading,
        "rules": "These numbers come from frozen code (hands/world). Quote only numbers that appear here; "
        "J is a growth score, not money, and says nothing about the $TINY price.",
    }


def _version(detail: Json) -> int:
    """A row's version: what its detail says, 1 for rows written before
    components carried one."""
    try:
        return int(detail.get("version", 1))
    except (TypeError, ValueError):
        return 1


def _reach_line(split: Json) -> str:
    """One sentence on who the attention is: people, agents, or accounts no
    rule can place."""
    said = []
    for kind in score.REACH_CLASSES:
        got = split.get(kind) or {}
        said.append(
            f"{kind} {_num(got.get('accounts', 0))} engaged account(s)"
            f" ({_num(got.get('first_contact_theirs', 0))} came first) and {_num(got.get('followers', 0))} follower(s) or karma,"
            f" weighted {_num(got.get('weighted', 0))}"
        )
    return "attention reach: " + "; ".join(said) + ". Unknown means no rule can tell; never call it people."


def history(days: int) -> Json:
    conn = db()
    try:
        latest = conn.execute("SELECT max(date) AS d FROM world_scores").fetchone()["d"]
        if latest is None:
            return {"days": days, "rows": []}
        since = _shift(latest, days)
        rows = conn.execute(
            "SELECT date, component, value, detail FROM world_scores WHERE date > ? ORDER BY date, component", (since,)
        ).fetchall()
    finally:
        conn.close()
    by_date: dict[str, Json] = {}
    for row in rows:
        day = by_date.setdefault(row["date"], {"date": row["date"], "values": {}, "parts": {}})
        detail = json.loads(row["detail"] or "{}")
        day["values"][row["component"]] = _num(row["value"])
        day["parts"][row["component"]] = detail.get("parts", {})
        if _version(detail) != 1:
            day.setdefault("versions", {})[row["component"]] = _version(detail)
        if detail.get("split"):
            day.setdefault("split", {})[row["component"]] = detail["split"]
    return {"days": days, "as_of": latest, "rows": list(by_date.values())}
