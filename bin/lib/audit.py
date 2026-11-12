"""The weekly audit Teeny can't write (docs/roadmap.md, G6; docs/governance.md).

    python3 bin/lib/audit.py [--week YYYY-Www] [--json]   build last week's page (or that week's)
    python3 bin/lib/audit.py boot                          record a boot (bin/spine, after the doctor)

Frozen code, no model: it reads the ledgers the hands and the loop already
keep and counts what happened in one ISO week (Monday 00:00 UTC to the next):

- **holds**: guard holds by category and outcome (`guard_holds`), and how
  many the hold reviewer released (G4, `guard_reviews`) and how many it
  queued for the operator's sample;
- **spend**: what the wallet paid against each of its caps (the largest
  payment, the largest rolling day, the largest recipient-week), payments in
  doubt or refused, and $TINY bought (`wallet_payments`, `wallet_tiny_buys`;
  caps from secrets/teeny.env, as numbers only);
- **halts**: the evolve loop's halts by kind (`state/evolve/halts.jsonl`);
- **generations**: each generation recorded that week, its verdict, and
  whether it was merged, promoted or reverted (`state/evolve/archive.jsonl`);
- **drift**: the largest persona drift a probe measured, and drift halts;
- **operator actions**: every act of a person, which the 30-day test
  (DIRECTIVE.md, "The test") needs to be zero: commits on main the loop
  didn't merge, guard holds a person decided, deploys (a boot on a new
  commit the loop didn't promote), restarts (a boot on the same commit;
  until a crash can be told apart from a person, every one counts). A level
  raised on the autonomy ladder is a commit on main, so it is counted there.

Nothing is free text: every value is a number, a date, an enum the code
checks, or a generation id matching `gen-<n>`. A hold's text, a halt's
detail, a commit message and a hold note are never read into the page.

The page is written to `state/audit/<week>.json` and `.md` every run, so the
box always has it. With `AUDIT_PUBLISH=1` in secrets/teeny.env (off by
default) and not under DRY_RUN, it also goes out with the receipts: to
`audit/<week>.json` and `.md` in the receipts repository (teenydroid/diary),
under the receipts lock and through the same secret gate.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

CODE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE / "hands" / "lib"))
sys.path.insert(0, str(CODE / "bin" / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands.guard import holds as guard_holds, lexicon  # noqa: E402
from teeny_hands.wallet import caps as wallet_caps  # noqa: E402

Json = dict[str, Any]
DAY = 86400
WEEK = re.compile(r"(\d{4})-W(\d{2})")
GEN_ID = re.compile(r"gen-\d{1,6}")
#: Every category a hold can carry: the lexicon's, the judge's (and its
#: "unknown"), a draft that was only a disallowed link, and a reply after a
#: rejection (P4). Anything else is counted as "other".
HOLD_CATEGORIES = tuple(dict.fromkeys(lexicon.CATEGORIES + ("links", "rejection", "unknown", "none")))
VERDICTS = ("won", "lost", "unevaluated", "duplicate", "no-proposal", "gated", "seed", "refused")
HALT_KINDS = ("generations", "winrate-jump", "j-rise", "persona-drift", "doctor", "fallback", "watch-revert")
AUDIT_DIR = "audit"


# --- where things are ------------------------------------------------------------------------


def root() -> Path:
    return Path(os.environ.get("TEENY_ROOT") or CODE)


def state() -> Path:
    return Path(os.environ.get("TEENY_STATE") or root() / "state")


def iso(at: float) -> str:
    return dt.datetime.fromtimestamp(at, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(text: Any) -> float | None:
    if isinstance(text, (int, float)):
        return float(text)
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def week_bounds(week: str | None, now: float) -> tuple[str, float, float]:
    """The ISO week named, else the last whole one before `now`."""
    if week:
        m = WEEK.fullmatch(week)
        if not m:
            raise ValueError(f"a week is YYYY-Www, not {week!r}")
        start = dt.datetime.fromisocalendar(int(m.group(1)), int(m.group(2)), 1).replace(tzinfo=dt.timezone.utc)
    else:
        today = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
        this_monday = today - dt.timedelta(days=today.weekday())
        start = dt.datetime.combine(this_monday - dt.timedelta(days=7), dt.time(), dt.timezone.utc)
    year, num, _ = start.isocalendar()
    return f"{year}-W{num:02d}", start.timestamp(), start.timestamp() + 7 * DAY


def _jsonl(path: Path) -> list[Json]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


class Ledgers:
    """state/hands.db, read-only; a missing file or table reads as empty."""

    def __init__(self, path: Path):
        self.conn = None
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

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()


def _enum(value: Any, allowed: Iterable[str]) -> str:
    return value if isinstance(value, str) and value in tuple(allowed) else "other"


# --- each section ------------------------------------------------------------------------------


def holds(db: Ledgers, start: float, end: float) -> Json:
    by: dict[str, dict[str, int]] = {}
    total = 0
    for r in db.rows("SELECT at, category, status FROM guard_holds"):
        at = epoch(r["at"])
        if at is None or not start <= at < end:
            continue
        cat = _enum(r["category"], HOLD_CATEGORIES)
        status = _enum(r["status"], guard_holds.STATUSES)
        by.setdefault(cat, {})
        by[cat][status] = by[cat].get(status, 0) + 1
        total += 1
    reviewed = {"released": 0, "kept": 0, "sampled": 0}
    for r in db.rows("SELECT at, outcome, sampled FROM guard_reviews WHERE at >= ? AND at < ?", (start, end)):
        if r["outcome"] in ("released", "kept"):
            reviewed[r["outcome"]] += 1
        if r["sampled"]:
            reviewed["sampled"] += 1
    return {"total": total, "by_category": dict(sorted(by.items())), "reviewer": reviewed}


def _usd(micro: int | None) -> float | None:
    return None if micro is None else round(micro / 1_000_000, 2)


def spend(db: Ledgers, start: float, end: float, env: Mapping[str, str]) -> Json:
    try:
        caps = wallet_caps.load(env) if wallet_caps.configured(env) else None
    except wallet_caps.CapsError:
        caps = None
    paid = [(float(r["created_at"]), int(r["usd_micro"]), str(r["to_address"]))
            for r in db.rows("SELECT created_at, usd_micro, to_address, status FROM wallet_payments "
                             "WHERE status IN ('paid', 'in_doubt') AND created_at >= ? AND created_at < ?",
                             (start - 7 * DAY, end))]
    in_week = [p for p in paid if p[0] >= start]
    max_tx = max((p[1] for p in in_week), default=0)
    max_day = 0
    max_recipient_week = 0
    for at, _, to in in_week:
        max_day = max(max_day, sum(m for t, m, _ in paid if at - DAY < t <= at))
        max_recipient_week = max(max_recipient_week, sum(m for t, m, r in paid if r == to and at - 7 * DAY < t <= at))
    status: dict[str, int] = {}
    for r in db.rows("SELECT status FROM wallet_payments WHERE created_at >= ? AND created_at < ?", (start, end)):
        s = _enum(r["status"], ("pending", "paid", "in_doubt", "failed", "refused"))
        status[s] = status.get(s, 0) + 1
    bought = sum(int(r["usd_micro"]) for r in db.rows(
        "SELECT usd_micro FROM wallet_tiny_buys WHERE status = 'bought' AND created_at >= ? AND created_at < ?",
        (start, end)))
    out = {"paid_usd": _usd(sum(p[1] for p in in_week)), "payments": status, "tiny_bought_usd": _usd(bought),
           "largest_payment_usd": _usd(max_tx), "largest_day_usd": _usd(max_day),
           "largest_recipient_week_usd": _usd(max_recipient_week), "caps": None}
    if caps is not None:
        out["caps"] = {"cluster": caps.cluster, "per_tx_usd": _usd(caps.per_tx_micro),
                       "per_day_usd": _usd(caps.per_day_micro),
                       "per_recipient_week_usd": _usd(caps.recipient_week_micro)}
        out["over_a_cap"] = (max_tx > caps.per_tx_micro or max_day > caps.per_day_micro
                             or max_recipient_week > caps.recipient_week_micro)
    return out


def _archive() -> list[Json]:
    merged: dict[str, Json] = {}
    first: dict[str, float] = {}
    for e in _jsonl(state() / "evolve" / "archive.jsonl"):
        if e.get("id") is None:
            continue
        key = str(e["id"])
        merged[key] = {**merged.get(key, {}), **e}
        at = epoch(e.get("recorded_at"))
        if at is not None and key not in first:
            first[key] = at
    for key, e in merged.items():
        e["_first"] = first.get(key)
    return list(merged.values())


def generations(start: float, end: float) -> tuple[list[Json], Json]:
    rows = []
    drift: list[float] = []
    for e in _archive():
        at = e.get("_first")
        gid = str(e.get("id"))
        if at is None or not start <= at < end or not GEN_ID.fullmatch(gid.split("/")[0]):
            continue
        rows.append({"id": gid if GEN_ID.fullmatch(gid) else gid.split("/")[0], "verdict": _enum(e.get("verdict"), VERDICTS),
                     "pr": bool(e.get("pr_number") or e.get("pr_url")), "merged": bool(e.get("merged_at")),
                     "promoted": bool(e.get("promoted_at")), "reverted": bool(e.get("reverted_at") or e.get("reverted"))})
        probe = e.get("probe")
        if isinstance(probe, Mapping) and isinstance(probe.get("drift"), (int, float)):
            drift.append(float(probe["drift"]))
    rows.sort(key=lambda r: r["id"])
    return rows, {"probes": len(drift), "max": round(max(drift), 3) if drift else None}


def halts(start: float, end: float) -> Json:
    by: dict[str, int] = {}
    for h in _jsonl(state() / "evolve" / "halts.jsonl"):
        at = epoch(h.get("at"))
        if at is None or not start <= at < end or h.get("resumed"):
            continue
        kind = _enum(h.get("kind"), HALT_KINDS)
        by[kind] = by.get(kind, 0) + 1
    return {"total": sum(by.values()), "by_kind": dict(sorted(by.items()))}


def _git_commits(start: float, end: float) -> list[tuple[str, float]] | None:
    """First-parent commits on main (origin/main when fetched, else HEAD)
    made in the week; None when the checkout isn't a git repository."""
    repo = root()
    for ref in ("origin/main", "HEAD"):
        try:
            out = subprocess.run(["git", "-C", str(repo), "log", "--first-parent", "--format=%H %ct",
                                  f"--since=@{int(start)}", ref], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if out.returncode == 0:
            rows = []
            for line in out.stdout.splitlines():
                sha, _, ct = line.partition(" ")
                if ct.isdigit() and start <= int(ct) < end:
                    rows.append((sha, float(ct)))
            return rows
    return None


def operator(db: Ledgers, start: float, end: float) -> Json:
    loop_shas = {str(e.get("merge_sha")) for e in _archive() if e.get("merge_sha")}
    commits = _git_commits(start, end)
    merges = None if commits is None else sum(1 for sha, _ in commits if sha not in loop_shas)
    decisions = 0
    for r in db.rows("SELECT decided_at, status FROM guard_holds WHERE status IN ('approved', 'rejected')"):
        at = epoch(r["decided_at"])
        if at is not None and start <= at < end:
            decisions += 1
    deploys = restarts = 0
    boots = _jsonl(state() / "boots.jsonl")
    prev: str | None = None
    for b in boots:
        at = epoch(b.get("at"))
        commit = str(b.get("commit") or "")
        if at is not None and start <= at < end and prev is not None:
            if commit != prev and commit not in loop_shas and not b.get("fallback"):
                deploys += 1
            elif commit == prev:
                restarts += 1
        prev = commit
    total = (merges or 0) + decisions + deploys + restarts
    return {"total": total, "commits_on_main": merges, "hold_decisions": decisions, "deploys": deploys,
            "restarts": restarts, "boots": sum(1 for b in boots if start <= (epoch(b.get("at")) or 0) < end)}


# --- the page -----------------------------------------------------------------------------------


def build(week: str | None = None, now: float | None = None) -> Json:
    t = hand.now() if now is None else now
    name, start, end = week_bounds(week, t)
    db = Ledgers(state() / "hands.db")
    try:
        gens, drift = generations(start, end)
        h = halts(start, end)
        drift["halts"] = h["by_kind"].get("persona-drift", 0)
        page = {"week": name, "from": iso(start), "to": iso(end), "built_at": iso(t),
                "holds": holds(db, start, end), "spend": spend(db, start, end, hand.secrets()),
                "halts": h, "generations": gens, "drift": drift, "operator": operator(db, start, end)}
    finally:
        db.close()
    return page


def markdown(page: Mapping[str, Any]) -> str:
    def money(v: Any) -> str:
        return "n/a" if v is None else f"${v:,.2f}"

    op = page["operator"]
    lines = [f"# Audit {page['week']}", "",
             f"{page['from']} to {page['to']}. Written by frozen code (`bin/audit`) from the ledgers; "
             "Teeny can't edit it.", "",
             "## Operator actions", "",
             f"**{op['total']}** in all: {op['commits_on_main'] if op['commits_on_main'] is not None else 'n/a'} "
             f"commit(s) on main the loop didn't merge, {op['hold_decisions']} guard hold(s) decided, "
             f"{op['deploys']} deploy(s), {op['restarts']} restart(s) ({op['boots']} boot(s) in all; "
             "a restart nobody explained counts).", "",
             "## Guard holds", "", f"{page['holds']['total']} held."]
    for cat, outcomes in page["holds"]["by_category"].items():
        lines.append(f"- {cat}: " + ", ".join(f"{n} {s}" for s, n in sorted(outcomes.items())))
    rv = page["holds"]["reviewer"]
    lines += [f"- the reviewer released {rv['released']}, kept {rv['kept']}, and queued {rv['sampled']} "
              "for the operator's sample", "", "## Spend against the caps", ""]
    sp = page["spend"]
    caps = sp.get("caps") or {}
    lines += [f"- paid: {money(sp['paid_usd'])}; $TINY bought: {money(sp['tiny_bought_usd'])}",
              f"- largest payment: {money(sp['largest_payment_usd'])} (cap {money(caps.get('per_tx_usd'))})",
              f"- largest day: {money(sp['largest_day_usd'])} (cap {money(caps.get('per_day_usd'))})",
              f"- largest recipient-week: {money(sp['largest_recipient_week_usd'])} "
              f"(cap {money(caps.get('per_recipient_week_usd'))})",
              "- payments: " + (", ".join(f"{n} {s}" for s, n in sorted(sp["payments"].items())) or "none"), "",
              "## Halts", "", f"{page['halts']['total']}: "
              + (", ".join(f"{n} {k}" for k, n in page["halts"]["by_kind"].items()) or "none"), "",
              "## Generations", ""]
    for g in page["generations"]:
        flags = [f for f in ("pr", "merged", "promoted", "reverted") if g[f]]
        lines.append(f"- {g['id']}: {g['verdict']}" + (f" ({', '.join(flags)})" if flags else ""))
    if not page["generations"]:
        lines.append("none")
    d = page["drift"]
    lines += ["", "## Persona drift", "",
              f"largest measured: {d['max'] if d['max'] is not None else 'n/a'} over {d['probes']} probe(s); "
              f"{d['halts']} drift halt(s)", ""]
    return "\n".join(lines)


def write_local(page: Mapping[str, Any]) -> Path:
    folder = state() / "audit"
    folder.mkdir(parents=True, exist_ok=True)
    for suffix, body in ((".json", json.dumps(page, indent=2, sort_keys=True) + "\n"), (".md", markdown(page) + "\n")):
        tmp = folder / f"{page['week']}{suffix}.tmp"
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, folder / f"{page['week']}{suffix}")
    return folder / f"{page['week']}.json"


