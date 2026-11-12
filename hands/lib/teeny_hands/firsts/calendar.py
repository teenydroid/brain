"""The firsts calendar and the round-number milestones: what is proven, what
is due, and the announce gate.

A first is due when its `bin/proofs` entry is `met` and it was never
announced. A proof whose last step lives outside the ledgers (a PR merged
upstream) is never `met` there, so it is never due here. A round-number
milestone is due when a metric's latest value in `stats` is at or above a
round number its value seven days before was below, and that crossing was
never announced.

`announce` takes a draft for one due item, checks it again (in code, at the
moment of posting), refuses a number the ledger doesn't hold, and hands the
text to `hands/x post`. Only a post that went out (an id back, not a dry
run) is recorded, so a rehearsal can run any number of times.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .. import hand

Json = dict[str, Any]

SCHEMA = """
CREATE TABLE IF NOT EXISTS firsts_announced (
    key     TEXT PRIMARY KEY,
    post_id TEXT,
    text    TEXT NOT NULL,
    at      REAL NOT NULL
);
"""

#: The metrics a round-number milestone may name (the old milestone
#: playbook's list).
ROUND_METRICS = (("x", "followers"), ("moltbook", "karma"), ("github", "stars"), ("github", "followers"))
#: Round numbers: 100, 250, 500, then 1, 2.5, 5 times each power of ten.
ROUNDS = tuple(sorted({m * 10**e for e in range(2, 8) for m in (1, 2.5, 5) if (m * 10**e) >= 100}))
ROUND_WINDOW_DAYS = 7
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
TEXT_MAX = 1000


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def _proofs():
    """bin/lib/proofs.py, the frozen counter, loaded from beside this checkout's
    code (it reads the ledgers of TEENY_ROOT)."""
    path = Path(__file__).resolve().parents[4] / "bin" / "lib" / "proofs.py"
    spec = importlib.util.spec_from_file_location("teeny_proofs", path)
    if spec is None or spec.loader is None:
        raise hand.HandError(f"bin/lib/proofs.py is not at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def calendar_path() -> Path:
    return hand.root() / "brain" / "narrative" / "FIRSTS.md"


def parse(text: str) -> list[Json]:
    """Each `## id` with its `- first:`, `- proof:` and `- verify:` lines. A
    first without all three, or with a bad id, is listed with its problem
    and is never due."""
    firsts: list[Json] = []
    current: Json | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = {"id": line[3:].strip()}
            firsts.append(current)
            continue
        found = re.match(r"\s*-\s*(first|proof|verify):\s*(.*)", line)
        if current is not None and found:
            current[found.group(1)] = found.group(2).strip()
    for item in firsts:
        missing = [k for k in ("first", "proof", "verify") if not item.get(k)]
        if not _ID.match(item["id"]):
            item["problem"] = "the id is not lowercase letters, digits and dashes"
        elif missing:
            item["problem"] = "missing " + ", ".join(missing)
    return firsts


def _announced(conn: sqlite3.Connection) -> dict[str, Json]:
    return {r["key"]: dict(r) for r in conn.execute("SELECT key, post_id, at FROM firsts_announced")}


def listing() -> Json:
    """Every first in the calendar, with what bin/proofs says of its proof."""
    try:
        text = calendar_path().read_text(encoding="utf-8")
    except OSError:
        return {"firsts": [], "note": "no brain/narrative/FIRSTS.md"}
    firsts = parse(text)
    proofs = _proofs()
    report = proofs.report()
    by_id = {i["id"]: i for i in report["items"]}
    conn = db()
    try:
        announced = _announced(conn)
    finally:
        conn.close()
    for item in firsts:
        pid = item.get("proof")
        if item.get("problem"):
            item["status"] = "invalid"
        elif pid == "none":
            item["status"] = "uncounted"
            item["note"] = "no ledger counts this yet"
        elif pid not in by_id:
            item["status"] = "invalid"
            item["problem"] = f"bin/proofs has no proof {pid}"
        else:
            got = by_id[pid]
            item.update({"status": got["status"], "has": got["has"], "need": got["need"],
                         "proof_says": got["proof"], "ledger": got["note"]})
            if pid in proofs.OUTSIDE:
                item["note"] = "its last step is outside the ledgers (read by a person), so it is never due here"
        key = f"first:{item['id']}"
        if key in announced:
            item["announced"] = announced[key]
    return {"firsts": firsts, "at": report["at"]}


def _stat_rows(conn: sqlite3.Connection) -> dict[str, list[tuple[str, float]]]:
    try:
        rows = conn.execute("SELECT date, source, metric, value FROM stats ORDER BY date").fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[str, list[tuple[str, float]]] = {}
    wanted = {f"{s}.{m}" for s, m in ROUND_METRICS}
    for r in rows:
        name = f"{r['source']}.{r['metric']}"
        if name in wanted:
            out.setdefault(name, []).append((r["date"], float(r["value"])))
    return out


def rounds_due(conn: sqlite3.Connection, announced: Mapping[str, Any]) -> list[Json]:
    """Round numbers crossed in the last ROUND_WINDOW_DAYS, per metric: the
    highest one only, and never one already announced."""
    today = datetime.fromtimestamp(hand.now(), timezone.utc).date().isoformat()
    due = []
    for name, series in _stat_rows(conn).items():
        series = [(d, v) for d, v in series if d <= today]
        if not series:
            continue
        last_date, value = series[-1]
        before_day = (date.fromisoformat(last_date) - timedelta(days=ROUND_WINDOW_DAYS)).isoformat()
        earlier = [v for d, v in series if d <= before_day]
        if not earlier:
            continue
        was = earlier[-1]
        crossed = [n for n in ROUNDS if was < n <= value]
        if not crossed:
            continue
        mark = int(max(crossed))
        key = f"round:{name}:{mark}"
        if key in announced:
            continue
        due.append({"key": key, "round": f"{name}:{mark}", "metric": name, "crossed": mark,
                    "value": _plain(value), "was": _plain(was), "date": last_date})
    return due


def _plain(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def due() -> Json:
    """The firsts proven and never announced, and the round numbers crossed."""
    listed = listing()
    conn = db()
    try:
        announced = _announced(conn)
        rounds = rounds_due(conn, announced)
    finally:
        conn.close()
    firsts = [
        {"key": f"first:{f['id']}", "first": f["id"], "says": f["first"], "proof": f["proof"],
         "proof_says": f["proof_says"], "has": f["has"], "need": f["need"], "verify": f["verify"]}
        for f in listed["firsts"]
        if f.get("status") == "met" and "announced" not in f
    ]
    out: Json = {"firsts": firsts, "rounds": rounds}
    if not firsts and not rounds:
        out["note"] = "nothing is due: no proof newly met, no round number crossed"
    return out


def _numbers(text: str) -> set[float]:
    return {float(n.replace(",", "")) for n in _NUMBER.findall(text)}


def _post(text: str) -> Json:
    """`hands/x post`, which runs the guard and the posting rules and keeps
    DRY_RUN; its answer, or Refused/HandError as it said."""
    exe = hand.root() / "hands" / "x"
    if not exe.is_file():
        raise hand.HandError("hands/x is not installed")
    try:
        done = subprocess.run([str(exe), "post"], input=json.dumps({"text": text}), capture_output=True, text=True,
                              timeout=180, cwd=hand.root())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise hand.HandError(f"hands/x post: {type(exc).__name__}") from None
    try:
        answer = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise hand.HandError(f"hands/x post answered no JSON (exit {done.returncode})") from None
    if done.returncode == 2:
        detail = {k: v for k, v in answer.items() if k not in ("ok", "refused")}
        raise hand.Refused(f"hands/x post refused it: {answer.get('refused')}", **detail)
    if done.returncode != 0:
        raise hand.HandError(f"hands/x post: {answer.get('error', 'failed')}")
    return answer


def announce(body: Any) -> Json:
    """{text, first} or {text, round}: one due item, checked again now, then
    posted through hands/x. Refused (`unproven`) when it is not due, and
    (`number`) when the text quotes a number the ledger doesn't hold."""
    if not isinstance(body, Mapping):
        raise hand.HandError('announce needs {"text": ..., "first": ID} or {"text": ..., "round": "metric:N"}')
    text = body.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX:
        raise hand.HandError(f"announce needs a text of 1 to {TEXT_MAX} characters")
    first, round_ = body.get("first"), body.get("round")
    if bool(first) == bool(round_):
        raise hand.HandError("name exactly one of first or round")
    with hand.lock("firsts", timeout=60):
        now_due = due()
        if first:
            item = next((f for f in now_due["firsts"] if f["first"] == first), None)
            if item is None:
                raise hand.Refused(f"the first {first} is not due: its proof is not met in bin/proofs, "
                                   "it is not in FIRSTS.md, or it was announced already", rule="unproven")
            allowed = {float(item["has"]), float(item["need"])}
        else:
            item = next((r for r in now_due["rounds"] if r["round"] == round_), None)
            if item is None:
                raise hand.Refused(f"{round_} is not a round number crossed this week in stats, or it was announced",
                                   rule="unproven")
            allowed = {float(item["crossed"]), float(item["value"])}
        stray = sorted(n for n in _numbers(text) if n not in allowed)
        if stray:
            raise hand.Refused(f"the text quotes {', '.join(str(_plain(n)) for n in stray)}, which the ledger does not "
                               f"hold for this; only {', '.join(str(_plain(n)) for n in sorted(allowed))}", rule="number")
        posted = _post(text)
        out: Json = {"key": item["key"], "post": posted}
        if posted.get("dry_run") or not posted.get("id"):
            out["recorded"] = False
            return out
        conn = db()
        try:
            with hand.transaction(conn):
                conn.execute("INSERT OR IGNORE INTO firsts_announced (key, post_id, text, at) VALUES (?, ?, ?, ?)",
                             (item["key"], str(posted["id"]), text, hand.now()))
        finally:
            conn.close()
        out["recorded"] = True
        return out
