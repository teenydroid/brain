"""The scoreboard and the lab, put in front of Teeny (docs/architecture.md,
"The scoreboard and the lab").

  block()        the beat's "scoreboard" block (bin/lib/tick.py render):
                 the three lines of `hands/stats board`, the open
                 experiments, the latest lessons; at most BLOCK_CHARS
  sections()     what both dreams get (bin/lib/dream.py): the board, the
                 open experiments as scored now, the lab's lessons, and
                 "true right now" (balances and followers), each a
                 `- ` list with a hard character cap, so reflect._fit can
                 cut it like the dream's other lists

Everything here is read in code from the ledgers and the hands; a model
never computes a number it is shown. A part that cannot be read is left out
(or says so), so a beat or a dream never fails over its scoreboard.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

CODE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE / "hands" / "lib"))

from teeny_hands import hand  # noqa: E402

Json = dict[str, Any]

#: The beat's block, at most.
BLOCK_CHARS = 1500
#: Each dream section, at most.
SECTION_CHARS = 2500
#: One item of a section or the block, at most.
ITEM_CHARS = 300
#: Lessons in the beat's block, and in a dream.
BLOCK_LESSONS = 3
DREAM_LESSONS = 10
#: How long a hand read for "true right now" may take.
HAND_TIMEOUT = 60


def _clip(text: Any, most: int = ITEM_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= most else text[: most - 3].rstrip() + "..."


def _safe(read: Callable[[], Any], fallback: Any) -> Any:
    try:
        return read()
    except Exception:  # noqa: BLE001 - the scoreboard never stops a beat or a dream
        return fallback


def _board(days: int = 7) -> Json:
    from teeny_hands.stats import board

    return board.board(days)


def _open(score: bool = False) -> list[Json]:
    from teeny_hands.lab import ledger

    if score:
        ledger.score()
    return ledger.listing("open", ledger.MAX_OPEN)["experiments"]


def _lessons(n: int) -> list[Json]:
    from teeny_hands.lab import ledger

    return ledger.lessons(n)["lessons"]


def experiment_line(e: Json) -> str:
    vs = ""
    if e.get("avg") is not None:
        vs = f", avg {e['avg']} vs {e['baseline_avg'] if e.get('baseline_avg') is not None else '-'} for the rest"
    due = ", due to conclude" if e.get("due") else ""
    return _clip(f"#{e['id']} {e['surface']} `{e['format']}` ({e['metric']}): {e['n']} of {e['posts']} posts measured"
                 f"{vs}, deadline {e['deadline']}{due}. Guess: {e['hypothesis']}")


def lesson_line(l: Json) -> str:
    return _clip(f"#{l['id']} {l['surface']} `{l['format']}` {l['verdict']} ({l['concluded']}): {l['lesson']}")


def block() -> list[str]:
    """The beat's scoreboard block, as lines; [] when nothing can be read."""
    board = _safe(_board, None)
    experiments = _safe(_open, None)
    lessons = _safe(lambda: _lessons(BLOCK_LESSONS), None)
    if board is None and experiments is None and lessons is None:
        return []
    lines = ["## scoreboard", ""]
    lines += [_clip(line) for line in (board or {}).get("lines", [])]
    if experiments is not None:
        lines.append("Open experiments: " + ("none. Propose one with `propose_experiment`." if not experiments else ""))
        lines += [f"- {experiment_line(e)}" for e in experiments]
    if lessons:
        lines.append("Latest lessons:")
        lines += [f"- {lesson_line(l)}" for l in lessons]
    text = "\n".join(lines)
    if len(text) > BLOCK_CHARS:
        text = text[: BLOCK_CHARS - 3].rstrip() + "..."
    return [*text.split("\n"), ""]


def _section(title: str, intro: str, items: list[str]) -> str:
    """A dream section, cut to SECTION_CHARS by dropping its last items."""
    head = [f"## {title}", "", intro, ""] if intro else [f"## {title}", ""]
    kept = [f"- {_clip(item)}" for item in items] or ["- none"]
    while len("\n".join(head + kept)) > SECTION_CHARS and len(kept) > 1:
        kept.pop()
    return "\n".join(head + kept)


def _run_hand(*argv: str) -> Json:
    hands = Path(os.environ.get("TEENY_HANDS") or CODE / "hands")
    proc = subprocess.run([str(hands / argv[0]), *argv[1:]], capture_output=True, text=True, timeout=HAND_TIMEOUT,
                          stdin=subprocess.DEVNULL)
    got = json.loads(proc.stdout or "{}")
    if proc.returncode != 0 or not isinstance(got, dict):
        raise RuntimeError(str(got.get("error") or got.get("refused") or "failed") if isinstance(got, dict) else "failed")
    return got


def _display(value: Any) -> str | None:
    return value.get("display") if isinstance(value, dict) and isinstance(value.get("display"), str) else None


def balances() -> list[str]:
    """The wallet and the x402 rail as they read now; a read that fails says so."""
    out = []
    try:
        got = _run_hand("wallet", "balance")
        sol = [f"{name.upper()} {_display(got.get(name))}" for name in ("sol", "usdc", "tiny") if _display(got.get(name))]
        out.append("wallet on Solana: " + (", ".join(sol) or "nothing readable"))
        for net, entry in sorted(((got.get("evm") or {}).get("networks") or {}).items()):
            held = [f"{k.upper()} {_display(v)}" for k, v in entry.items() if _display(v)]
            if held:
                out.append(f"wallet on {net}: {', '.join(held)}")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        out.append(f"wallet: could not be read just now ({_clip(exc, 120)}); say nothing about its balance")
    try:
        got = _run_hand("x402", "fund", "status")
        if got.get("base_usdc") is not None:
            out.append(f"x402 rail (USDC on Base, what paid search spends): {got['base_usdc']}")
        else:
            out.append("x402 rail: could not be read just now; say nothing about it")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        out.append("x402 rail: could not be read just now; say nothing about it")
    return out


def sections(score: bool = True) -> list[str]:
    """The dream's sections. `score` first records the open experiments'
    numbers (code), so the night dream concludes from today's."""
    board = _safe(_board, None)
    experiments = _safe(lambda: _open(score), [])
    lessons = _safe(lambda: _lessons(DREAM_LESSONS), [])
    out = []
    if board is not None:
        items = list(board.get("lines", []))
        for surface in ("x", "moltbook"):
            for g in board.get(surface, {}).get("formats", [])[:6]:
                items.append(f"{surface} format {g['format']}: {g['n']} measured, avg result {g['avg']}")
        out.append(_section("the scoreboard", "Counted in code from your ledgers (hands/stats board). The last 7 days.",
                            items))
    out.append(_section("your experiments", "Open, scored in code just now. `due` ones are ready for a verdict.",
                        [experiment_line(e) for e in experiments]))
    out.append(_section("what the lab has learned", "Concluded experiments, newest first.",
                        [lesson_line(l) for l in lessons]))
    people = [f"{a['metric']}: {a['value']} (as of {a['date']})" for a in (board or {}).get("audience", [])]
    out.append(_section("true right now", "Read just now. Where your memory page says otherwise, this is right:"
                        " fix the page, never copy an old number into it.", _safe(balances, []) + people))
    return out
