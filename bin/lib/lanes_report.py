"""What my model lanes spent, from the teeny-lanes plugin's ledger.

    python3 bin/lib/lanes_report.py BRAIN/lanes.toml STATE/lanes [DAYS]

Each model call the Gateway makes is one line of state/lanes/usage-<UTC
day>.jsonl (core/plugins/teeny-lanes/lanes.ts, Ledger): the lane, the
session key, the model and its tokens. This reads the last DAYS days (today
included) and prints one JSON object: per lane, its tokens, calls and daily
cap with the days it went over; and the threads (sessions) that spent the
most, each against its lane's per-thread cap. Torn lines are skipped. It
reads and never writes; nothing here enforces a cap.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

TOP_THREADS = 10


def caps(lanes_toml: Path) -> dict[str, dict[str, int | None]]:
    """Each lane's daily caps from brain/lanes.toml; none when it is missing or broken."""
    try:
        lanes = tomllib.loads(lanes_toml.read_text(encoding="utf-8")).get("lane", [])
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    out: dict[str, dict[str, int | None]] = {}
    for lane in lanes if isinstance(lanes, list) else []:
        if isinstance(lane, dict) and isinstance(lane.get("id"), str):
            out[lane["id"]] = {k: v if isinstance(v, int) and not isinstance(v, bool) else None
                               for k, v in (("daily", lane.get("daily_tokens")), ("thread_daily", lane.get("thread_daily_tokens")))}
    return out


def rows(ledger: Path, days: list[str]) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for day in days:
        try:
            text = (ledger / f"usage-{day}.jsonl").read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and isinstance(row.get("lane"), str) and isinstance(row.get("session"), str) \
                    and isinstance(row.get("total"), int) and not isinstance(row.get("total"), bool):
                out.append((day, row))
    return out


def report(lanes_toml: Path, ledger: Path, days: int, today: dt.date | None = None) -> dict[str, Any]:
    if days < 1:
        raise ValueError("DAYS must be at least 1")
    today = today or dt.datetime.now(dt.timezone.utc).date()
    span = [(today - dt.timedelta(days=n)).isoformat() for n in range(days - 1, -1, -1)]
    limits = caps(lanes_toml)
    lane_day: dict[tuple[str, str], int] = {}
    thread_day: dict[tuple[str, str], int] = {}
    lanes: dict[str, dict[str, Any]] = {}
    threads: dict[str, dict[str, Any]] = {}
    for day, r in rows(ledger, span):
        lane, session, total = r["lane"], r["session"], r["total"]
        l = lanes.setdefault(lane, {"tokens": 0, "calls": 0, "models": {}})
        l["tokens"] += total
        l["calls"] += 1
        model = r.get("model") if isinstance(r.get("model"), str) else "unknown"
        l["models"][model] = l["models"].get(model, 0) + total
        t = threads.setdefault(session, {"session": session, "lane": lane, "tokens": 0, "calls": 0})
        t["tokens"] += total
        t["calls"] += 1
        lane_day[(lane, day)] = lane_day.get((lane, day), 0) + total
        thread_day[(session, day)] = thread_day.get((session, day), 0) + total
    for lane, l in lanes.items():
        cap = limits.get(lane, {}).get("daily")
        l["daily_cap"] = cap
        l["days_over"] = sorted(d for (ln, d), n in lane_day.items() if ln == lane and cap and n > cap)
    top = sorted(threads.values(), key=lambda t: (-t["tokens"], t["session"]))[:TOP_THREADS]
    for t in top:
        cap = limits.get(t["lane"], {}).get("thread_daily")
        t["daily_cap"] = cap
        t["peak_day"] = max((n for (s, _), n in thread_day.items() if s == t["session"]), default=0)
        t["days_over"] = sorted(d for (s, d), n in thread_day.items() if s == t["session"] and cap and n > cap)
    return {"days": span, "enforced": False, "lanes": dict(sorted(lanes.items())), "top_threads": top}


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print("usage: lanes_report.py LANES_TOML LEDGER_DIR [DAYS]", file=sys.stderr)
        return 2
    try:
        days = int(argv[3]) if len(argv) == 4 else 1
        out = report(Path(argv[1]), Path(argv[2]), days)
    except ValueError as e:
        print(f"lanes: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
