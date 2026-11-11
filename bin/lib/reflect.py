"""Teeny's end of day (docs/architecture.md, 4a, "Reflection").

    python3 bin/lib/reflect.py

MEMORY.md is the page that rides in every prompt Teeny speaks from: the
persistence that survives a compaction, a restart and recall missing. Once
a night this rewrites it, from inside the day:

1. Gathers what happened since the last reflection: the notes Teeny chose to
   keep (`remember`, in `mind_notes`), what it thought and did each beat
   (state/tick/runs.jsonl), the follow-ups still waiting, and the current
   page.
2. Asks the `teeny` agent on its own thread (`teeny:mind`, so the day is
   still in context) for the whole new page and a diary entry, as JSON.
3. Keeps the old page in state/reflect/MEMORY-<stamp>.md, writes the new one
   (titled, at most prompts.MEMORY_MAX_CHARS), writes the diary to
   state/diary/<date>.md (the day it looks back on: before 06:00 that is
   yesterday) and teaches it to the core's memory, then rebuilds
   the prompts so the next beat reads the new page (the agents set
   `refresh_prompt_on_resume`).

An answer that is not JSON, or a page that is empty or too long, changes
nothing; neither does a failed turn. The next night tries again with
everything since the last reflection that worked.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

CODE = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("TEENY_ROOT") or CODE)
BRAIN = Path(os.environ.get("OPENHUMAN_WORKSPACE") or ROOT / "brain")
sys.path.insert(0, str(CODE / "hands" / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import prompts  # noqa: E402
from teeny_hands import hand, stimuli  # noqa: E402

#: The most of the day's beats the message carries (newest kept).
MAX_BEATS = 120
DEFAULT_TZ = "Asia/Kolkata"

Json = dict[str, Any]

SHAPE = """## your answer

Answer with one JSON object and nothing else:

{"memory": "<the whole new MEMORY.md, markdown>", "diary": "<tonight's diary entry>"}

`memory` replaces the page entirely, so carry over everything that still matters and drop what no longer does. Keep its sections (people, promises and open threads, what i am in the middle of, what i have decided). Write about each person in a line or two you could act on tomorrow: who they are, where you know them from (x, moltbook, github, mail), what you owe them. Under %d characters; shorter is better.