def publish(page: Mapping[str, Any]) -> Json:
    """Pushes the page beside the receipts, under their lock and secret gate."""
    import receipts  # noqa: PLC0415 (bin/lib)

    token = hand.setting("RECEIPTS_GITHUB_TOKEN") or hand.setting("DIARY_GITHUB_TOKEN") or hand.setting("GITHUB_TOKEN")
    if not token:
        raise hand.HandError("no token: set RECEIPTS_GITHUB_TOKEN (or GITHUB_TOKEN) in secrets/teeny.env")
    branch = receipts._branch()
    with hand.lock("receipts", timeout=60):
        repo = receipts._checkout(token)
        folder = repo / AUDIT_DIR
        folder.mkdir(exist_ok=True)
        (folder / f"{page['week']}.json").write_text(json.dumps(page, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (folder / f"{page['week']}.md").write_text(markdown(page) + "\n", encoding="utf-8")
        receipts._git(repo, "add", "--", AUDIT_DIR)
        if not receipts._git(repo, "diff", "--cached", "--name-only").strip():
            return {"pushed": 0}
        try:
            receipts.check_secrets(receipts._git(repo, "diff", "--cached", "--unified=0"))
        except hand.Refused as why:
            receipts._git(repo, "reset", "-q", "--hard", f"origin/{branch}")
            raise hand.Refused("the audit carries a secret, nothing committed", why=why.reason) from None
        receipts._git(repo, "commit", "-q", "-m", f"audit: {page['week']}")
        try:
            receipts._git(repo, "push", "-q", "origin", f"HEAD:{branch}", token=token)
        except hand.HandError:
            receipts._git(repo, "reset", "-q", "--hard", f"origin/{branch}")
            raise
    return {"pushed": 1}


def record_boot(commit: str | None = None, at: float | None = None) -> Json:
    """One line in state/boots.jsonl: when the spine booted and on which
    commit, and whether it was a last-good fallback."""
    if commit is None:
        got = subprocess.run(["git", "-C", str(root()), "rev-parse", "HEAD"], capture_output=True, text=True)
        commit = got.stdout.strip() if got.returncode == 0 else "unknown"
    row = {"at": iso(hand.now() if at is None else at), "commit": commit,
           "fallback": (state() / "spine-fallback.json").exists()}
    path = state() / "boots.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bin/audit", description="The weekly audit, from frozen code.")
    parser.add_argument("verb", nargs="?", choices=("week", "boot"), default="week")
    parser.add_argument("--week", help="YYYY-Www; default: last week")
    parser.add_argument("--json", action="store_true", help="print the page only; write and publish nothing")
    args = parser.parse_args(argv)
    try:
        if args.verb == "boot":
            print(json.dumps({"ok": True, "boot": record_boot()}))
            return 0
        page = build(args.week)
        if args.json:
            print(json.dumps(page, sort_keys=True))
            return 0
        path = write_local(page)
        out: Json = {"ok": True, "week": page["week"], "operator_actions": page["operator"]["total"],
                     "page": str(path.relative_to(root())) if path.is_relative_to(root()) else str(path),
                     "dry_run": hand.dry_run(), "publish": hand.flag("AUDIT_PUBLISH", False), "pushed": 0}
        if out["publish"] and not out["dry_run"]:
            out.update(publish(page))
        print(json.dumps(out))
        return 0
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    except hand.HandError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