`diary` is a few sentences in your own voice about the day: what happened, what you learned, how you feel about it.""" % prompts.MEMORY_MAX_CHARS


def _state(*parts: str) -> Path:
    path = hand.state_dir().joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clock() -> dt.datetime:
    return dt.datetime.fromtimestamp(hand.now(), ZoneInfo(os.environ.get("CALENDAR_TZ") or DEFAULT_TZ))


#: How far after midnight a reflection still looks back on the day before.
#: The pinned job runs at 02:00, after the quiet hours start.
DAY_ENDS_AFTER_MIDNIGHT = dt.timedelta(hours=6)


def _day_of(now: dt.datetime) -> dt.date:
    """The day an entry written at `now` is about."""
    return (now - DAY_ENDS_AFTER_MIDNIGHT).date()


def _since() -> float:
    try:
        return float((_state("reflect") / "last.at").read_text().strip())
    except (OSError, ValueError):
        return hand.now() - 86400


def _beats(since: float) -> list[Json]:
    path = hand.state_dir() / "tick" / "runs.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            beat = json.loads(line)
            at = dt.datetime.strptime(beat["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
        except (ValueError, KeyError, TypeError):
            continue
        if at > since and beat.get("ok") and not beat.get("idle"):
            out.append({"at": beat["at"], "thought": beat.get("thought", ""),
                        "did": [f"{r.get('do')}: {r.get('status')}" for r in beat.get("results", [])]})
    return out[-MAX_BEATS:]


def render(since: float) -> str:
    # Strictly after: a note from the second the last reflection ran was
    # already in it.
    notes = [n for n in stimuli.notes(since=since)["notes"] if n["at"] > since]
    waiting = stimuli.follow_ups()["follow_ups"]
    beats = _beats(since)
    page = prompts._read(BRAIN / "MEMORY.md") or "(empty)"
    lines = [f"# end of the day ({_clock():%A %d %B, %H:%M} your time)", "",
             "Time to look back and rewrite MEMORY.md, the page you carry into every conversation.", ""]
    lines += ["## what you chose to remember today", ""]
    lines += [f"- {'about ' + n['who'] + ': ' if n.get('who') else ''}{n['note']}" for n in notes] or ["- nothing"]
    lines += ["", "## what you thought and did, beat by beat", ""]
    for b in beats:
        did = f" ({'; '.join(b['did'])})" if b["did"] else ""
        lines.append(f"- {b['at']}: {b['thought'] or '-'}{did}")
    if not beats:
        lines.append("- a quiet day")
    lines += ["", "## follow-ups still waiting", ""]
    for f in waiting:
        due = dt.datetime.fromtimestamp(f["due_at"], _clock().tzinfo)
        lines.append(f"- {due:%a %d %b %H:%M}: {f['about']}")
    if not waiting:
        lines.append("- none")
    lines += ["", "## MEMORY.md as it stands", "", page, "", SHAPE]
    return "\n".join(lines)


def parse(text: str) -> tuple[str, str]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        raw = raw.rsplit("```", 1)[0]
    start = raw.find("{")
    if start < 0:
        raise ValueError("no JSON object in the answer")
    value, _ = json.JSONDecoder().raw_decode(raw[start:])
    if not isinstance(value, dict):
        raise ValueError("the answer is not an object")
    memory, diary = value.get("memory"), value.get("diary")
    if not isinstance(memory, str) or not memory.strip():
        raise ValueError("no memory page in the answer")
    memory = memory.strip()
    if not memory.startswith("# MEMORY.md"):
        memory = "# MEMORY.md\n\n" + memory
    if len(memory) > prompts.MEMORY_MAX_CHARS:
        raise ValueError(f"the page is {len(memory)} characters; the most is {prompts.MEMORY_MAX_CHARS}")
    return memory + "\n", diary.strip() if isinstance(diary, str) else ""


def ask(message: str) -> str:
    thread = os.environ.get("TEENY_MIND_THREAD") or "teeny:mind"
    proc = subprocess.run([str(CODE / "bin" / "ask"), "--agent", "teeny", "--thread", thread],
                          input=message, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-500:] or "the turn failed")
    return proc.stdout


def _learn(text: str) -> bool:
    oh = os.environ.get("TEENY_OH") or str(CODE / "bin" / "oh")
    proc = subprocess.run([oh, "memory_learn", json.dumps({"text": text, "kind": "other", "confidence": 0.8})],
                          capture_output=True, text=True, timeout=60)
    return proc.returncode == 0


def reflect() -> Json:
    since = _since()
    try:
        said = ask(render(since))
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"the turn failed: {exc}"}
    try:
        memory, diary = parse(said)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "answer": said[:500]}
    now = _clock()
    page = BRAIN / "MEMORY.md"
    if page.is_file():
        (_state("reflect") / f"MEMORY-{now:%Y%m%dT%H%M}.md").write_text(page.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = page.with_suffix(".md.tmp")
    tmp.write_text(memory, encoding="utf-8")
    os.replace(tmp, page)
    learned = None
    if diary:
        day = _day_of(now)
        (_state("diary") / f"{day:%Y-%m-%d}.md").write_text(f"# {day:%A %d %B %Y}\n\n{diary}\n", encoding="utf-8")
        learned = _learn(f"diary, {day:%Y-%m-%d}: {diary}")
    rebuilt = [p.name for p in prompts.build(BRAIN)]
    (_state("reflect") / "last.at").write_text(f"{hand.now():.0f}\n")
    return {"ok": True, "memory_chars": len(memory), "diary": bool(diary), "learned": learned, "prompts": rebuilt}


if __name__ == "__main__":
    try:
        result = reflect()
    except (hand.HandError, OSError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("ok") else 1)
